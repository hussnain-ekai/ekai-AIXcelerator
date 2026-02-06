import pg from 'pg';

import { config } from '../config.js';

const pool = new pg.Pool({
  connectionString: config.DATABASE_URL,
  max: 20,
  idleTimeoutMillis: config.PG_IDLE_TIMEOUT_MS,
  connectionTimeoutMillis: 5000,
});

/**
 * Execute a SQL query with RLS context. Before every query, sets
 * `app.current_user` so that PostgreSQL row-level security policies
 * can enforce workspace isolation.
 */
async function query(
  sql: string,
  params: unknown[],
  currentUser: string,
): Promise<pg.QueryResult> {
  const client = await pool.connect();
  try {
    await client.query("SELECT set_config('app.current_user', $1, false)", [currentUser]);
    const result = await client.query(sql, params);
    return result;
  } finally {
    client.release();
  }
}

async function healthCheck(): Promise<boolean> {
  try {
    await pool.query('SELECT 1');
    return true;
  } catch {
    return false;
  }
}

async function close(): Promise<void> {
  await pool.end();
}

export const postgresService = { query, healthCheck, close, pool };
