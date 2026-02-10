import type { FastifyInstance, FastifyRequest } from 'fastify';

import { postgresService } from '../services/postgresService.js';
import { minioService } from '../services/minioService.js';
import { neo4jService } from '../services/neo4jService.js';

interface ArtifactRow {
  id: string;
  data_product_id: string;
  artifact_type: string;
  version: number;
  minio_path: string;
  filename: string | null;
  file_size_bytes: number | null;
  content_type: string | null;
  metadata: Record<string, unknown>;
  created_by: string;
  created_at: string;
}

interface QualityCheckRow {
  id: string;
  data_product_id: string;
  overall_score: number;
  check_results: Record<string, unknown>;
  issues: unknown[];
  acknowledged: boolean;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
  created_at: string;
}

interface BrdRow {
  id: string;
  data_product_id: string;
  version: number;
  brd_json: Record<string, unknown>;
  is_complete: boolean;
  created_by: string;
  created_at: string;
}

interface SemanticViewRow {
  id: string;
  data_product_id: string;
  version: number;
  yaml_content: string;
  validation_status: string;
  validation_errors: unknown;
  validated_at: string | null;
  created_by: string;
  created_at: string;
}

export async function artifactRoutes(app: FastifyInstance): Promise<void> {
  /**
   * GET /artifacts/:dataProductId
   * List artifacts for a data product.
   * By default returns only the latest version of each artifact type.
   * Use ?all_versions=true to get full version history.
   */
  app.get(
    '/:dataProductId',
    async (
      request: FastifyRequest<{
        Params: { dataProductId: string };
        Querystring: { all_versions?: string };
      }>,
      reply,
    ) => {
      const { dataProductId } = request.params;
      const allVersions = request.query.all_versions === 'true';
      const { snowflakeUser } = request.user;

      let result;
      if (allVersions) {
        // Return all versions, ordered by type then version desc
        result = await postgresService.query(
          `SELECT
             id, data_product_id, artifact_type, version, minio_path, filename,
             file_size_bytes, content_type, metadata, created_by, created_at
           FROM artifacts
           WHERE data_product_id = $1
           ORDER BY artifact_type, version DESC`,
          [dataProductId],
          snowflakeUser,
        );
      } else {
        // Return only the latest version of each artifact type
        result = await postgresService.query(
          `SELECT DISTINCT ON (artifact_type)
             id, data_product_id, artifact_type, version, minio_path, filename,
             file_size_bytes, content_type, metadata, created_by, created_at
           FROM artifacts
           WHERE data_product_id = $1
           ORDER BY artifact_type, version DESC`,
          [dataProductId],
          snowflakeUser,
        );
      }

      return reply.send({
        data: result.rows as ArtifactRow[],
      });
    },
  );

  /**
   * GET /artifacts/:dataProductId/versions/:artifactType
   * Get version history for a specific artifact type.
   */
  app.get(
    '/:dataProductId/versions/:artifactType',
    async (
      request: FastifyRequest<{
        Params: { dataProductId: string; artifactType: string };
      }>,
      reply,
    ) => {
      const { dataProductId, artifactType } = request.params;
      const { snowflakeUser } = request.user;

      const result = await postgresService.query(
        `SELECT
           id, version, created_at, created_by, file_size_bytes
         FROM artifacts
         WHERE data_product_id = $1 AND artifact_type = $2::artifact_type
         ORDER BY version DESC`,
        [dataProductId, artifactType],
        snowflakeUser,
      );

      return reply.send({
        artifact_type: artifactType,
        versions: result.rows as Array<{
          id: string;
          version: number;
          created_at: string;
          created_by: string;
          file_size_bytes: number | null;
        }>,
      });
    },
  );

  /**
   * GET /artifacts/:dataProductId/:artifactId
   * Get a single artifact's content from MinIO.
   */
  app.get(
    '/:dataProductId/:artifactId',
    async (
      request: FastifyRequest<{
        Params: { dataProductId: string; artifactId: string };
      }>,
      reply,
    ) => {
      const { dataProductId, artifactId } = request.params;
      const { snowflakeUser } = request.user;

      // Look up artifact metadata
      const result = await postgresService.query(
        `SELECT
           id, minio_path, filename, content_type
         FROM artifacts
         WHERE id = $1 AND data_product_id = $2`,
        [artifactId, dataProductId],
        snowflakeUser,
      );

      const artifact = result.rows[0] as
        | { id: string; minio_path: string; filename: string | null; content_type: string | null }
        | undefined;

      if (!artifact) {
        return reply.status(404).send({
          error: 'NOT_FOUND',
          message: 'Artifact not found',
        });
      }

      try {
        const content = await minioService.getFile('artifacts', artifact.minio_path);

        return reply
          .header(
            'Content-Type',
            artifact.content_type ?? 'application/octet-stream',
          )
          .header(
            'Content-Disposition',
            `inline; filename="${artifact.filename ?? artifactId}"`,
          )
          .send(content);
      } catch (err: unknown) {
        request.log.error({ err, artifactId }, 'Failed to retrieve artifact from MinIO');
        return reply.status(404).send({
          error: 'NOT_FOUND',
          message: 'Artifact content not found in storage',
        });
      }
    },
  );

  /**
   * GET /artifacts/:dataProductId/erd
   * Get ERD data from Neo4j for a data product.
   * Returns nodes (tables) and edges (relationships).
   */
  app.get(
    '/:dataProductId/erd',
    async (
      request: FastifyRequest<{ Params: { dataProductId: string } }>,
      reply,
    ) => {
      const { dataProductId } = request.params;

      try {
        const erdData = await neo4jService.executeRead(async (tx) => {
          // Fetch table nodes linked to this data product
          // Note: AI service stores property as data_product_id (snake_case)
          const nodesResult = await tx.run(
            `MATCH (t:Table {data_product_id: $dataProductId})
             OPTIONAL MATCH (t)-[:HAS_COLUMN]->(c:Column)
             RETURN t, collect(c) as columns`,
            { dataProductId },
          );

          const nodes = nodesResult.records.map((record) => {
            const table = record.get('t') as {
              properties: Record<string, unknown>;
            };
            const columns = record.get('columns') as Array<{
              properties: Record<string, unknown>;
            }>;
            const props = table.properties;
            // Neo4j stores classification (FACT/DIMENSION), frontend expects type (fact/dimension)
            const classification = ((props.classification ?? 'UNKNOWN') as string).toLowerCase();
            const tableType = classification === 'fact' ? 'fact' : 'dimension';
            return {
              id: props.fqn as string,
              name: (props.fqn as string)?.split('.').pop() ?? props.fqn,
              type: tableType,
              rowCount: typeof props.row_count === 'object' && props.row_count !== null
                ? Number((props.row_count as { low?: number }).low ?? 0)
                : Number(props.row_count ?? 0),
              columns: columns
                .filter((c) => c.properties)
                .map((c) => ({
                  name: c.properties.name as string,
                  dataType: c.properties.data_type as string,
                  nullable: c.properties.nullable as boolean,
                  isPrimaryKey: (c.properties.is_pk ?? false) as boolean,
                  isForeignKey: (c.properties.is_fk ?? false) as boolean,
                })),
            };
          });

          // Fetch relationship edges
          // Note: AI service creates FK_REFERENCES relationships (not HAS_FOREIGN_KEY)
          const edgesResult = await tx.run(
            `MATCH (s:Table {data_product_id: $dataProductId})-[r:FK_REFERENCES]->(t:Table)
             RETURN s.fqn as source, t.fqn as target, r as relationship`,
            { dataProductId },
          );

          const edges = edgesResult.records.map((record, idx) => {
            const rel = record.get('relationship') as {
              properties: Record<string, unknown>;
            };
            const source = record.get('source') as string;
            const target = record.get('target') as string;
            const srcCol = (rel.properties.source_column as string) ?? '';
            const tgtCol = (rel.properties.target_column as string) ?? '';
            return {
              id: `edge-${idx}`,
              source,
              target,
              label: srcCol && tgtCol ? `${srcCol} → ${tgtCol}` : 'FK_REFERENCES',
              sourceColumn: srcCol,
              targetColumn: tgtCol,
              confidence: (rel.properties.confidence as number) ?? 1.0,
              cardinality: (rel.properties.cardinality as string) ?? 'many-to-one',
            };
          });

          return { nodes, edges };
        });

        return reply.send(erdData);
      } catch (err: unknown) {
        request.log.error({ err, dataProductId }, 'Failed to fetch ERD from Neo4j');
        return reply.send({ nodes: [], edges: [] });
      }
    },
  );

  /**
   * GET /artifacts/:dataProductId/yaml
   * Get the latest semantic view YAML for a data product.
   */
  app.get(
    '/:dataProductId/yaml',
    async (
      request: FastifyRequest<{ Params: { dataProductId: string } }>,
      reply,
    ) => {
      const { dataProductId } = request.params;
      const { snowflakeUser } = request.user;

      const result = await postgresService.query(
        `SELECT
           id, data_product_id, version, yaml_content, validation_status,
           validation_errors, validated_at, created_by, created_at
         FROM semantic_views
         WHERE data_product_id = $1
         ORDER BY version DESC
         LIMIT 1`,
        [dataProductId],
        snowflakeUser,
      );

      const semanticView = result.rows[0] as SemanticViewRow | undefined;

      if (!semanticView) {
        return reply.status(404).send({
          error: 'NOT_FOUND',
          message: 'No semantic view found for this data product',
        });
      }

      return reply.send(semanticView);
    },
  );

  /**
   * GET /artifacts/:dataProductId/brd
   * Get the latest Business Requirements Document for a data product.
   */
  app.get(
    '/:dataProductId/brd',
    async (
      request: FastifyRequest<{ Params: { dataProductId: string } }>,
      reply,
    ) => {
      const { dataProductId } = request.params;
      const { snowflakeUser } = request.user;

      const result = await postgresService.query(
        `SELECT
           id, data_product_id, version, brd_json, is_complete,
           created_by, created_at
         FROM business_requirements
         WHERE data_product_id = $1
         ORDER BY version DESC
         LIMIT 1`,
        [dataProductId],
        snowflakeUser,
      );

      const brd = result.rows[0] as BrdRow | undefined;

      if (!brd) {
        return reply.status(404).send({
          error: 'NOT_FOUND',
          message: 'No BRD found for this data product',
        });
      }

      return reply.send(brd);
    },
  );

  /**
   * GET /artifacts/:dataProductId/data-description
   * Get the latest Data Description for a data product.
   */
  app.get(
    '/:dataProductId/data-description',
    async (
      request: FastifyRequest<{ Params: { dataProductId: string } }>,
      reply,
    ) => {
      const { dataProductId } = request.params;
      const { snowflakeUser } = request.user;

      const result = await postgresService.query(
        `SELECT
           id, data_product_id, version, description_json,
           created_by, created_at
         FROM data_descriptions
         WHERE data_product_id = $1
         ORDER BY version DESC
         LIMIT 1`,
        [dataProductId],
        snowflakeUser,
      );

      interface DataDescriptionRow {
        id: string;
        data_product_id: string;
        version: number;
        description_json: Record<string, unknown>;
        created_by: string;
        created_at: string;
      }

      const dd = result.rows[0] as DataDescriptionRow | undefined;

      if (!dd) {
        return reply.status(404).send({
          error: 'NOT_FOUND',
          message: 'No data description found for this data product',
        });
      }

      return reply.send(dd);
    },
  );

  /**
   * GET /artifacts/:dataProductId/quality-report
   * Get the latest data quality report for a data product.
   * Transforms response to camelCase format expected by frontend.
   */
  app.get(
    '/:dataProductId/quality-report',
    async (
      request: FastifyRequest<{ Params: { dataProductId: string } }>,
      reply,
    ) => {
      const { dataProductId } = request.params;
      const { snowflakeUser } = request.user;

      const result = await postgresService.query(
        `SELECT
           id, data_product_id, overall_score, check_results, issues,
           acknowledged, acknowledged_by, acknowledged_at, created_at
         FROM data_quality_checks
         WHERE data_product_id = $1
         ORDER BY created_at DESC
         LIMIT 1`,
        [dataProductId],
        snowflakeUser,
      );

      const report = result.rows[0] as QualityCheckRow | undefined;

      if (!report) {
        return reply.status(404).send({
          error: 'NOT_FOUND',
          message: 'No quality report found for this data product',
        });
      }

      // Transform to frontend QualityReport format (camelCase + computed fields)
      const checkResults = report.check_results as Record<string, unknown> ?? {};
      const tableSummaries = (checkResults.table_summaries ?? checkResults.tableSummaries ?? []) as Array<{
        table_name?: string;
        tableName?: string;
        score?: number;
        issues?: unknown[];
        issue_count?: number;
        issueCount?: number;
        row_count?: number;
        rowCount?: number;
        null_percentage?: number;
        nullPercentage?: number;
      }>;

      // Calculate passing tables (score >= 70 is "good")
      const passingThreshold = 70;
      const passingTables = tableSummaries.filter(
        (t) => (t.score ?? 0) >= passingThreshold
      ).length;

      // Build checks array grouped by check_type for the frontend
      // Frontend expects: { title: string, issues: { table, column?, detail }[] }[]
      // Issues can be either strings or objects with table/column/detail fields
      const rawIssues = report.issues ?? [];

      // Group issues by check_type (or inferred category for string issues)
      const issuesByType = new Map<string, Array<{ table: string; column?: string; detail: string }>>();

      for (const issue of rawIssues) {
        let checkType = 'General';
        let issueEntry: { table: string; column?: string; detail: string };

        if (typeof issue === 'string') {
          // Issue is a plain string - infer category from content
          const issueStr = issue as string;
          if (issueStr.toLowerCase().includes('primary key') || issueStr.toLowerCase().includes('duplicate')) {
            checkType = 'Primary Key Issues';
          } else if (issueStr.toLowerCase().includes('foreign key')) {
            checkType = 'Foreign Key Issues';
          } else if (issueStr.toLowerCase().includes('null')) {
            checkType = 'Data Completeness';
          } else if (issueStr.toLowerCase().includes('description')) {
            checkType = 'Documentation';
          }

          // Extract table name from string if present (e.g., "DMTDEMO.BRONZE.TABLE_NAME")
          const tableMatch = issueStr.match(/([A-Z_]+\.[A-Z_]+\.[A-Z_]+)/i);
          issueEntry = {
            table: tableMatch?.[1] ?? '',
            detail: issueStr,
          };
        } else {
          // Issue is an object with structured fields
          const issueObj = issue as {
            table?: string;
            column?: string;
            check_type?: string;
            checkType?: string;
            severity?: string;
            message?: string;
            detail?: string;
          };
          checkType = issueObj.check_type ?? issueObj.checkType ?? 'General';
          issueEntry = {
            table: issueObj.table ?? '',
            column: issueObj.column,
            detail: issueObj.detail ?? issueObj.message ?? '',
          };
        }

        if (!issuesByType.has(checkType)) {
          issuesByType.set(checkType, []);
        }
        issuesByType.get(checkType)!.push(issueEntry);
      }

      // Convert to array format expected by frontend
      const checks = Array.from(issuesByType.entries()).map(([title, issues]) => ({
        title,
        issues,
      }));

      // If no issues exist, provide default empty sections for common check types
      if (checks.length === 0) {
        checks.push(
          { title: 'Primary Key Uniqueness', issues: [] },
          { title: 'Foreign Key Integrity', issues: [] },
          { title: 'Null Value Analysis', issues: [] },
        );
      }

      const transformedReport = {
        id: report.id,
        dataProductId: report.data_product_id,
        overallScore: report.overall_score,
        totalTables: tableSummaries.length > 0 ? tableSummaries.length : 1,
        passingTables: tableSummaries.length > 0 ? passingTables : (report.overall_score >= passingThreshold ? 1 : 0),
        tableSummaries: tableSummaries.map((t) => ({
          tableName: t.table_name ?? t.tableName ?? '',
          score: t.score ?? 0,
          issueCount: t.issue_count ?? t.issueCount ?? (t.issues as unknown[] | undefined)?.length ?? 0,
          rowCount: t.row_count ?? t.rowCount ?? 0,
        })),
        checks,
        acknowledged: report.acknowledged,
        acknowledgedBy: report.acknowledged_by,
        acknowledgedAt: report.acknowledged_at,
        createdAt: report.created_at,
      };

      return reply.send(transformedReport);
    },
  );
}
