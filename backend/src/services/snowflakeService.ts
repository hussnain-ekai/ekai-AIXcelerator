import snowflake from 'snowflake-sdk';

import { config } from '../config.js';

export interface SnowflakeQueryResult {
  rows: Record<string, unknown>[];
  statement: string;
}

// Fail-open for OCSP in dev
snowflake.configure({ ocspFailOpen: true });

let connection: snowflake.Connection | null = null;
let connectionPromise: Promise<snowflake.Connection> | null = null;

function getConnection(): Promise<snowflake.Connection> {
  if (connection !== null) {
    return Promise.resolve(connection);
  }

  if (connectionPromise !== null) {
    return connectionPromise;
  }

  connectionPromise = new Promise<snowflake.Connection>((resolve, reject) => {
    const conn = snowflake.createConnection({
      account: config.SNOWFLAKE_ACCOUNT,
      username: config.SNOWFLAKE_USER,
      password: config.SNOWFLAKE_PASSWORD,
      warehouse: config.SNOWFLAKE_WAREHOUSE,
      database: config.SNOWFLAKE_DATABASE || undefined,
      role: config.SNOWFLAKE_ROLE,
      authenticator: 'SNOWFLAKE',
    });

    conn.connect((err) => {
      if (err) {
        connectionPromise = null;
        reject(new Error(`Snowflake connection failed: ${err.message}`));
      } else {
        connection = conn;
        resolve(conn);
      }
    });
  });

  return connectionPromise;
}

/**
 * Execute a SQL query against Snowflake and return rows.
 */
async function executeQuery(
  sql: string,
  binds: snowflake.Binds = [],
): Promise<SnowflakeQueryResult> {
  const conn = await getConnection();

  return new Promise<SnowflakeQueryResult>((resolve, reject) => {
    conn.execute({
      sqlText: sql,
      binds,
      complete: (err, stmt, rows) => {
        if (err) {
          reject(new Error(`Snowflake query error: ${err.message}`));
        } else {
          resolve({
            rows: (rows ?? []) as Record<string, unknown>[],
            statement: stmt.getSqlText(),
          });
        }
      },
    });
  });
}

async function healthCheck(): Promise<boolean> {
  try {
    await executeQuery('SELECT CURRENT_USER() AS "user"');
    return true;
  } catch {
    return false;
  }
}

async function destroy(): Promise<void> {
  if (connection !== null) {
    return new Promise<void>((resolve) => {
      connection?.destroy(() => {
        connection = null;
        connectionPromise = null;
        resolve();
      });
    });
  }
}

export const snowflakeService = { executeQuery, healthCheck, destroy };
