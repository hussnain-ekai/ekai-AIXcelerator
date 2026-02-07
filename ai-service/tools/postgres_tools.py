"""LangChain tools for PostgreSQL application state operations.

Tools manage workspace-scoped application state:
    - Data product CRUD
    - Business requirements persistence
    - Semantic view metadata storage
    - Audit log entries
"""

import json
import logging
from typing import Any
from uuid import uuid4

from langchain.tools import tool

from services import postgres as pg_service

logger = logging.getLogger(__name__)


async def _get_pool() -> Any:
    """Return the global PostgreSQL pool, raising if not initialized."""
    if pg_service._pool is None:
        raise RuntimeError("PostgreSQL pool not initialized. Start the application first.")
    return pg_service._pool


@tool
async def save_workspace_state(data_product_id: str, state: str) -> str:
    """Update the state JSONB column on a data product record.

    Used by agents to persist intermediate state (e.g. discovered tables,
    selected schemas, current phase) between conversation turns.

    Args:
        data_product_id: UUID of the data product.
        state: JSON string representing the new state object.
    """
    pool = await _get_pool()

    parsed_state: dict[str, Any] = json.loads(state)

    sql = """
    UPDATE data_products
    SET state = $1::jsonb,
        updated_at = NOW()
    WHERE id = $2::uuid
    """
    result = await pg_service.execute(pool, sql, json.dumps(parsed_state), data_product_id)

    return json.dumps({"status": "ok", "result": result})


@tool
async def load_workspace_state(data_product_id: str) -> str:
    """Read the current state JSONB from a data product record.

    Args:
        data_product_id: UUID of the data product.
    """
    pool = await _get_pool()

    sql = """
    SELECT state
    FROM data_products
    WHERE id = $1::uuid
    """
    rows = await pg_service.query(pool, sql, data_product_id)

    if not rows:
        return json.dumps({"error": f"Data product not found: {data_product_id}"})

    state = rows[0]["state"]
    return json.dumps(state) if state else json.dumps({})


@tool
async def save_brd(
    data_product_id: str,
    brd_json: str,
    created_by: str,
) -> str:
    """Persist a business requirements document for a data product.

    Creates a new business_requirements row with the provided JSON content.

    Args:
        data_product_id: UUID of the data product.
        brd_json: JSON string containing the structured BRD.
        created_by: Username of the person who created the BRD.
    """
    pool = await _get_pool()
    brd_id = str(uuid4())

    # LLM may send raw text or malformed JSON — normalize to valid JSON string
    try:
        parsed = json.loads(brd_json)
    except (json.JSONDecodeError, TypeError):
        # Wrap raw BRD text in a JSON object
        parsed = {"document": brd_json}
    clean_json = json.dumps(parsed)

    sql = """
    INSERT INTO business_requirements (id, data_product_id, brd_json, created_by, created_at)
    VALUES ($1::uuid, $2::uuid, $3::jsonb, $4, NOW())
    """
    await pg_service.execute(pool, sql, brd_id, data_product_id, clean_json, created_by)

    return json.dumps({"status": "ok", "brd_id": brd_id})


@tool
async def get_latest_brd(data_product_id: str) -> str:
    """Retrieve the most recent BRD for a data product.

    Args:
        data_product_id: UUID of the data product.
    """
    pool = await _get_pool()
    rows = await pg_service.query(
        pool,
        "SELECT brd_json, version FROM business_requirements WHERE data_product_id = $1::uuid ORDER BY version DESC LIMIT 1",
        data_product_id,
    )
    if not rows:
        return json.dumps({"status": "not_found", "message": "No BRD found for this data product"})
    return json.dumps({"status": "ok", "version": rows[0]["version"], "brd_json": rows[0]["brd_json"]})


@tool
async def save_semantic_view(
    data_product_id: str,
    yaml_content: str,
    created_by: str,
) -> str:
    """Persist a semantic view YAML for a data product.

    Creates a new semantic_views row with the YAML content.

    Args:
        data_product_id: UUID of the data product.
        yaml_content: The semantic view YAML string.
        created_by: Username of the person who created the semantic view.
    """
    pool = await _get_pool()
    sv_id = str(uuid4())

    sql = """
    INSERT INTO semantic_views (id, data_product_id, yaml_content, created_by, created_at)
    VALUES ($1::uuid, $2::uuid, $3, $4, NOW())
    """
    await pg_service.execute(pool, sql, sv_id, data_product_id, yaml_content, created_by)

    return json.dumps({"status": "ok", "semantic_view_id": sv_id})


@tool
async def save_quality_report(
    data_product_id: str,
    overall_score: int,
    check_results: str,
    issues: str,
) -> str:
    """Persist a data quality report for a data product.

    Creates a row in the data_quality_checks table. This is REQUIRED after
    running quality checks during discovery.

    Args:
        data_product_id: UUID of the data product.
        overall_score: Health score between 0 and 100.
        check_results: JSON string of detailed per-check results.
        issues: JSON string array of issues found.
    """
    pool = await _get_pool()
    report_id = str(uuid4())

    sql = """
    INSERT INTO data_quality_checks (id, data_product_id, overall_score, check_results, issues)
    VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5::jsonb)
    """
    await pg_service.execute(pool, sql, report_id, data_product_id, overall_score, check_results, issues)

    return json.dumps({"status": "ok", "report_id": report_id, "overall_score": overall_score})


@tool
async def log_agent_action(
    workspace_id: str,
    action_type: str,
    details: str,
    user_name: str,
) -> str:
    """Write an entry to the audit_logs table.

    Records agent actions for compliance and debugging purposes.

    Args:
        workspace_id: UUID of the workspace.
        action_type: Category of action (e.g. 'discovery', 'generation', 'publish').
        details: JSON string with action details.
        user_name: Username of the acting user.
    """
    pool = await _get_pool()
    log_id = str(uuid4())

    sql = """
    INSERT INTO audit_logs (id, workspace_id, action_type, details, user_name, created_at)
    VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5, NOW())
    """
    await pg_service.execute(pool, sql, log_id, workspace_id, action_type, details, user_name)

    return json.dumps({"status": "ok", "log_id": log_id})
