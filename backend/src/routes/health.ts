import type { FastifyInstance } from 'fastify';

import { postgresService } from '../services/postgresService.js';
import { neo4jService } from '../services/neo4jService.js';
import { redisService } from '../services/redisService.js';
import { minioService } from '../services/minioService.js';
import { snowflakeService } from '../services/snowflakeService.js';

type ServiceStatus = 'ok' | 'down';

interface ServiceHealthMap {
  postgresql: ServiceStatus;
  neo4j: ServiceStatus;
  redis: ServiceStatus;
  minio: ServiceStatus;
  snowflake: ServiceStatus;
}

async function checkAllServices(): Promise<ServiceHealthMap> {
  const [pg, neo4j, redis, minio, sf] = await Promise.all([
    postgresService.healthCheck(),
    neo4jService.healthCheck(),
    redisService.healthCheck(),
    minioService.healthCheck(),
    snowflakeService.healthCheck(),
  ]);

  return {
    postgresql: pg ? 'ok' : 'down',
    neo4j: neo4j ? 'ok' : 'down',
    redis: redis ? 'ok' : 'down',
    minio: minio ? 'ok' : 'down',
    snowflake: sf ? 'ok' : 'down',
  };
}

export async function healthRoutes(app: FastifyInstance): Promise<void> {
  /**
   * GET /health
   * Returns service status with individual dependency health.
   */
  app.get('/', async (_request, reply) => {
    const services = await checkAllServices();

    return reply.send({
      status: 'ok',
      version: '0.1.0',
      timestamp: new Date().toISOString(),
      services,
    });
  });

  /**
   * GET /health/ready
   * Readiness probe. Returns 200 if all services are healthy, 503 otherwise.
   */
  app.get('/ready', async (_request, reply) => {
    const services = await checkAllServices();
    const allHealthy = Object.values(services).every((s) => s === 'ok');

    const statusCode = allHealthy ? 200 : 503;

    return reply.status(statusCode).send({
      status: allHealthy ? 'ready' : 'not_ready',
      timestamp: new Date().toISOString(),
      services,
    });
  });
}
