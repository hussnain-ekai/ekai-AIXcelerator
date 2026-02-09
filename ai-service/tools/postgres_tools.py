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
    If the content is JSON (structured output from the generation agent),
    it is automatically assembled into Snowflake-compatible YAML.

    Args:
        data_product_id: UUID of the data product.
        yaml_content: The semantic view YAML string (or JSON structure).
        created_by: Username of the person who created the semantic view.
    """
    # Auto-detect JSON and assemble into YAML
    content = yaml_content.strip()
    if content.startswith("{"):
        try:
            from agents.generation import extract_json_from_text, assemble_semantic_view_yaml
            structure = extract_json_from_text(content)
            if structure and "tables" in structure:
                assembled = assemble_semantic_view_yaml(structure)
                if assembled and len(assembled) > 50:
                    logger.info("save_semantic_view: auto-assembled JSON to YAML (%d chars)", len(assembled))
                    content = assembled
        except Exception as e:
            logger.warning("save_semantic_view: failed to auto-assemble JSON to YAML: %s", e)

    pool = await _get_pool()
    sv_id = str(uuid4())

    sql = """
    INSERT INTO semantic_views (id, data_product_id, yaml_content, created_by, created_at)
    VALUES ($1::uuid, $2::uuid, $3, $4, NOW())
    """
    await pg_service.execute(pool, sql, sv_id, data_product_id, content, created_by)

    return json.dumps({"status": "ok", "semantic_view_id": sv_id})


@tool
async def get_latest_semantic_view(data_product_id: str) -> str:
    """Retrieve the most recent semantic view YAML for a data product.

    Args:
        data_product_id: UUID of the data product.
    """
    pool = await _get_pool()
    rows = await pg_service.query(
        pool,
        "SELECT yaml_content, version, validation_status FROM semantic_views WHERE data_product_id = $1::uuid ORDER BY version DESC LIMIT 1",
        data_product_id,
    )
    if not rows:
        return json.dumps({"status": "not_found", "message": "No semantic view found for this data product"})
    return json.dumps({
        "status": "ok",
        "version": rows[0]["version"],
        "yaml_content": rows[0]["yaml_content"],
        "validation_status": rows[0].get("validation_status"),
    })


@tool
async def update_validation_status(
    data_product_id: str,
    status: str,
    errors: str = "",
) -> str:
    """Update the validation status of the latest semantic view.

    Args:
        data_product_id: UUID of the data product.
        status: New validation status (valid, invalid, pending).
        errors: JSON string of validation errors (empty if valid).
    """
    pool = await _get_pool()

    # Parse errors to ensure valid JSON
    try:
        parsed_errors = json.loads(errors) if errors else []
    except (json.JSONDecodeError, TypeError):
        parsed_errors = [{"message": errors}] if errors else []

    sql = """
    UPDATE semantic_views
    SET validation_status = $1,
        validation_errors = $2::jsonb,
        validated_at = NOW()
    WHERE data_product_id = $3::uuid
    AND version = (
        SELECT MAX(version) FROM semantic_views WHERE data_product_id = $3::uuid
    )
    """
    await pg_service.execute(pool, sql, status, json.dumps(parsed_errors), data_product_id)

    return json.dumps({"status": "ok", "validation_status": status})


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
    data_product_id: str,
    action_type: str,
    details: str,
    user_name: str,
) -> str:
    """Write an entry to the audit_logs table.

    Records agent actions for compliance and debugging purposes.

    Args:
        data_product_id: UUID of the data product (workspace_id is resolved automatically).
        action_type: Category of action (e.g. 'discovery', 'generation', 'publish').
        details: JSON string with action details.
        user_name: Username of the acting user.
    """
    try:
        pool = await _get_pool()
        log_id = str(uuid4())

        # Resolve workspace_id from data_product_id
        ws_rows = await pg_service.query(
            pool,
            "SELECT workspace_id FROM data_products WHERE id = $1::uuid",
            data_product_id,
        )
        workspace_id = str(ws_rows[0]["workspace_id"]) if ws_rows else None

        if not workspace_id:
            logger.warning("log_agent_action: no workspace found for data_product_id %s", data_product_id)
            return json.dumps({"status": "ok", "log_id": log_id, "note": "audit log skipped — workspace not found"})

        sql = """
        INSERT INTO audit_logs (id, workspace_id, data_product_id, action_type, action_details, user_name, created_at)
        VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5::jsonb, $6, NOW())
        """
        await pg_service.execute(pool, sql, log_id, workspace_id, data_product_id, action_type, details, user_name)

        return json.dumps({"status": "ok", "log_id": log_id})
    except Exception as e:
        logger.error("log_agent_action failed: %s", e)
        return json.dumps({"status": "ok", "log_id": str(uuid4()), "note": "audit log skipped due to internal error"})
