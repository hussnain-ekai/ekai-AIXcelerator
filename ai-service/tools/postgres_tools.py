"""LangChain tools for PostgreSQL application state operations.

Tools manage workspace-scoped application state:
    - Data product CRUD
    - Business requirements persistence
    - Semantic view metadata storage
    - Audit log entries
"""

import contextvars
import json
import logging
import re
from typing import Any
from uuid import uuid4

from langchain.tools import tool

from services import postgres as pg_service

logger = logging.getLogger(__name__)

# Context variable for the real data_product_id (set from agent.py before tool execution).
# LLMs sometimes truncate UUIDs — this override ensures tools always use the correct one.
_data_product_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_data_product_id_ctx", default=None
)


def set_data_product_context(data_product_id: str | None) -> None:
    """Set the data_product_id context for the current task."""
    _data_product_id_ctx.set(data_product_id)


# Context variable for the data product NAME (used by naming.py for schema derivation).
_data_product_name_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_data_product_name_ctx", default=None
)


def set_data_product_name_context(name: str | None) -> None:
    """Set the data product name context for the current task."""
    _data_product_name_ctx.set(name)


def get_data_product_name() -> str | None:
    """Return the data product name from the current context."""
    return _data_product_name_ctx.get()


def _resolve_dp_id(llm_provided: str) -> str:
    """Return the contextvar data_product_id if available, else the LLM-provided one."""
    ctx_id = _data_product_id_ctx.get()
    if ctx_id:
        if ctx_id != llm_provided:
            logger.warning(
                "data_product_id mismatch — LLM sent %r, using context %r",
                llm_provided, ctx_id,
            )
        return ctx_id
    return llm_provided


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
async def save_data_description(
    data_product_id: str,
    description_json: str,
    created_by: str,
) -> str:
    """Persist a data description document for a data product.

    Creates a new data_descriptions row with the provided JSON content.

    Args:
        data_product_id: UUID of the data product.
        description_json: JSON string containing the structured data description.
        created_by: Username of the person who created the description.
    """
    data_product_id = _resolve_dp_id(data_product_id)
    pool = await _get_pool()
    dd_id = str(uuid4())

    # LLM may send raw text or malformed JSON — normalize to valid JSON string
    try:
        parsed = json.loads(description_json)
    except (json.JSONDecodeError, TypeError):
        parsed = {"document": description_json}
    clean_json = json.dumps(parsed)

    sql = """
    INSERT INTO data_descriptions (id, data_product_id, description_json, created_by)
    VALUES ($1::uuid, $2::uuid, $3::jsonb, $4)
    """
    await pg_service.execute(pool, sql, dd_id, data_product_id, clean_json, created_by)

    return json.dumps({"status": "ok", "data_description_id": dd_id})


@tool
async def get_latest_data_description(data_product_id: str) -> str:
    """Retrieve the most recent data description for a data product.

    Args:
        data_product_id: UUID of the data product.
    """
    data_product_id = _resolve_dp_id(data_product_id)
    pool = await _get_pool()
    rows = await pg_service.query(
        pool,
        "SELECT description_json, version FROM data_descriptions WHERE data_product_id = $1::uuid ORDER BY version DESC LIMIT 1",
        data_product_id,
    )
    if not rows:
        return json.dumps({"status": "not_found", "message": "No data description found for this data product"})
    return json.dumps({"status": "ok", "version": rows[0]["version"], "description_json": rows[0]["description_json"]})


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
    data_product_id = _resolve_dp_id(data_product_id)
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
    data_product_id = _resolve_dp_id(data_product_id)
    pool = await _get_pool()
    rows = await pg_service.query(
        pool,
        "SELECT brd_json, version FROM business_requirements WHERE data_product_id = $1::uuid ORDER BY version DESC LIMIT 1",
        data_product_id,
    )
    if not rows:
        return json.dumps({"status": "not_found", "message": "No BRD found for this data product"})
    return json.dumps({"status": "ok", "version": rows[0]["version"], "brd_json": rows[0]["brd_json"]})


async def _strip_unnecessary_casts(yaml_str: str, data_product_id: str) -> str:
    """Remove TRY_CAST/CAST on columns that are already the target numeric type.

    Some LLMs (e.g. gpt-5-mini) add TRY_CAST(COL AS NUMERIC) even when the column
    is already NUMBER/FLOAT/REAL. Snowflake errors: "TRY_CAST cannot be used with
    arguments of types NUMBER(38,0) and FLOAT". This uses the Redis metadata cache
    to detect and strip these unnecessary casts.
    """
    import re
    import yaml as _yaml
    from services.redis import get_client as get_redis

    # Universal safety: TRY_CAST only works on VARCHAR input in Snowflake.
    # Always convert to CAST first (works for any type conversion).
    yaml_str = re.sub(r'\bTRY_CAST\(', 'CAST(', yaml_str)

    redis = await get_redis()
    if not redis:
        return yaml_str

    # Build column->data_type map from Redis metadata cache
    col_types: dict[str, str] = {}  # "TABLE.COLUMN" -> data_type
    cache_keys = await redis.keys(f"cache:metadata:{data_product_id}:*")
    for key in cache_keys:
        cached = await redis.get(key)
        if not cached:
            continue
        try:
            import json as _json
            meta = _json.loads(cached) if isinstance(cached, str) else cached
            for col_info in meta if isinstance(meta, list) else []:
                col_name = (col_info.get("COLUMN_NAME") or col_info.get("column_name") or "").upper()
                col_type = (col_info.get("DATA_TYPE") or col_info.get("data_type") or "").upper()
                if col_name and col_type:
                    col_types[col_name] = col_type
        except Exception:
            continue

    if not col_types:
        logger.info("_strip_unnecessary_casts: no column metadata found, skipping")
        return yaml_str

    _NUMERIC_TYPES = {"NUMBER", "FLOAT", "REAL", "DOUBLE", "INTEGER", "INT", "BIGINT",
                      "SMALLINT", "TINYINT", "DECIMAL", "NUMERIC", "FIXED"}

    def _is_numeric_col(col_name: str) -> bool:
        ct = col_types.get(col_name.upper(), "")
        # Handle types like "NUMBER(38,0)" -> "NUMBER"
        base_type = ct.split("(")[0].strip()
        return base_type in _NUMERIC_TYPES

    # Pattern: TRY_CAST(COL_NAME AS NUMERIC/FLOAT/DOUBLE/NUMBER)
    # Also: CAST(COL_NAME AS NUMERIC/FLOAT/DOUBLE/NUMBER)
    pattern = re.compile(
        r'(?:TRY_CAST|CAST)\(\s*([A-Z_][A-Z0-9_]*)\s+AS\s+(?:NUMERIC|FLOAT|DOUBLE|NUMBER|REAL|INTEGER)\s*\)',
        re.IGNORECASE,
    )

    def _replace_cast(match: re.Match) -> str:
        col = match.group(1)
        if _is_numeric_col(col):
            logger.info("_strip_unnecessary_casts: stripped cast on already-numeric column %s", col)
            return col
        return match.group(0)  # Keep the cast if column isn't numeric

    result = pattern.sub(_replace_cast, yaml_str)
    if result != yaml_str:
        logger.info("_strip_unnecessary_casts: cleaned unnecessary casts in YAML")
    return result


def _repair_yaml_description_scalars(yaml_text: str) -> tuple[str, bool]:
    """Quote unquoted description values that contain `:` and break YAML parsing."""
    repaired_lines: list[str] = []
    changed = False

    for line in yaml_text.splitlines():
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]

        if not stripped.lower().startswith("description:"):
            repaired_lines.append(line)
            continue

        _, raw_value = stripped.split(":", 1)
        value = raw_value.strip()
        if not value:
            repaired_lines.append(line)
            continue
        if value.startswith(("'", '"', "|", ">", "{", "[")):
            repaired_lines.append(line)
            continue

        # YAML plain scalars often fail when they contain `: `.
        # Quote these values to keep save_semantic_view resilient.
        if ": " in value:
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            repaired_lines.append(f'{indent}description: "{escaped}"')
            changed = True
            continue

        repaired_lines.append(line)

    return "\n".join(repaired_lines), changed


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

    Production hardening:
    - Dedup guard: skips save if an assembled version already exists within 2 minutes
    - JSON input always routed through template assembler (deterministic YAML)
    - Raw YAML input validated for required structure before saving
    - All YAML validated with yaml.safe_load before persistence

    Args:
        data_product_id: UUID of the data product.
        yaml_content: The semantic view YAML string (or JSON structure).
        created_by: Username of the person who created the semantic view.
    """
    import yaml as _yaml

    data_product_id = _resolve_dp_id(data_product_id)
    content = yaml_content.strip()
    is_json = content.startswith("{")
    logger.info("save_semantic_view: received %d chars, is_json=%s, dp_id=%s, first_100=%s",
                len(content), is_json, data_product_id, repr(content[:100]))

    pool = await _get_pool()

    # ── Dedup guard: if a version was saved very recently (within 2 min),
    # skip this save. Prevents LLM calling save twice in one generation cycle
    # (once with JSON → assembler, once with raw YAML → buggy). ──
    recent_rows = await pg_service.query(
        pool,
        "SELECT id, LENGTH(yaml_content) as len FROM semantic_views "
        "WHERE data_product_id = $1::uuid AND created_at > NOW() - INTERVAL '2 minutes' "
        "ORDER BY version DESC LIMIT 1",
        data_product_id,
    )
    if recent_rows and not is_json:
        # A version was already saved recently (likely from the assembler path).
        # Raw YAML saves are lower quality — skip to keep the assembled version.
        logger.info(
            "save_semantic_view: DEDUP GUARD — skipping raw YAML save, "
            "assembled version already exists (id=%s, %d chars, saved <2min ago)",
            recent_rows[0]["id"], recent_rows[0]["len"],
        )
        return json.dumps({
            "status": "ok",
            "semantic_view_id": str(recent_rows[0]["id"]),
            "note": "Using previously saved assembled version (dedup guard)",
        })

    if is_json:
        try:
            from agents.generation import extract_json_from_text, assemble_semantic_view_yaml, build_table_metadata, _build_fqn_sample_values, build_working_layer_map
            logger.info("save_semantic_view: extracting JSON from text...")
            structure = extract_json_from_text(content)
            if structure and "tables" in structure:
                logger.info("save_semantic_view: building table metadata...")
                meta = await build_table_metadata(data_product_id, structure)
                logger.info("save_semantic_view: building sample values map...")
                sv_map = await _build_fqn_sample_values(data_product_id)
                wl_map = await build_working_layer_map(data_product_id)
                logger.info("save_semantic_view: assembling YAML (meta=%d tables, sv_map=%d, wl=%d)...", len(meta), len(sv_map), len(wl_map))
                assembled = assemble_semantic_view_yaml(structure, table_metadata=meta, sample_values_map=sv_map, working_layer_map=wl_map)
                if assembled and len(assembled) > 50:
                    logger.info("save_semantic_view: auto-assembled JSON to YAML (%d chars, meta=%d tables)", len(assembled), len(meta))
                    content = assembled
                else:
                    logger.warning("save_semantic_view: assembly returned empty/short result (%s chars)", len(assembled) if assembled else 0)
            else:
                logger.warning("save_semantic_view: extract_json_from_text returned no tables structure=%s", bool(structure))
        except Exception as e:
            logger.warning("save_semantic_view: failed to auto-assemble JSON to YAML: %s", e)
    else:
        # Content is raw YAML — apply column quoting and expression cleanup
        try:
            from agents.generation import quote_columns_in_yaml_str
            logger.info("save_semantic_view: applying YAML column quoting...")
            content = await quote_columns_in_yaml_str(content, data_product_id)
            logger.info("save_semantic_view: YAML column quoting done (%d chars)", len(content))
        except Exception as e:
            logger.warning("save_semantic_view: failed to apply YAML column quoting: %s", e)

        # Strip unnecessary TRY_CAST on columns that are already numeric
        try:
            content = await _strip_unnecessary_casts(content, data_product_id)
        except Exception as e:
            logger.warning("save_semantic_view: failed to strip unnecessary casts: %s", e)

    # ── YAML structure validation before saving ──
    try:
        parsed_obj = _yaml.safe_load(content)
    except _yaml.YAMLError as e:
        repaired_content, repaired = _repair_yaml_description_scalars(content)
        if not repaired:
            logger.error("save_semantic_view: YAML parse error: %s", e)
            return json.dumps({"status": "error", "error": f"Invalid YAML syntax: {e}"})
        try:
            parsed_obj = _yaml.safe_load(repaired_content)
            content = repaired_content
            logger.info("save_semantic_view: auto-repaired YAML description quoting issue")
        except _yaml.YAMLError as repaired_err:
            logger.error(
                "save_semantic_view: YAML parse error after auto-repair attempt: %s",
                repaired_err,
            )
            return json.dumps({"status": "error", "error": f"Invalid YAML syntax: {repaired_err}"})

    if not isinstance(parsed_obj, dict):
        return json.dumps({"status": "error", "error": "YAML content is not a valid mapping"})
    if "name" not in parsed_obj:
        return json.dumps({"status": "error", "error": "YAML missing required 'name' field"})
    if "tables" not in parsed_obj or not parsed_obj["tables"]:
        return json.dumps({"status": "error", "error": "YAML missing required 'tables' list"})
    for i, tbl in enumerate(parsed_obj["tables"]):
        if not tbl.get("base_table"):
            return json.dumps({"status": "error", "error": f"Table #{i} missing 'base_table'"})
        bt = tbl["base_table"]
        for key in ("database", "schema", "table"):
            if not bt.get(key):
                return json.dumps({"status": "error", "error": f"Table #{i} base_table missing '{key}'"})

    # Data isolation guard: semantic model can only use selected source tables
    # plus internal EKAIX-managed curated/marts objects.
    try:
        from tools.snowflake_tools import _allowed_tables

        allowed_tables = {t.upper() for t in (_allowed_tables.get() or [])}
        if allowed_tables:
            for i, tbl in enumerate(parsed_obj["tables"]):
                bt = tbl.get("base_table", {})
                db = str(bt.get("database", "")).strip('"').upper()
                schema = str(bt.get("schema", "")).strip('"').upper()
                table = str(bt.get("table", "")).strip('"').upper()
                base_fqn = f"{db}.{schema}.{table}"
                if db != "EKAIX" and base_fqn not in allowed_tables:
                    return json.dumps({
                        "status": "error",
                        "error": (
                            f"Table #{i} base_table '{base_fqn}' is outside the selected "
                            "data product scope."
                        ),
                    })
    except Exception as scope_err:
        logger.warning("save_semantic_view: scope validation skipped due to internal error: %s", scope_err)

    logger.info("save_semantic_view: YAML structure validation passed (%d tables)", len(parsed_obj["tables"]))

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
    data_product_id = _resolve_dp_id(data_product_id)
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
    data_product_id = _resolve_dp_id(data_product_id)
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


def _extract_agent_fqn(details: Any) -> str | None:
    """Extract DATABASE.SCHEMA.OBJECT from action details when available."""
    if isinstance(details, dict):
        for key in ("agent_fqn", "published_agent_fqn", "ai_agent_fqn", "cortex_agent_fqn"):
            value = details.get(key)
            if isinstance(value, str) and value.count(".") == 2:
                return value.strip('"')
        # Fallback: search the serialized payload
        haystack = json.dumps(details)
    elif isinstance(details, str):
        haystack = details
    else:
        haystack = str(details)

    matches = re.findall(r'\b([A-Za-z0-9_]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+)\b', haystack)
    if not matches:
        return None
    for candidate in matches:
        obj_name = candidate.split(".")[-1].upper()
        if "AGENT" in obj_name:
            return candidate
    return matches[0]


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
    data_product_id = _resolve_dp_id(data_product_id)
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
    data_product_id = _resolve_dp_id(data_product_id)
    try:
        pool = await _get_pool()
        log_id = str(uuid4())

        # Normalize details into valid JSONB payload
        try:
            parsed_details = json.loads(details) if isinstance(details, str) else details
        except (json.JSONDecodeError, TypeError):
            parsed_details = {"message": str(details)}
        details_json = json.dumps(parsed_details if parsed_details is not None else {})

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
        await pg_service.execute(pool, sql, log_id, workspace_id, data_product_id, action_type, details_json, user_name)

        # Publishing side-effects: persist canonical published state and metadata.
        if action_type.strip().lower() == "publish":
            agent_fqn = _extract_agent_fqn(parsed_details)
            try:
                await pg_service.execute(
                    pool,
                    """UPDATE data_products
                       SET status = 'published'::data_product_status,
                           published_at = NOW(),
                           published_agent_fqn = COALESCE($1, published_agent_fqn),
                           state = jsonb_set(
                               jsonb_set(COALESCE(state, '{}'::jsonb), '{current_phase}', '"explorer"'::jsonb),
                               '{published}',
                               'true'::jsonb
                           ),
                           updated_at = NOW()
                       WHERE id = $2::uuid""",
                    agent_fqn,
                    data_product_id,
                )
            except Exception as publish_err:
                logger.warning("log_agent_action: failed to persist publish metadata: %s", publish_err)

        return json.dumps({"status": "ok", "log_id": log_id})
    except Exception as e:
        logger.error("log_agent_action failed: %s", e)
        return json.dumps({"status": "ok", "log_id": str(uuid4()), "note": "audit log skipped due to internal error"})


@tool
async def verify_brd_completeness(data_product_id: str) -> str:
    """Verify that the latest BRD is complete and well-formed.

    Checks:
    - All 7 sections present
    - No placeholder text ([TBD], [TODO], etc.)
    - Table references match discovered tables from Redis metadata cache

    Args:
        data_product_id: UUID of the data product.

    Returns:
        JSON: {"status": "pass"|"fail", "issues": [...], "section_count": N}
    """
    data_product_id = _resolve_dp_id(data_product_id)
    issues: list[str] = []

    try:
        pool = await _get_pool()

        # Load latest BRD
        rows = await pg_service.query(
            pool,
            "SELECT brd_json FROM business_requirements WHERE data_product_id = $1::uuid ORDER BY version DESC LIMIT 1",
            data_product_id,
        )
        if not rows:
            return json.dumps({"status": "fail", "issues": ["No BRD found"], "section_count": 0})

        brd_json = rows[0].get("brd_json")
        brd_text = ""
        if isinstance(brd_json, dict):
            brd_text = brd_json.get("document", str(brd_json))
        elif isinstance(brd_json, str):
            try:
                parsed = json.loads(brd_json)
                brd_text = parsed.get("document", brd_json)
            except (json.JSONDecodeError, TypeError):
                brd_text = brd_json

        # Check sections
        section_markers = {
            "SECTION 1:": "Executive Summary",
            "SECTION 2:": "Metrics and Calculations",
            "SECTION 3:": "Dimensions and Filters",
            "SECTION 4:": "Table Relationships",
            "SECTION 5:": "Data Requirements",
            "SECTION 6:": "Data Quality Rules",
            "SECTION 7:": "Sample Questions",
        }
        section_count = 0
        for marker, name in section_markers.items():
            if marker in brd_text:
                section_count += 1
            else:
                issues.append(f"Missing {name} ({marker})")

        # Check for placeholder text
        placeholders = ["[TBD]", "[TODO]", "[PLACEHOLDER]", "[FILL IN]", "[INSERT"]
        for p in placeholders:
            count = brd_text.upper().count(p.upper())
            if count > 0:
                issues.append(f"Found {count} instance(s) of placeholder '{p}'")

        # Check table references against Redis metadata cache
        try:
            from services.redis import get_client as get_redis
            redis = await get_redis()
            if redis:
                cache_keys = await redis.keys(f"cache:metadata:{data_product_id}:*")
                discovered_tables = set()
                for key in cache_keys:
                    # Key format: cache:metadata:{dp_id}:{DB}.{SCHEMA}.{TABLE}
                    parts = key.split(":")
                    if len(parts) >= 4:
                        fqn = parts[3] if isinstance(parts[3], str) else parts[3].decode()
                        table_name = fqn.split(".")[-1]
                        discovered_tables.add(table_name.upper())

                if discovered_tables:
                    # Extract table names mentioned in SECTION 5
                    import re
                    section5_match = re.search(r'SECTION 5.*?(?=SECTION 6|---END|$)', brd_text, re.DOTALL)
                    if section5_match:
                        section5 = section5_match.group(0)
                        # Look for table name patterns (ALL_CAPS words)
                        brd_tables = set(re.findall(r'\b([A-Z][A-Z0-9_]{2,})\b', section5))
                        # Filter to likely table names (not generic words)
                        generic = {"SECTION", "TABLE", "PURPOSE", "FIELDS", "DATA", "PRODUCT", "QUALITY",
                                   "RULES", "SAMPLE", "QUESTIONS", "METRICS", "DIMENSIONS", "FILTERS",
                                   "REQUIREMENTS", "EXECUTIVE", "SUMMARY", "RELATIONSHIPS", "THE",
                                   "FOR", "AND", "NOT", "ALL", "BRD", "BEGIN", "END", "NUMBER",
                                   "VARCHAR", "DATE", "BOOLEAN", "TIMESTAMP"}
                        brd_tables -= generic
                        for t in brd_tables:
                            if t not in discovered_tables and not any(t in dt for dt in discovered_tables):
                                issues.append(f"BRD references table '{t}' not found in discovered tables")
        except Exception as e:
            logger.debug("verify_brd: Redis check failed: %s", e)

        status = "pass" if not issues else "fail"
        return json.dumps({"status": status, "issues": issues, "section_count": section_count})

    except Exception as e:
        logger.error("verify_brd_completeness failed: %s", e)
        return json.dumps({"status": "error", "issues": [str(e)], "section_count": 0})
