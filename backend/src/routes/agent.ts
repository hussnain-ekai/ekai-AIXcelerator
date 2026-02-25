import type { FastifyInstance, FastifyRequest } from 'fastify';

import { config } from '../config.js';
import { postgresService } from '../services/postgresService.js';
import {
  sendMessageSchema,
  sessionIdParamSchema,
  retrySchema,
} from '../schemas/agent.js';

type SourceMode = 'structured' | 'document' | 'hybrid' | 'unknown';
type ExactnessState = 'validated_exact' | 'estimated' | 'insufficient_evidence' | 'not_applicable';
type ConfidenceDecision = 'high' | 'medium' | 'abstain';
type TrustState =
  | 'answer_ready'
  | 'answer_with_warnings'
  | 'abstained_missing_evidence'
  | 'abstained_conflicting_evidence'
  | 'blocked_access'
  | 'failed_recoverable'
  | 'failed_admin';

interface NormalizedAnswerContract {
  source_mode: SourceMode;
  exactness_state: ExactnessState;
  confidence_decision: ConfidenceDecision;
  trust_state: TrustState;
  evidence_summary: string | null;
  conflict_notes: string[];
  citations: Array<Record<string, unknown>>;
  recovery_actions: Array<Record<string, unknown>>;
  metadata: Record<string, unknown>;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

function normalizeSourceMode(value: unknown): SourceMode {
  if (value === 'structured' || value === 'document' || value === 'hybrid') return value;
  return 'unknown';
}

function normalizeExactnessState(value: unknown): ExactnessState {
  if (
    value === 'validated_exact' ||
    value === 'estimated' ||
    value === 'insufficient_evidence'
  ) {
    return value;
  }
  return 'not_applicable';
}

function normalizeConfidenceDecision(value: unknown): ConfidenceDecision {
  if (value === 'high' || value === 'medium' || value === 'abstain') return value;
  return 'medium';
}

function inferTrustState(
  requested: unknown,
  confidenceDecision: ConfidenceDecision,
  citations: Array<Record<string, unknown>>,
): TrustState {
  if (
    requested === 'answer_ready' ||
    requested === 'answer_with_warnings' ||
    requested === 'abstained_missing_evidence' ||
    requested === 'abstained_conflicting_evidence' ||
    requested === 'blocked_access' ||
    requested === 'failed_recoverable' ||
    requested === 'failed_admin'
  ) {
    return requested;
  }
  if (confidenceDecision === 'abstain') return 'abstained_missing_evidence';
  return citations.length > 0 ? 'answer_ready' : 'answer_with_warnings';
}

function normalizeStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is string => typeof item === 'string')
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

function normalizeCitations(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is Record<string, unknown> => isObject(item));
}

function normalizeRecoveryActions(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is Record<string, unknown> => isObject(item));
}

function isRecoverableOpsSchemaError(err: unknown): boolean {
  if (!err || typeof err !== 'object') return false;
  const code = (err as { code?: unknown }).code;
  return code === '42P01' || code === '42703' || code === '23503';
}

async function emitStalledRunAlertEvent(input: {
  sessionId: string;
  snowflakeUser: string;
  quietSeconds: number;
}): Promise<void> {
  try {
    const productResult = await postgresService.query(
      `SELECT data_product_id AS id
       FROM qa_evidence
       WHERE query_id LIKE ($1 || ':%')
       ORDER BY created_at DESC
       LIMIT 1`,
      [input.sessionId],
      input.snowflakeUser,
    );
    const dataProductId = String(
      (productResult.rows[0] as { id?: string } | undefined)?.id ?? '',
    );
    if (!dataProductId) return;

    await postgresService.query(
      `INSERT INTO ops_alert_events
         (data_product_id, signal, severity, message, source_service, source_route,
          session_id, metadata, created_by)
       VALUES
         ($1::uuid, $2, $3, $4, $5, $6, $7, $8::jsonb, $9)`,
      [
        dataProductId,
        'stalled_run',
        'warning',
        'SSE stream was quiet beyond alert threshold',
        'backend',
        '/agent/stream',
        input.sessionId,
        JSON.stringify({ quiet_seconds: input.quietSeconds }),
        'ekaix-backend',
      ],
      input.snowflakeUser,
    );
  } catch (err) {
    if (isRecoverableOpsSchemaError(err)) return;
  }
}

export function normalizeAnswerContractPayload(
  candidate: unknown,
  fallbackEvidenceSummary?: string,
): NormalizedAnswerContract {
  const payload = isObject(candidate) ? candidate : {};
  const citations = normalizeCitations(payload.citations);
  const confidenceDecision = normalizeConfidenceDecision(payload.confidence_decision);
  const trustState = inferTrustState(payload.trust_state, confidenceDecision, citations);

  return {
    source_mode: normalizeSourceMode(payload.source_mode),
    exactness_state: normalizeExactnessState(payload.exactness_state),
    confidence_decision: confidenceDecision,
    trust_state: trustState,
    evidence_summary:
      typeof payload.evidence_summary === 'string'
        ? payload.evidence_summary
        : (fallbackEvidenceSummary ?? null),
    conflict_notes: normalizeStringList(payload.conflict_notes),
    citations,
    recovery_actions: normalizeRecoveryActions(payload.recovery_actions),
    metadata: isObject(payload.metadata) ? payload.metadata : {},
  };
}

export function normalizeStatusEventData(data: Record<string, unknown>): Record<string, unknown> {
  const contractCandidate =
    (isObject(data.answer_contract) && data.answer_contract) ||
    (isObject(data.contract) && data.contract) ||
    data;
  const fallbackSummary = typeof data.message === 'string' ? data.message : undefined;
  const answerContract = normalizeAnswerContractPayload(contractCandidate, fallbackSummary);

  return {
    ...data,
    source_mode: answerContract.source_mode,
    exactness_state: answerContract.exactness_state,
    confidence_decision: answerContract.confidence_decision,
    trust_state: answerContract.trust_state,
    citations: answerContract.citations,
    recovery_actions: answerContract.recovery_actions,
    answer_contract: answerContract,
  };
}

export function normalizeApiResponseEnvelope(
  payload: Record<string, unknown>,
): Record<string, unknown> {
  const contractCandidate =
    (isObject(payload.answer_contract) && payload.answer_contract) ||
    (isObject(payload.contract) && payload.contract) ||
    (isObject(payload.response_contract) && payload.response_contract) ||
    payload;
  const fallbackSummary = typeof payload.message === 'string' ? payload.message : undefined;
  const answerContract = normalizeAnswerContractPayload(contractCandidate, fallbackSummary);

  return {
    ...payload,
    source_mode: answerContract.source_mode,
    exactness_state: answerContract.exactness_state,
    confidence_decision: answerContract.confidence_decision,
    trust_state: answerContract.trust_state,
    citations: answerContract.citations,
    recovery_actions: answerContract.recovery_actions,
    answer_contract: answerContract,
  };
}

export function normalizeSseLine(line: string): string {
  if (!line.startsWith('data: ')) return line;
  const raw = line.slice('data: '.length);
  if (raw === '[DONE]') return line;

  try {
    const parsed = JSON.parse(raw) as { type?: unknown; data?: unknown };
    if (!isObject(parsed)) return line;
    if (parsed.type !== 'status' || !isObject(parsed.data)) return line;
    const normalized = {
      ...parsed,
      data: normalizeStatusEventData(parsed.data),
    };
    return `data: ${JSON.stringify(normalized)}`;
  } catch {
    return line;
  }
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

      const { session_id, message, data_product_id, attachments, file_contents } = parseResult.data;
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
              file_contents,
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
        return reply.send(normalizeApiResponseEnvelope(responseData));
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
      let lastInboundAt = Date.now();
      let stalledAlertRaised = false;
      const stalledAlertInterval = setInterval(() => {
        const quietMs = Date.now() - lastInboundAt;
        if (quietMs >= 120_000 && !stalledAlertRaised) {
          stalledAlertRaised = true;
          const quietSeconds = Math.floor(quietMs / 1000);
          request.log.warn(
            'OPS_ALERT[stalled_run] session=%s quiet_seconds=%d',
            sessionId,
            quietSeconds,
          );
          void emitStalledRunAlertEvent({
            sessionId,
            snowflakeUser,
            quietSeconds,
          });
        }
        if (quietMs < 120_000) stalledAlertRaised = false;
      }, 30_000);

      let abortController: AbortController | undefined;

      // Clean up on client disconnect
      request.raw.on('close', () => {
        clearInterval(keepaliveInterval);
        clearInterval(stalledAlertInterval);
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
          clearInterval(stalledAlertInterval);
          return;
        }

        const reader = aiResponse.body.getReader();
        const decoder = new TextDecoder();
        let sseBuffer = '';

        const pump = async (): Promise<void> => {
          try {
            let done = false;
            while (!done) {
              const result = await reader.read();
              done = result.done;
              if (result.value) {
                lastInboundAt = Date.now();
                const text = decoder.decode(result.value, { stream: true });
                sseBuffer += text;
                const lines = sseBuffer.split('\n');
                sseBuffer = lines.pop() ?? '';
                for (const line of lines) {
                  raw.write(`${normalizeSseLine(line)}\n`);
                }
              }
            }
            if (sseBuffer.length > 0) {
              raw.write(normalizeSseLine(sseBuffer));
              sseBuffer = '';
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
            clearInterval(stalledAlertInterval);
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
        clearInterval(stalledAlertInterval);
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
        return reply.send(normalizeApiResponseEnvelope(responseData));
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
   * Get conversation history for a session from the AI service's LangGraph checkpointer.
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

      request.log.info(
        { sessionId, user: snowflakeUser },
        'Fetching history from AI service checkpointer',
      );

      try {
        const aiResponse = await fetch(
          `${config.AI_SERVICE_URL}/agent/history/${sessionId}`,
          {
            headers: {
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
              'Failed to fetch history',
          });
        }

        const responseData = (await aiResponse.json()) as Record<string, unknown>;
        return reply.send(normalizeApiResponseEnvelope(responseData));
      } catch (err: unknown) {
        request.log.error({ err }, 'Failed to reach AI service for history');
        return reply.status(502).send({
          error: 'AI_SERVICE_UNAVAILABLE',
          message: 'Unable to reach the AI service',
        });
      }
    },
  );

  /**
   * POST /agent/retry
   * Retry or edit a message using LangGraph checkpoint time-travel.
   */
  app.post(
    '/retry',
    async (request: FastifyRequest, reply) => {
      const parseResult = retrySchema.safeParse(request.body);
      if (!parseResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid request body',
          details: parseResult.error.flatten().fieldErrors,
        });
      }

      const { session_id, data_product_id, message_id, edited_content, original_content } = parseResult.data;
      const { snowflakeUser } = request.user;

      request.log.info(
        { session_id, data_product_id, user: snowflakeUser },
        'Retrying message via AI service',
      );

      try {
        const aiResponse = await fetch(
          `${config.AI_SERVICE_URL}/agent/retry`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-Snowflake-User': snowflakeUser,
            },
            body: JSON.stringify({
              session_id,
              data_product_id,
              message_id,
              edited_content,
              original_content,
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
        return reply.send(normalizeApiResponseEnvelope(responseData));
      } catch (err: unknown) {
        request.log.error({ err }, 'Failed to reach AI service for retry');
        return reply.status(502).send({
          error: 'AI_SERVICE_UNAVAILABLE',
          message: 'Unable to reach the AI service',
        });
      }
    },
  );

  /**
   * GET /agent/checkpoints/:sessionId
   * List checkpoints for a session.
   */
  app.get(
    '/checkpoints/:sessionId',
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

      try {
        const aiResponse = await fetch(
          `${config.AI_SERVICE_URL}/agent/checkpoints/${sessionId}`,
          {
            headers: {
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
              'Failed to fetch checkpoints',
          });
        }

        const responseData = (await aiResponse.json()) as Record<string, unknown>;
        return reply.send(responseData);
      } catch (err: unknown) {
        request.log.error({ err }, 'Failed to reach AI service for checkpoints');
        return reply.status(502).send({
          error: 'AI_SERVICE_UNAVAILABLE',
          message: 'Unable to reach the AI service',
        });
      }
    },
  );

  /**
   * POST /agent/rollback/:checkpointId
   * Rollback conversation to a specific checkpoint.
   */
  app.post(
    '/rollback/:checkpointId',
    async (
      request: FastifyRequest<{
        Params: { checkpointId: string };
        Querystring: { session_id: string };
      }>,
      reply,
    ) => {
      const { checkpointId } = request.params;
      const sessionId =
        (request.query as { session_id?: string }).session_id;
      const { snowflakeUser } = request.user;

      if (!sessionId) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'session_id query parameter is required',
        });
      }

      request.log.info(
        { checkpointId, sessionId, user: snowflakeUser },
        'Rolling back to checkpoint via AI service',
      );

      try {
        const aiResponse = await fetch(
          `${config.AI_SERVICE_URL}/agent/rollback/${checkpointId}?session_id=${encodeURIComponent(sessionId)}`,
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
              (errorBody?.detail as string | undefined) ??
              (errorBody?.message as string | undefined) ??
              'Rollback failed',
            details: errorBody,
          });
        }

        const responseData = (await aiResponse.json()) as Record<string, unknown>;
        return reply.send(responseData);
      } catch (err: unknown) {
        request.log.error({ err }, 'Failed to reach AI service for rollback');
        return reply.status(502).send({
          error: 'AI_SERVICE_UNAVAILABLE',
          message: 'Unable to reach the AI service',
        });
      }
    },
  );
}
