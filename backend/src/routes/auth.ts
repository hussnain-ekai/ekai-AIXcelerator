import type { FastifyInstance, FastifyRequest } from 'fastify';

import { postgresService } from '../services/postgresService.js';

export async function authRoutes(app: FastifyInstance): Promise<void> {
  /**
   * GET /auth/user
   * Returns the current authenticated user. Creates the workspace on first access (upsert).
   */
  app.get('/user', async (request: FastifyRequest, reply) => {
    const { snowflakeUser, displayName, role, account } = request.user;

    // Upsert workspace: create one if it doesn't exist for this Snowflake user
    const upsertResult = await postgresService.query(
      `INSERT INTO workspaces (snowflake_user, display_name)
       VALUES ($1, $2)
       ON CONFLICT (snowflake_user) DO UPDATE SET display_name = EXCLUDED.display_name
       RETURNING id, snowflake_user, display_name, settings, created_at`,
      [snowflakeUser, displayName],
      snowflakeUser,
    );

    const workspace = upsertResult.rows[0] as
      | {
          id: string;
          snowflake_user: string;
          display_name: string;
          settings: Record<string, unknown>;
          created_at: string;
        }
      | undefined;

    if (!workspace) {
      return reply.status(500).send({
        error: 'INTERNAL_ERROR',
        message: 'Failed to create or retrieve workspace',
      });
    }

    return reply.send({
      username: snowflakeUser,
      display_name: displayName,
      role,
      account,
      workspace_id: workspace.id,
      settings: workspace.settings,
    });
  });
}
