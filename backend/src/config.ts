/**
 * Application configuration loaded from the ROOT .env file.
 *
 * IMPORTANT: All configuration is loaded from the ROOT .env file (../.env).
 * Do NOT create a separate .env file in this directory.
 */
import path from 'path';
import { fileURLToPath } from 'url';
import { config as dotenvConfig } from 'dotenv';
import { z } from 'zod';

// Get __dirname equivalent for ESM
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Load environment variables from root .env file (backend/src -> backend -> root)
dotenvConfig({ path: path.resolve(__dirname, '../..', '.env') });

const envSchema = z.object({
  // Service port (BACKEND_PORT is preferred, PORT is fallback)
  BACKEND_PORT: z.coerce.number().optional(),
  PORT: z.coerce.number().default(8000),
  NODE_ENV: z
    .enum(['development', 'production', 'test'])
    .default('development'),

  // PostgreSQL
  DATABASE_URL: z.string().min(1),

  // Neo4j
  NEO4J_URI: z.string().min(1),
  NEO4J_USER: z.string().min(1),
  NEO4J_PASSWORD: z.string().min(1),

  // Redis
  REDIS_URL: z.string().min(1),

  // MinIO
  MINIO_ENDPOINT: z.string().min(1),
  MINIO_PORT: z.coerce.number().default(9000),
  MINIO_ACCESS_KEY: z.string().min(1),
  MINIO_SECRET_KEY: z.string().min(1),
  MINIO_USE_SSL: z
    .enum(['true', 'false'])
    .default('false')
    .transform((val) => val === 'true'),

  // AI Service
  AI_SERVICE_URL: z.string().min(1),

  // Snowflake
  SNOWFLAKE_ACCOUNT: z.string().min(1),
  SNOWFLAKE_USER: z.string().min(1),
  SNOWFLAKE_PASSWORD: z.string().default(''),  // Can be empty if loaded from sf.txt
  SNOWFLAKE_WAREHOUSE: z.string().min(1),
  SNOWFLAKE_DATABASE: z.string().default(''),
  SNOWFLAKE_ROLE: z.string().min(1),

  // Connection tuning (optional)
  PG_IDLE_TIMEOUT_MS: z.coerce.number().default(30000),
});

export type Env = z.infer<typeof envSchema>;

function loadConfig(): Env {
  const result = envSchema.safeParse(process.env);

  if (!result.success) {
    const formatted = result.error.flatten().fieldErrors;
    const missing = Object.entries(formatted)
      .map(([key, errors]) => `  ${key}: ${errors?.join(', ')}`)
      .join('\n');

    throw new Error(`Invalid environment variables:\n${missing}`);
  }

  // Use BACKEND_PORT if set, otherwise fall back to PORT
  const data = result.data;
  if (data.BACKEND_PORT) {
    data.PORT = data.BACKEND_PORT;
  }

  return data;
}

export const config = loadConfig();
