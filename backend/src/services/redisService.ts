import { createClient, type RedisClientType } from 'redis';

import { config } from '../config.js';

let client: RedisClientType | undefined;

async function getClient(): Promise<RedisClientType> {
  if (!client) {
    client = createClient({ url: config.REDIS_URL });
    client.on('error', (err: unknown) => {
      console.error('Redis error:', err);
    });
    await client.connect();
  }
  return client;
}

async function healthCheck(): Promise<boolean> {
  try {
    const c = await getClient();
    const pong = await c.ping();
    return pong === 'PONG';
  } catch {
    return false;
  }
}

async function close(): Promise<void> {
  if (client) {
    await client.quit();
    client = undefined;
  }
}

export const redisService = { getClient, healthCheck, close };
