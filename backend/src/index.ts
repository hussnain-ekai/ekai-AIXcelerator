import 'dotenv/config';

import Fastify from 'fastify';
import cors from '@fastify/cors';
import rateLimit from '@fastify/rate-limit';

import { config } from './config.js';
import { authMiddleware } from './middleware/authMiddleware.js';
import { errorHandler } from './middleware/errorHandler.js';
import { requestLogger } from './middleware/requestLogger.js';
import { healthRoutes } from './routes/health.js';
import { authRoutes } from './routes/auth.js';
import { databaseRoutes } from './routes/databases.js';
import { dataProductRoutes } from './routes/dataProducts.js';
import { agentRoutes } from './routes/agent.js';
import { artifactRoutes } from './routes/artifacts.js';
import { documentRoutes } from './routes/documents.js';
import { settingsRoutes, restoreSavedLlmConfig } from './routes/settings.js';
import { postgresService } from './services/postgresService.js';
import { neo4jService } from './services/neo4jService.js';
import { redisService } from './services/redisService.js';

async function buildApp(): Promise<ReturnType<typeof Fastify>> {
  const app = Fastify({
    logger: {
      level: config.NODE_ENV === 'production' ? 'info' : 'debug',
      transport:
        config.NODE_ENV === 'development'
          ? { target: 'pino-pretty', options: { colorize: true } }
          : undefined,
    },
  });

  // --- Global error handler ---
  app.setErrorHandler(errorHandler);

  // --- Global hooks ---
  app.addHook('preHandler', requestLogger);
  app.addHook('preHandler', authMiddleware);

  // --- Plugins ---
  await app.register(cors, {
    origin: true,
    credentials: true,
    methods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS', 'PATCH'],
  });

  await app.register(rateLimit, {
    max: 100,
    timeWindow: '1 minute',
  });

  // --- Routes ---
  await app.register(healthRoutes, { prefix: '/health' });
  await app.register(authRoutes, { prefix: '/auth' });
  await app.register(databaseRoutes, { prefix: '/databases' });
  await app.register(dataProductRoutes, { prefix: '/data-products' });
  await app.register(agentRoutes, { prefix: '/agent' });
  await app.register(artifactRoutes, { prefix: '/artifacts' });
  await app.register(documentRoutes, { prefix: '/documents' });
  await app.register(settingsRoutes, { prefix: '/settings' });

  return app;
}

async function start(): Promise<void> {
  const app = await buildApp();

  const shutdown = async (signal: string): Promise<void> => {
    app.log.info(`Received ${signal}, shutting down gracefully...`);
    await app.close();
    await postgresService.close();
    await neo4jService.close();
    await redisService.close();
    process.exit(0);
  };

  process.on('SIGTERM', () => void shutdown('SIGTERM'));
  process.on('SIGINT', () => void shutdown('SIGINT'));

  await app.listen({ port: config.PORT, host: '0.0.0.0' });
  app.log.info(`Server running on port ${config.PORT}`);

  // Restore saved LLM config from PostgreSQL (fire-and-forget, but log errors)
  restoreSavedLlmConfig(app.log).catch((err) => {
    app.log.error({ err }, 'Failed to restore LLM config on startup');
  });
}

start().catch((err: unknown) => {
  console.error('Failed to start server:', err);
  process.exit(1);
});

export { buildApp };
