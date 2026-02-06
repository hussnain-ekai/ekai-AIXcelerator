// Rate limiting is configured via @fastify/rate-limit plugin in src/index.ts.
// This module provides route-level overrides if needed.

import type { FastifyInstance } from 'fastify';

export function registerRateLimitOverrides(
  _app: FastifyInstance,
): void {
  // TODO: Add route-specific rate limit overrides (e.g., stricter limits on /agent)
}
