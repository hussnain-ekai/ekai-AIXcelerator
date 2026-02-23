import type { FastifyError, FastifyReply, FastifyRequest } from 'fastify';
import { config } from '../config.js';

export function errorHandler(
  error: FastifyError,
  request: FastifyRequest,
  reply: FastifyReply,
): void {
  const statusCode = error.statusCode ?? 500;
  const errorCode =
    statusCode >= 500 ? 'INTERNAL_SERVER_ERROR' : (error.code ?? 'REQUEST_ERROR');

  let message =
    statusCode >= 500
      ? 'Internal Server Error'
      : (error.message || 'Request failed');

  // Fastify emits this code when JSON payload exceeds bodyLimit.
  if (error.code === 'FST_ERR_CTP_BODY_TOO_LARGE') {
    message = `Payload too large. Maximum request size is ${config.BACKEND_BODY_LIMIT_MB}MB.`;
  }

  request.log.error(
    { err: error, requestId: request.id },
    'Request error occurred',
  );

  void reply.status(statusCode).send({
    error: errorCode,
    message,
    statusCode,
    requestId: request.id,
  });
}
