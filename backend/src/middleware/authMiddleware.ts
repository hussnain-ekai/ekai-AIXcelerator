import type { FastifyReply, FastifyRequest } from 'fastify';

import type { User } from '../types/index.js';

/**
 * Fastify preHandler hook that extracts user identity from SPCS headers.
 *
 * In production (SPCS), Snowflake sets the `Sf-Context-Current-User` header.
 * In local development, the `X-Dev-User` header serves as a fallback.
 * Health-check routes are excluded from authentication.
 */
export async function authMiddleware(
  request: FastifyRequest,
  reply: FastifyReply,
): Promise<void> {
  // Skip authentication for health-check routes
  if (request.url.startsWith('/health')) {
    return;
  }

  const spcsHeader = request.headers['sf-context-current-user'];
  const devHeader = request.headers['x-dev-user'];

  const snowflakeUser =
    typeof spcsHeader === 'string' && spcsHeader.length > 0
      ? spcsHeader
      : typeof devHeader === 'string' && devHeader.length > 0
        ? devHeader
        : undefined;

  if (!snowflakeUser) {
    await reply.status(401).send({
      error: 'UNAUTHORIZED',
      message: 'Missing Sf-Context-Current-User header',
    });
    return;
  }

  const user: User = {
    snowflakeUser,
    displayName: snowflakeUser,
    role: 'USER',
    account: 'dev',
  };

  request.user = user;
}
