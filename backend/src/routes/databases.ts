import type { FastifyInstance, FastifyRequest } from 'fastify';

import { snowflakeService } from '../services/snowflakeService.js';

export async function databaseRoutes(app: FastifyInstance): Promise<void> {
  /**
   * GET /databases
   * List databases accessible to the current user via Snowflake SHOW DATABASES.
   */
  app.get(
    '/',
    async (
      request: FastifyRequest,
      reply,
    ) => {
      request.log.info('Listing databases from Snowflake');

      const result = await snowflakeService.executeQuery('SHOW DATABASES');

      const databases = result.rows.map((row) => ({
        name: row['name'] as string,
        comment: (row['comment'] as string) ?? '',
        owner: (row['owner'] as string) ?? '',
        created_at: row['created_on'] as string,
      }));

      return reply.send({ databases });
    },
  );

  /**
   * GET /databases/:database/schemas
   * List schemas for a specific database from Snowflake INFORMATION_SCHEMA.
   */
  app.get(
    '/:database/schemas',
    async (
      request: FastifyRequest<{ Params: { database: string } }>,
      reply,
    ) => {
      const { database } = request.params;
      request.log.info({ database }, 'Listing schemas from Snowflake');

      const result = await snowflakeService.executeQuery(
        `SELECT
           SCHEMA_NAME AS "name",
           CATALOG_NAME AS "database",
           COMMENT AS "comment"
         FROM "${database}".INFORMATION_SCHEMA.SCHEMATA
         WHERE SCHEMA_NAME <> 'INFORMATION_SCHEMA'
         ORDER BY SCHEMA_NAME`,
      );

      // For each schema, get approximate table count
      const schemas = await Promise.all(
        result.rows.map(async (row) => {
          const schemaName = row['name'] as string;
          let tablesCount = 0;
          try {
            const tableResult = await snowflakeService.executeQuery(
              `SELECT COUNT(*) AS "count"
               FROM "${database}".INFORMATION_SCHEMA.TABLES
               WHERE TABLE_SCHEMA = '${schemaName}'
                 AND TABLE_TYPE IN ('BASE TABLE', 'VIEW')`,
            );
            tablesCount = Number(tableResult.rows[0]?.['count'] ?? 0);
          } catch {
            // Ignore — user may not have access to count tables in this schema
          }

          return {
            name: schemaName,
            database: row['database'] as string,
            tables_count: tablesCount,
            comment: (row['comment'] as string) ?? '',
          };
        }),
      );

      return reply.send({ schemas });
    },
  );

  /**
   * GET /databases/:database/schemas/:schema/tables
   * List tables in a schema from Snowflake INFORMATION_SCHEMA.
   */
  app.get(
    '/:database/schemas/:schema/tables',
    async (
      request: FastifyRequest<{ Params: { database: string; schema: string } }>,
      reply,
    ) => {
      const { database, schema } = request.params;
      request.log.info({ database, schema }, 'Listing tables from Snowflake');

      const result = await snowflakeService.executeQuery(
        `SELECT
           TABLE_NAME AS "name",
           TABLE_SCHEMA AS "schema",
           TABLE_CATALOG AS "database",
           TABLE_TYPE AS "table_type",
           ROW_COUNT AS "row_count",
           COMMENT AS "comment"
         FROM "${database}".INFORMATION_SCHEMA.TABLES
         WHERE TABLE_SCHEMA = '${schema}'
           AND TABLE_TYPE IN ('BASE TABLE', 'VIEW')
         ORDER BY TABLE_NAME`,
      );

      const tables = result.rows.map((row) => ({
        name: row['name'] as string,
        schema: row['schema'] as string,
        database: row['database'] as string,
        fqn: `${row['database'] as string}.${row['schema'] as string}.${row['name'] as string}`,
        table_type: row['table_type'] as string,
        row_count: row['table_type'] === 'VIEW' ? null : Number(row['row_count'] ?? 0),
        comment: (row['comment'] as string) ?? '',
      }));

      return reply.send({ tables });
    },
  );
}
