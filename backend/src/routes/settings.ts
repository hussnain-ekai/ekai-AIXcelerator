import type { FastifyInstance, FastifyRequest } from 'fastify';

import { config } from '../config.js';
import { postgresService } from '../services/postgresService.js';

const AI_SERVICE_URL = config.AI_SERVICE_URL;

interface LlmConfigBody {
  provider: string;
  model?: string;
  cortex_model?: string;
  // Vertex AI — user-provided credentials
  vertex_credentials_json?: string;
  vertex_project?: string;
  vertex_location?: string;
  vertex_model?: string;
  // Anthropic
  anthropic_api_key?: string;
  anthropic_model?: string;
  // OpenAI
  openai_api_key?: string;
  openai_model?: string;
  // Azure OpenAI
  azure_openai_api_key?: string;
  azure_openai_endpoint?: string;
  azure_openai_deployment?: string;
  azure_openai_api_version?: string;
}

interface LlmTestBody extends LlmConfigBody {}

/**
 * Proxy a request to the AI service and return the JSON response.
 */
async function aiServiceRequest(
  method: string,
  path: string,
  body?: unknown,
): Promise<unknown> {
  const url = `${AI_SERVICE_URL}${path}`;
  const options: RequestInit = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body !== undefined) {
    options.body = JSON.stringify(body);
  }
  const response = await fetch(url, options);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`AI service ${method} ${path} failed (${response.status}): ${text}`);
  }
  return response.json();
}

export async function settingsRoutes(app: FastifyInstance): Promise<void> {
  /**
   * GET /settings/llm
   *
   * Returns saved config from PostgreSQL + active status from AI service.
   * Also returns default values for Azure from env.
   */
  app.get(
    '/llm',
    async (request: FastifyRequest, reply) => {
      const currentUser = (request.headers['sf-context-current-user'] as string) ?? 'system';

      // Read saved config from PostgreSQL workspaces table
      let saved: unknown = null;
      try {
        const result = await postgresService.query(
          `SELECT settings->'llm_config' AS llm_config
           FROM workspaces
           WHERE snowflake_user = $1
           ORDER BY updated_at DESC
           LIMIT 1`,
          [currentUser],
          currentUser,
        );
        if (result.rows.length > 0 && result.rows[0].llm_config) {
          saved = result.rows[0].llm_config;
        }
      } catch (err) {
        request.log.warn({ err }, 'Failed to read saved LLM config from PostgreSQL');
      }

      // Get active status from AI service
      let active: unknown = null;
      try {
        active = await aiServiceRequest('GET', '/config/llm');
      } catch (err) {
        request.log.warn({ err }, 'Failed to get active LLM status from AI service');
      }

      // Default values from env for pre-filling forms
      const defaults = {
        azure_openai_endpoint: process.env['AZURE_OPENAI_ENDPOINT'] ?? '',
        azure_openai_deployment: process.env['AZURE_OPENAI_DEPLOYMENT'] ?? '',
        azure_openai_api_version: process.env['AZURE_OPENAI_API_VERSION'] ?? '2024-12-01-preview',
        azure_openai_key_configured: (process.env['AZURE_OPENAI_API_KEY'] ?? '').length > 0,
      };

      return reply.send({ saved, active, defaults });
    },
  );

  /**
   * PUT /settings/llm
   *
   * Save LLM config to PostgreSQL and apply to AI service.
   */
  app.put(
    '/llm',
    async (
      request: FastifyRequest<{ Body: LlmConfigBody }>,
      reply,
    ) => {
      const currentUser = (request.headers['sf-context-current-user'] as string) ?? 'system';
      const body = request.body;

      // Save to PostgreSQL
      try {
        await postgresService.query(
          `UPDATE workspaces
           SET settings = jsonb_set(
             COALESCE(settings, '{}'::jsonb),
             '{llm_config}',
             $1::jsonb
           ),
           updated_at = NOW()
           WHERE snowflake_user = $2`,
          [JSON.stringify(body), currentUser],
          currentUser,
        );
      } catch (err) {
        request.log.error({ err }, 'Failed to save LLM config to PostgreSQL');
      }

      // Apply to AI service
      let aiResponse: unknown = null;
      try {
        aiResponse = await aiServiceRequest('POST', '/config/llm', body);
      } catch (err) {
        request.log.error({ err }, 'Failed to apply LLM config to AI service');
        return reply.status(502).send({
          error: 'ai_service_error',
          message: 'Failed to apply LLM config to AI service',
          details: { error: String(err) },
        });
      }

      return reply.send({ saved: true, active: aiResponse });
    },
  );

  /**
   * POST /settings/llm/test
   *
   * Proxy test request to AI service.
   */
  app.post(
    '/llm/test',
    async (
      request: FastifyRequest<{ Body: LlmTestBody }>,
      reply,
    ) => {
      try {
        const result = await aiServiceRequest('POST', '/config/llm/test', request.body);
        return reply.send(result);
      } catch (err) {
        request.log.error({ err }, 'LLM test proxy failed');
        return reply.status(502).send({
          error: 'ai_service_error',
          message: 'Failed to proxy test to AI service',
          details: { error: String(err) },
        });
      }
    },
  );
}

/**
 * Restore saved LLM config on startup.
 *
 * Queries PostgreSQL for any saved llm_config and POSTs it to the AI service.
 * Includes retry logic in case AI service isn't ready yet.
 * Fire-and-forget — does not block startup.
 */
export async function restoreSavedLlmConfig(logger: { info: (...args: unknown[]) => void; warn: (...args: unknown[]) => void; error: (...args: unknown[]) => void }): Promise<void> {
  // Wait a bit for AI service to be ready
  await new Promise(resolve => setTimeout(resolve, 2000));

  try {
    const result = await postgresService.pool.query(
      `SELECT settings->'llm_config' AS llm_config
       FROM workspaces
       WHERE settings->'llm_config' IS NOT NULL
       ORDER BY updated_at DESC
       LIMIT 1`,
    );

    if (result.rows.length > 0 && result.rows[0].llm_config) {
      const savedConfig = result.rows[0].llm_config;
      logger.info('Restoring saved LLM config: provider=%s', savedConfig.provider);

      // Retry up to 3 times in case AI service isn't ready
      let lastError: unknown = null;
      for (let i = 0; i < 3; i++) {
        try {
          await aiServiceRequest('POST', '/config/llm', savedConfig);
          logger.info('Saved LLM config restored successfully');
          return;
        } catch (err) {
          lastError = err;
          logger.warn('LLM config restore attempt %d failed, retrying...', i + 1);
          await new Promise(resolve => setTimeout(resolve, 1000));
        }
      }

      logger.error({ err: lastError }, 'Failed to restore saved LLM config after 3 attempts');
    } else {
      logger.info('No saved LLM config found to restore');
    }
  } catch (err) {
    logger.error({ err }, 'Failed to restore saved LLM config (database error)');
  }
}
