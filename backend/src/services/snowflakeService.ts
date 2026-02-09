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

function createNewConnection(): Promise<snowflake.Connection> {
  // Clear any stale cached state
  connection = null;
  connectionPromise = null;

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

function getConnection(): Promise<snowflake.Connection> {
  // If we have a cached connection, verify it's still alive
  if (connection !== null) {
    if (connection.isUp()) {
      return Promise.resolve(connection);
    }
    // Connection is stale/terminated — discard and reconnect
    connection = null;
    connectionPromise = null;
  }

  // If a connection attempt is already in progress, reuse it
  if (connectionPromise !== null) {
    return connectionPromise;
  }

  return createNewConnection();
}

/**
 * Execute a single query attempt against Snowflake.
 */
function executeQueryOnce(
  conn: snowflake.Connection,
  sql: string,
  binds: snowflake.Binds,
): Promise<SnowflakeQueryResult> {
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

/**
 * Execute a SQL query against Snowflake and return rows.
 * Automatically reconnects once if the connection has been terminated.
 */
async function executeQuery(
  sql: string,
  binds: snowflake.Binds = [],
): Promise<SnowflakeQueryResult> {
  const conn = await getConnection();

  try {
    return await executeQueryOnce(conn, sql, binds);
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : String(err);
    // If the connection was terminated, reconnect and retry once
    if (message.includes('terminated connection')) {
      connection = null;
      connectionPromise = null;
      const freshConn = await createNewConnection();
      return executeQueryOnce(freshConn, sql, binds);
    }
    throw err;
  }
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
