import type { FastifyReply, FastifyRequest } from 'fastify';

export async function requestLogger(
  request: FastifyRequest,
  _reply: FastifyReply,
): Promise<void> {
  request.log.info(
    {
      method: request.method,
      url: request.url,
      requestId: request.id,
    },
    'Incoming request',
  );
}
