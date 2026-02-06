import type { FastifyInstance, FastifyRequest } from 'fastify';

import { config } from '../config.js';
import { redisService } from '../services/redisService.js';
import { postgresService } from '../services/postgresService.js';
import {
  sendMessageSchema,
  sessionIdParamSchema,
} from '../schemas/agent.js';

interface MessageHistoryRow {
  id: string;
  session_id: string;
  role: string;
  content: string;
  tool_calls: unknown[] | null;
  created_at: string;
}

export async function agentRoutes(app: FastifyInstance): Promise<void> {
  /**
   * POST /agent/message
   * Send a message to the AI service. Proxies the request to the FastAPI AI service.
   */
  app.post(
    '/message',
    async (request: FastifyRequest, reply) => {
      const parseResult = sendMessageSchema.safeParse(request.body);
      if (!parseResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid request body',
          details: parseResult.error.flatten().fieldErrors,
        });
      }

      const { session_id, message, data_product_id, attachments } = parseResult.data;
      const { snowflakeUser } = request.user;

      request.log.info(
        { session_id, data_product_id, user: snowflakeUser },
        'Sending message to AI service',
      );

      try {
        const aiResponse = await fetch(
          `${config.AI_SERVICE_URL}/agent/message`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-Snowflake-User': snowflakeUser,
            },
            body: JSON.stringify({
              session_id,
              message,
              data_product_id: data_product_id ?? '',
              attachments,
              user: snowflakeUser,
            }),
          },
        );

        if (!aiResponse.ok) {
          const errorBody = (await aiResponse.json().catch(() => null)) as Record<string, unknown> | null;
          return reply.status(aiResponse.status).send({
            error: 'AI_SERVICE_ERROR',
            message:
              (errorBody?.message as string | undefined) ??
              'AI service returned an error',
            details: errorBody,
          });
        }

        const responseData = (await aiResponse.json()) as Record<string, unknown>;
        return reply.send(responseData);
      } catch (err: unknown) {
        request.log.error({ err }, 'Failed to reach AI service');
        return reply.status(502).send({
          error: 'AI_SERVICE_UNAVAILABLE',
          message: 'Unable to reach the AI service',
        });
      }
    },
  );

  /**
   * GET /agent/stream/:sessionId
   * SSE endpoint. Opens a connection to the AI service's stream endpoint
   * and pipes events to the client. Sends keepalive pings every 15 seconds.
   */
  app.get(
    '/stream/:sessionId',
    async (
      request: FastifyRequest<{ Params: { sessionId: string } }>,
      reply,
    ) => {
      const paramResult = sessionIdParamSchema.safeParse(request.params);
      if (!paramResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid session ID',
        });
      }

      const { sessionId } = paramResult.data;
      const { snowflakeUser } = request.user;

      request.log.info(
        { sessionId, user: snowflakeUser },
        'Opening SSE stream',
      );

      // Set SSE headers using raw response
      // Include CORS headers manually since reply.hijack() bypasses Fastify plugins
      const origin = request.headers.origin ?? '*';
      const raw = reply.raw;
      raw.writeHead(200, {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        Connection: 'keep-alive',
        'X-Accel-Buffering': 'no',
        'Access-Control-Allow-Origin': origin,
        'Access-Control-Allow-Credentials': 'true',
      });

      // Keepalive ping every 15 seconds
      const keepaliveInterval = setInterval(() => {
        raw.write(':ping\n\n');
      }, 15_000);

      let abortController: AbortController | undefined;

      // Clean up on client disconnect
      request.raw.on('close', () => {
        clearInterval(keepaliveInterval);
        if (abortController) {
          abortController.abort();
        }
      });

      try {
        abortController = new AbortController();

        const aiResponse = await fetch(
          `${config.AI_SERVICE_URL}/agent/stream/${sessionId}`,
          {
            headers: {
              Accept: 'text/event-stream',
              'X-Snowflake-User': snowflakeUser,
            },
            signal: abortController.signal,
          },
        );

        if (!aiResponse.ok || !aiResponse.body) {
          raw.write(
            `event: error\ndata: ${JSON.stringify({ error: 'AI_SERVICE_ERROR', message: 'Failed to open stream' })}\n\n`,
          );
          raw.end();
          clearInterval(keepaliveInterval);
          return;
        }

        const reader = aiResponse.body.getReader();
        const decoder = new TextDecoder();

        const pump = async (): Promise<void> => {
          try {
            let done = false;
            while (!done) {
              const result = await reader.read();
              done = result.done;
              if (result.value) {
                const text = decoder.decode(result.value, { stream: true });
                raw.write(text);
              }
            }
          } catch (err: unknown) {
            // AbortError is expected when client disconnects
            if (
              err instanceof Error &&
              err.name !== 'AbortError'
            ) {
              request.log.error({ err }, 'SSE stream error');
              raw.write(
                `event: error\ndata: ${JSON.stringify({ error: 'STREAM_ERROR', message: 'Stream interrupted' })}\n\n`,
              );
            }
          } finally {
            clearInterval(keepaliveInterval);
            raw.end();
          }
        };

        // Start pumping but don't await -- Fastify raw mode handles the lifecycle
        void pump();
      } catch (err: unknown) {
        request.log.error({ err }, 'Failed to connect to AI service stream');
        raw.write(
          `event: error\ndata: ${JSON.stringify({ error: 'AI_SERVICE_UNAVAILABLE', message: 'Unable to reach AI service' })}\n\n`,
        );
        clearInterval(keepaliveInterval);
        raw.end();
      }

      // Tell Fastify we've taken over the response
      return reply.hijack();
    },
  );

  /**
   * POST /agent/interrupt/:sessionId
   * Cancel the current agent execution for a session.
   */
  app.post(
    '/interrupt/:sessionId',
    async (
      request: FastifyRequest<{ Params: { sessionId: string } }>,
      reply,
    ) => {
      const paramResult = sessionIdParamSchema.safeParse(request.params);
      if (!paramResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid session ID',
        });
      }

      const { sessionId } = paramResult.data;
      const { snowflakeUser } = request.user;

      request.log.info(
        { sessionId, user: snowflakeUser },
        'Interrupting agent session',
      );

      try {
        const aiResponse = await fetch(
          `${config.AI_SERVICE_URL}/agent/interrupt/${sessionId}`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-Snowflake-User': snowflakeUser,
            },
          },
        );

        if (!aiResponse.ok) {
          const errorBody = (await aiResponse.json().catch(() => null)) as Record<string, unknown> | null;
          return reply.status(aiResponse.status).send({
            error: 'AI_SERVICE_ERROR',
            message:
              (errorBody?.message as string | undefined) ??
              'Failed to interrupt session',
          });
        }

        const responseData = (await aiResponse.json()) as Record<string, unknown>;
        return reply.send(responseData);
      } catch (err: unknown) {
        request.log.error({ err }, 'Failed to reach AI service for interrupt');
        return reply.status(502).send({
          error: 'AI_SERVICE_UNAVAILABLE',
          message: 'Unable to reach the AI service',
        });
      }
    },
  );

  /**
   * GET /agent/history/:sessionId
   * Get conversation history for a session. Tries Redis first, falls back to PostgreSQL.
   */
  app.get(
    '/history/:sessionId',
    async (
      request: FastifyRequest<{ Params: { sessionId: string } }>,
      reply,
    ) => {
      const paramResult = sessionIdParamSchema.safeParse(request.params);
      if (!paramResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid session ID',
        });
      }

      const { sessionId } = paramResult.data;
      const { snowflakeUser } = request.user;

      // Try Redis cache first
      try {
        const redisClient = await redisService.getClient();
        const cached = await redisClient.get(`agent:history:${sessionId}`);
        if (cached) {
          const parsed = JSON.parse(cached) as { messages?: unknown[]; data_product_id?: string } | unknown[];
          // Handle both formats: { messages: [...] } and direct array [...]
          const messages = Array.isArray(parsed) ? parsed : (parsed.messages ?? []);
          return reply.send({
            session_id: sessionId,
            messages,
            data_product_id: Array.isArray(parsed) ? undefined : parsed.data_product_id,
          });
        }
      } catch (err: unknown) {
        request.log.warn({ err }, 'Redis unavailable, falling back to PostgreSQL');
      }

      // Fall back to PostgreSQL — look up from the data product's state JSONB
      // which stores the session_id, then query the data product for its full state
      const result = await postgresService.query(
        `SELECT state
         FROM data_products
         WHERE state->>'session_id' = $1`,
        [sessionId],
        snowflakeUser,
      );

      const row = result.rows[0] as { state: Record<string, unknown> } | undefined;

      if (!row) {
        return reply.status(404).send({
          error: 'NOT_FOUND',
          message: 'Session not found',
        });
      }

      // The messages may be stored in the state JSONB
      const messages = (row.state.messages ?? []) as MessageHistoryRow[];

      return reply.send({ data: messages });
    },
  );
}
