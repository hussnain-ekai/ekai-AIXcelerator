/**
 * Lightweight SQL migration runner.
 *
 * Reads scripts/migrate-*.sql files in alphabetical order,
 * applies unapplied migrations inside transactions, and records
 * each version + SHA-256 checksum in schema_migrations.
 *
 * Usage: npx tsx src/scripts/migrate.ts
 */

import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

import pg from 'pg';

const DATABASE_URL = process.env.DATABASE_URL;

if (!DATABASE_URL) {
  console.error('DATABASE_URL environment variable is required');
  process.exit(1);
}

const SCRIPTS_DIR = path.resolve(import.meta.dirname, '../../../scripts');

async function main(): Promise<void> {
  const client = new pg.Client({ connectionString: DATABASE_URL });
  await client.connect();

  try {
    // Ensure tracker table exists
    await client.query(`
      CREATE TABLE IF NOT EXISTS schema_migrations (
        version TEXT PRIMARY KEY,
        filename TEXT NOT NULL,
        checksum_sha256 TEXT NOT NULL,
        applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        applied_by TEXT DEFAULT current_user
      )
    `);

    // Read applied migrations
    const appliedResult = await client.query(
      'SELECT version, checksum_sha256 FROM schema_migrations ORDER BY version',
    );
    const applied = new Map<string, string>(
      appliedResult.rows.map((r: { version: string; checksum_sha256: string }) => [
        r.version,
        r.checksum_sha256,
      ]),
    );

    // Find migration files
    const files = fs
      .readdirSync(SCRIPTS_DIR)
      .filter((f) => /^migrate-\d{3}-.+\.sql$/.test(f))
      .sort();

    if (files.length === 0) {
      console.log('No migration files found.');
      return;
    }

    let appliedCount = 0;
    let skippedCount = 0;

    for (const filename of files) {
      const version = filename.replace(/\.sql$/, '');
      const filePath = path.join(SCRIPTS_DIR, filename);
      const sql = fs.readFileSync(filePath, 'utf-8');
      const checksum = crypto.createHash('sha256').update(sql).digest('hex');

      const existingChecksum = applied.get(version);

      if (existingChecksum) {
        if (existingChecksum !== checksum) {
          console.warn(
            `WARNING: ${filename} checksum mismatch (expected ${existingChecksum.slice(0, 12)}..., got ${checksum.slice(0, 12)}...). Skipping.`,
          );
        }
        skippedCount++;
        continue;
      }

      // Apply migration in a transaction
      console.log(`Applying ${filename}...`);
      try {
        await client.query('BEGIN');
        await client.query(sql);
        await client.query(
          'INSERT INTO schema_migrations (version, filename, checksum_sha256) VALUES ($1, $2, $3)',
          [version, filename, checksum],
        );
        await client.query('COMMIT');
        appliedCount++;
        console.log(`  Applied ${filename}`);
      } catch (err: unknown) {
        await client.query('ROLLBACK');
        const message = err instanceof Error ? err.message : String(err);
        console.error(`  FAILED ${filename}: ${message}`);
        process.exit(1);
      }
    }

    console.log(
      `\nMigration complete: ${appliedCount} applied, ${skippedCount} already applied, ${files.length} total.`,
    );
  } finally {
    await client.end();
  }
}

main().catch((err) => {
  console.error('Migration runner failed:', err);
  process.exit(1);
});
