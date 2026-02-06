import crypto from 'node:crypto';

import type { FastifyInstance, FastifyRequest } from 'fastify';

import { postgresService } from '../services/postgresService.js';
import {
  createDataProductSchema,
  updateDataProductSchema,
  shareDataProductSchema,
  listDataProductsQuerySchema,
} from '../schemas/dataProduct.js';

// --- Row type returned from PostgreSQL ---

interface DataProductRow {
  id: string;
  workspace_id: string;
  name: string;
  description: string | null;
  database_reference: string;
  schemas: string[];
  tables: string[];
  status: string;
  state: Record<string, unknown>;
  health_score: number | null;
  published_at: string | null;
  published_agent_fqn: string | null;
  created_at: string;
  updated_at: string;
}

interface ShareRow {
  id: string;
  data_product_id: string;
  shared_with_user: string;
  permission: string;
  shared_by: string;
  created_at: string;
}

/** Allowed sort columns. Only whitelisted column names are used in ORDER BY. */
const SORT_COLUMN_MAP: Record<string, string> = {
  name: 'dp.name',
  updated_at: 'dp.updated_at',
  created_at: 'dp.created_at',
  status: 'dp.status',
  health_score: 'dp.health_score',
};

export async function dataProductRoutes(app: FastifyInstance): Promise<void> {
  /**
   * GET /data-products
   * List data products with pagination, search, and sorting.
   */
  app.get(
    '/',
    async (request: FastifyRequest, reply) => {
      const queryResult = listDataProductsQuerySchema.safeParse(request.query);
      if (!queryResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid query parameters',
          details: queryResult.error.flatten().fieldErrors,
        });
      }

      const { page, per_page, search, status, sort_by, sort_order } =
        queryResult.data;
      const { snowflakeUser } = request.user;
      const offset = (page - 1) * per_page;

      // Build dynamic WHERE clause — always exclude archived/deleted
      const conditions: string[] = ["dp.status <> 'archived'"];
      const params: unknown[] = [];
      let paramIndex = 1;

      if (search) {
        conditions.push(
          `(dp.name ILIKE $${paramIndex} OR dp.description ILIKE $${paramIndex})`,
        );
        params.push(`%${search}%`);
        paramIndex++;
      }

      if (status) {
        conditions.push(`dp.status = $${paramIndex}`);
        params.push(status);
        paramIndex++;
      }

      const whereClause =
        conditions.length > 0 ? `WHERE ${conditions.join(' AND ')}` : '';

      // Safe sort column (whitelisted)
      const sortColumn = SORT_COLUMN_MAP[sort_by] ?? 'dp.updated_at';
      const sortDir = sort_order === 'asc' ? 'ASC' : 'DESC';

      // Count total
      const countSql = `
        SELECT COUNT(*) AS total
        FROM data_products dp
        ${whereClause}
      `;
      const countResult = await postgresService.query(
        countSql,
        params,
        snowflakeUser,
      );
      const total = Number(
        (countResult.rows[0] as { total: string } | undefined)?.total ?? 0,
      );
      const totalPages = Math.ceil(total / per_page);

      // Fetch page with owner (from workspaces) and share_count (from data_product_shares)
      const dataSql = `
        SELECT
          dp.id,
          dp.workspace_id,
          dp.name,
          dp.description,
          dp.database_reference,
          dp.schemas,
          dp.tables,
          dp.status,
          dp.state,
          dp.health_score,
          dp.published_at,
          dp.published_agent_fqn,
          dp.created_at,
          dp.updated_at,
          w.snowflake_user AS owner,
          COALESCE(shares.share_count, 0) AS share_count
        FROM data_products dp
        LEFT JOIN workspaces w ON dp.workspace_id = w.id
        LEFT JOIN (
          SELECT data_product_id, COUNT(*) AS share_count
          FROM data_product_shares
          GROUP BY data_product_id
        ) shares ON dp.id = shares.data_product_id
        ${whereClause}
        ORDER BY ${sortColumn} ${sortDir}
        LIMIT $${paramIndex} OFFSET $${paramIndex + 1}
      `;

      const dataResult = await postgresService.query(
        dataSql,
        [...params, per_page, offset],
        snowflakeUser,
      );

      // Transform to include owner and shareCount in expected format
      const rows = dataResult.rows as Array<DataProductRow & { owner?: string; share_count?: number }>;
      const transformedRows = rows.map((row) => ({
        ...row,
        owner: row.owner ?? null,
        shareCount: Number(row.share_count ?? 0),
      }));

      return reply.send({
        data: transformedRows,
        meta: {
          page,
          per_page,
          total,
          total_pages: totalPages,
        },
      });
    },
  );

  /**
   * POST /data-products
   * Create a new data product. Also ensures the user's workspace exists.
   */
  app.post(
    '/',
    async (request: FastifyRequest, reply) => {
      const parseResult = createDataProductSchema.safeParse(request.body);
      if (!parseResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid request body',
          details: parseResult.error.flatten().fieldErrors,
        });
      }

      const { name, description, database_reference, schemas, tables } =
        parseResult.data;
      const { snowflakeUser } = request.user;

      // Ensure workspace exists
      const wsResult = await postgresService.query(
        `INSERT INTO workspaces (snowflake_user, display_name)
         VALUES ($1, $1)
         ON CONFLICT (snowflake_user) DO UPDATE SET snowflake_user = EXCLUDED.snowflake_user
         RETURNING id`,
        [snowflakeUser],
        snowflakeUser,
      );
      const workspaceId = (wsResult.rows[0] as { id: string } | undefined)?.id;

      if (!workspaceId) {
        return reply.status(500).send({
          error: 'INTERNAL_ERROR',
          message: 'Failed to resolve workspace',
        });
      }

      // Generate a session ID for the agent conversation
      const sessionId = crypto.randomUUID();

      // Insert data product
      try {
        const insertResult = await postgresService.query(
          `INSERT INTO data_products
             (workspace_id, name, description, database_reference, schemas, tables, state)
           VALUES ($1, $2, $3, $4, $5, $6, $7)
           RETURNING
             id, workspace_id, name, description, database_reference, schemas, tables,
             status, state, health_score, published_at, published_agent_fqn,
             created_at, updated_at`,
          [
            workspaceId,
            name,
            description,
            database_reference,
            schemas,
            tables,
            JSON.stringify({ session_id: sessionId, current_phase: 'discovery' }),
          ],
          snowflakeUser,
        );

        const product = insertResult.rows[0] as DataProductRow | undefined;

        if (!product) {
          return reply.status(500).send({
            error: 'INTERNAL_ERROR',
            message: 'Failed to create data product',
          });
        }

        return reply.status(201).send({
          ...product,
          session_id: sessionId,
        });
      } catch (err: unknown) {
        // Check for unique constraint violation (duplicate name in workspace)
        const pgError = err as { code?: string };
        if (pgError.code === '23505') {
          return reply.status(409).send({
            error: 'CONFLICT',
            message: `A data product named '${name}' already exists in your workspace`,
          });
        }
        throw err;
      }
    },
  );

  /**
   * GET /data-products/:id
   * Get a single data product by ID.
   */
  app.get(
    '/:id',
    async (
      request: FastifyRequest<{ Params: { id: string } }>,
      reply,
    ) => {
      const { id } = request.params;
      const { snowflakeUser } = request.user;

      const result = await postgresService.query(
        `SELECT
           id, workspace_id, name, description, database_reference, schemas, tables,
           status, state, health_score, published_at, published_agent_fqn,
           created_at, updated_at
         FROM data_products
         WHERE id = $1`,
        [id],
        snowflakeUser,
      );

      const product = result.rows[0] as DataProductRow | undefined;

      if (!product) {
        return reply.status(404).send({
          error: 'NOT_FOUND',
          message: 'Data product not found',
        });
      }

      return reply.send(product);
    },
  );

  /**
   * PUT /data-products/:id
   * Update a data product's name, description, or status.
   */
  app.put(
    '/:id',
    async (
      request: FastifyRequest<{ Params: { id: string } }>,
      reply,
    ) => {
      const { id } = request.params;
      const { snowflakeUser } = request.user;

      const parseResult = updateDataProductSchema.safeParse(request.body);
      if (!parseResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid request body',
          details: parseResult.error.flatten().fieldErrors,
        });
      }

      const updates = parseResult.data;

      // Build dynamic SET clause
      const setClauses: string[] = [];
      const params: unknown[] = [];
      let paramIndex = 1;

      if (updates.name !== undefined) {
        setClauses.push(`name = $${paramIndex}`);
        params.push(updates.name);
        paramIndex++;
      }

      if (updates.description !== undefined) {
        setClauses.push(`description = $${paramIndex}`);
        params.push(updates.description);
        paramIndex++;
      }

      if (updates.database_reference !== undefined) {
        setClauses.push(`database_reference = $${paramIndex}`);
        params.push(updates.database_reference);
        paramIndex++;
      }

      if (updates.schemas !== undefined) {
        setClauses.push(`schemas = $${paramIndex}`);
        params.push(updates.schemas);
        paramIndex++;
      }

      if (updates.tables !== undefined) {
        setClauses.push(`tables = $${paramIndex}`);
        params.push(updates.tables);
        paramIndex++;
      }

      if (updates.status !== undefined) {
        setClauses.push(`status = $${paramIndex}`);
        params.push(updates.status);
        paramIndex++;
      }

      if (setClauses.length === 0) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'No fields to update',
        });
      }

      const updateSql = `
        UPDATE data_products
        SET ${setClauses.join(', ')}
        WHERE id = $${paramIndex}
        RETURNING
          id, workspace_id, name, description, database_reference, schemas, tables,
          status, state, health_score, published_at, published_agent_fqn,
          created_at, updated_at
      `;
      params.push(id);

      const result = await postgresService.query(
        updateSql,
        params,
        snowflakeUser,
      );

      const product = result.rows[0] as DataProductRow | undefined;

      if (!product) {
        return reply.status(404).send({
          error: 'NOT_FOUND',
          message: 'Data product not found',
        });
      }

      return reply.send(product);
    },
  );

  /**
   * DELETE /data-products/:id
   * Soft delete by setting status to 'archived'. The RLS policy will
   * continue to hide it from lists if you filter by non-archived status.
   * For a true soft delete, we update the state with deleted_at.
   */
  app.delete(
    '/:id',
    async (
      request: FastifyRequest<{ Params: { id: string } }>,
      reply,
    ) => {
      const { id } = request.params;
      const { snowflakeUser } = request.user;

      const result = await postgresService.query(
        `UPDATE data_products
         SET status = 'archived',
             state = state || $1::jsonb
         WHERE id = $2
         RETURNING id`,
        [
          JSON.stringify({ deleted_at: new Date().toISOString() }),
          id,
        ],
        snowflakeUser,
      );

      if (result.rowCount === 0) {
        return reply.status(404).send({
          error: 'NOT_FOUND',
          message: 'Data product not found',
        });
      }

      return reply.send({ success: true });
    },
  );

  /**
   * POST /data-products/:id/share
   * Share a data product with another Snowflake user.
   */
  app.post(
    '/:id/share',
    async (
      request: FastifyRequest<{ Params: { id: string } }>,
      reply,
    ) => {
      const { id } = request.params;
      const { snowflakeUser } = request.user;

      const parseResult = shareDataProductSchema.safeParse(request.body);
      if (!parseResult.success) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Invalid request body',
          details: parseResult.error.flatten().fieldErrors,
        });
      }

      const { shared_with_user, permission } = parseResult.data;

      // Cannot share with yourself
      if (shared_with_user.toUpperCase() === snowflakeUser.toUpperCase()) {
        return reply.status(400).send({
          error: 'VALIDATION_ERROR',
          message: 'Cannot share a data product with yourself',
        });
      }

      // Verify the data product exists and is owned by the user (RLS enforces this)
      const dpCheck = await postgresService.query(
        'SELECT id FROM data_products WHERE id = $1',
        [id],
        snowflakeUser,
      );

      if (dpCheck.rowCount === 0) {
        return reply.status(404).send({
          error: 'NOT_FOUND',
          message: 'Data product not found',
        });
      }

      try {
        const insertResult = await postgresService.query(
          `INSERT INTO data_product_shares
             (data_product_id, shared_with_user, permission, shared_by)
           VALUES ($1, $2, $3, $4)
           RETURNING id, data_product_id, shared_with_user, permission, shared_by, created_at`,
          [id, shared_with_user, permission, snowflakeUser],
          snowflakeUser,
        );

        const share = insertResult.rows[0] as ShareRow | undefined;

        if (!share) {
          return reply.status(500).send({
            error: 'INTERNAL_ERROR',
            message: 'Failed to create share',
          });
        }

        return reply.status(201).send(share);
      } catch (err: unknown) {
        const pgError = err as { code?: string };
        if (pgError.code === '23505') {
          return reply.status(409).send({
            error: 'CONFLICT',
            message: `Data product is already shared with '${shared_with_user}'`,
          });
        }
        throw err;
      }
    },
  );

  /**
   * DELETE /data-products/:id/share/:shareId
   * Remove a share.
   */
  app.delete(
    '/:id/share/:shareId',
    async (
      request: FastifyRequest<{ Params: { id: string; shareId: string } }>,
      reply,
    ) => {
      const { id, shareId } = request.params;
      const { snowflakeUser } = request.user;

      const result = await postgresService.query(
        `DELETE FROM data_product_shares
         WHERE id = $1 AND data_product_id = $2
         RETURNING id`,
        [shareId, id],
        snowflakeUser,
      );

      if (result.rowCount === 0) {
        return reply.status(404).send({
          error: 'NOT_FOUND',
          message: 'Share not found',
        });
      }

      return reply.send({ success: true });
    },
  );

  /**
   * POST /data-products/:id/acknowledge-quality
   * Acknowledge a data quality report so the user can proceed.
   */
  app.post(
    '/:id/acknowledge-quality',
    async (
      request: FastifyRequest<{ Params: { id: string } }>,
      reply,
    ) => {
      const { id } = request.params;
      const { snowflakeUser } = request.user;

      // Find the latest unacknowledged quality check for this data product
      const result = await postgresService.query(
        `UPDATE data_quality_checks
         SET acknowledged = true,
             acknowledged_by = $1,
             acknowledged_at = now()
         WHERE data_product_id = $2
           AND acknowledged = false
         RETURNING id`,
        [snowflakeUser, id],
        snowflakeUser,
      );

      if (result.rowCount === 0) {
        return reply.status(404).send({
          error: 'NOT_FOUND',
          message: 'No pending quality check found for this data product',
        });
      }

      return reply.send({ success: true });
    },
  );
}
