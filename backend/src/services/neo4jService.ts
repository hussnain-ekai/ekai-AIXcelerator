import neo4j, { type Driver, type ManagedTransaction } from 'neo4j-driver';

import { config } from '../config.js';

let driver: Driver | undefined;

function getDriver(): Driver {
  if (!driver) {
    driver = neo4j.driver(
      config.NEO4J_URI,
      neo4j.auth.basic(config.NEO4J_USER, config.NEO4J_PASSWORD),
    );
  }
  return driver;
}

/**
 * Execute a read transaction. The `work` callback receives a
 * ManagedTransaction from the neo4j-driver v6 API.
 */
async function executeRead<T>(
  work: (tx: ManagedTransaction) => Promise<T>,
): Promise<T> {
  const session = getDriver().session();
  try {
    return await session.executeRead(work);
  } finally {
    await session.close();
  }
}

/**
 * Execute a write transaction. The `work` callback receives a
 * ManagedTransaction from the neo4j-driver v6 API.
 */
async function executeWrite<T>(
  work: (tx: ManagedTransaction) => Promise<T>,
): Promise<T> {
  const session = getDriver().session();
  try {
    return await session.executeWrite(work);
  } finally {
    await session.close();
  }
}

async function healthCheck(): Promise<boolean> {
  try {
    const session = getDriver().session();
    try {
      await session.run('RETURN 1');
      return true;
    } finally {
      await session.close();
    }
  } catch {
    return false;
  }
}

async function close(): Promise<void> {
  if (driver) {
    await driver.close();
    driver = undefined;
  }
}

export const neo4jService = {
  executeRead,
  executeWrite,
  healthCheck,
  close,
  getDriver,
};
