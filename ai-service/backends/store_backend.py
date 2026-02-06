"""PostgreSQL-backed durable persistence for completed sessions and metadata.

Handles long-term storage of agent conversations, data products, business
requirements, semantic views, and audit logs. Uses async connection pooling
and row-level security for workspace isolation.
"""

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import asyncpg

from services import postgres as pg_service


@dataclass
class PostgresStoreBackend:
    """Durable persistence layer backed by PostgreSQL."""

    pool: asyncpg.Pool

    async def get_data_product(
        self,
        data_product_id: str,
        current_user: str | None = None,
    ) -> dict[str, Any] | None:
        """Load a data product record by ID."""
        rows = await pg_service.query(
            self.pool,
            "SELECT * FROM data_products WHERE id = $1::uuid",
            data_product_id,
            current_user=current_user,
        )
        if not rows:
            return None
        return dict(rows[0])

    async def save_data_product_state(
        self,
        data_product_id: str,
        state: dict[str, Any],
        current_user: str | None = None,
    ) -> None:
        """Update the state JSONB column on a data product."""
        await pg_service.execute(
            self.pool,
            """
            UPDATE data_products
            SET state = $1::jsonb, updated_at = NOW()
            WHERE id = $2::uuid
            """,
            json.dumps(state),
            data_product_id,
            current_user=current_user,
        )

    async def save_business_requirements(
        self,
        data_product_id: str,
        content: dict[str, Any],
        created_by: str,
    ) -> str:
        """Insert or update a business requirements document. Returns the BRD ID."""
        brd_id = str(uuid4())
        await pg_service.execute(
            self.pool,
            """
            INSERT INTO business_requirements (id, data_product_id, content, created_by, created_at)
            VALUES ($1::uuid, $2::uuid, $3::jsonb, $4, NOW())
            ON CONFLICT (data_product_id) DO UPDATE
            SET content = EXCLUDED.content,
                created_by = EXCLUDED.created_by,
                created_at = NOW()
            """,
            brd_id,
            data_product_id,
            json.dumps(content),
            created_by,
        )
        return brd_id

    async def save_semantic_view(
        self,
        data_product_id: str,
        yaml_content: str,
        created_by: str,
    ) -> str:
        """Insert or update a semantic view record. Returns the semantic view ID."""
        sv_id = str(uuid4())
        await pg_service.execute(
            self.pool,
            """
            INSERT INTO semantic_views (id, data_product_id, yaml_content, created_by, created_at)
            VALUES ($1::uuid, $2::uuid, $3, $4, NOW())
            ON CONFLICT (data_product_id) DO UPDATE
            SET yaml_content = EXCLUDED.yaml_content,
                created_by = EXCLUDED.created_by,
                created_at = NOW()
            """,
            sv_id,
            data_product_id,
            yaml_content,
            created_by,
        )
        return sv_id

    async def write_audit_log(
        self,
        workspace_id: str,
        action_type: str,
        details: dict[str, Any],
        user_name: str,
    ) -> str:
        """Append an entry to the audit log. Returns the log entry ID."""
        log_id = str(uuid4())
        await pg_service.execute(
            self.pool,
            """
            INSERT INTO audit_logs (id, workspace_id, action_type, details, user_name, created_at)
            VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5, NOW())
            """,
            log_id,
            workspace_id,
            action_type,
            json.dumps(details),
            user_name,
        )
        return log_id
