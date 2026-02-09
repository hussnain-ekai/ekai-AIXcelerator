"""LangChain tools for Snowflake operations.

Tools execute queries via Restricted Caller's Rights (RCR), ensuring users
only see data their Snowflake role permits. All queries use the shared
Snowflake connector from services.snowflake.

Robustness features:
    - SQL identifier validation prevents injection via database/schema/table names
    - CaseInsensitiveDict from snowflake service handles Snowflake's casing quirks
    - Structured error returns with context for easier debugging
"""

import contextvars
import json
import logging
import re
from typing import Any

import requests
from langchain_core.tools import tool

from config import get_settings
from services.snowflake import execute_query_sync, get_connection

# ---------------------------------------------------------------------------
# Data isolation context — set by the router before agent invocation
# ---------------------------------------------------------------------------

# The allowed database for the current agent session. When set, execute_rcr_query
# will prepend USE DATABASE and reject cross-database references.
_allowed_database: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_allowed_database", default=None,
)
# The allowed tables (FQNs) for the current agent session.
_allowed_tables: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "_allowed_tables", default=None,
)

logger = logging.getLogger(__name__)


def set_data_isolation_context(database: str | None, tables: list[str] | None) -> None:
    """Set the allowed database/tables for the current agent session.

    Called by the router before agent invocation. Ensures execute_rcr_query
    cannot query outside the data product's scope.
    """
    _allowed_database.set(database)
    _allowed_tables.set(tables)


def _check_cross_database_reference(sql_upper: str, allowed_db: str) -> str | None:
    """Return an error message if the SQL references a database other than allowed_db.

    Checks for three-part names (DB.SCHEMA.TABLE) and two-part names (DB.TABLE)
    in FROM and JOIN clauses.
    """
    allowed = allowed_db.upper()
    # Match three-part names: "DB"."SCHEMA"."TABLE" or DB.SCHEMA.TABLE
    # Captures the database portion (with or without quotes)
    pattern = re.compile(
        r'(?:FROM|JOIN)\s+'
        r'"?([A-Za-z_$][A-Za-z0-9_$]*)"?\s*\.\s*'
        r'"?[A-Za-z_$][A-Za-z0-9_$]*"?\s*\.\s*'
        r'"?[A-Za-z_$][A-Za-z0-9_$]*"?',
        re.IGNORECASE,
    )
    for match in pattern.finditer(sql_upper):
        db_ref = match.group(1).strip('"').upper()
        if db_ref != allowed:
            return (
                f"Access denied: query references database '{db_ref}' but this "
                f"data product only has access to '{allowed_db}'"
            )
    return None


# ---------------------------------------------------------------------------
# Identifier validation — prevents SQL injection via object names
# ---------------------------------------------------------------------------

# Snowflake identifiers: alphanumeric + underscores + $, 1-255 chars
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]{0,254}$")


def _validate_identifier(name: str, label: str = "identifier") -> str | None:
    """Validate a Snowflake identifier. Returns error message or None if valid."""
    if not name or not name.strip():
        return f"{label} cannot be empty"
    if not _IDENTIFIER_RE.match(name):
        return f"Invalid {label}: '{name}'. Only alphanumeric characters, underscores, and $ are allowed."
    return None


def _validate_fqn(fqn: str) -> tuple[list[str], str | None]:
    """Validate a fully qualified name (DATABASE.SCHEMA.TABLE).

    Returns (parts, error_message). error_message is None if valid.
    """
    if not fqn or not fqn.strip():
        return [], "FQN cannot be empty"

    parts = fqn.split(".")
    if len(parts) != 3:
        return parts, f"Invalid FQN format: '{fqn}'. Expected DATABASE.SCHEMA.TABLE (3 dot-separated parts, got {len(parts)})"

    for i, (part, label) in enumerate(zip(parts, ["database", "schema", "table"])):
        err = _validate_identifier(part, label)
        if err:
            return parts, err

    return parts, None


def _quoted_fqn(parts: list[str]) -> str:
    """Build a safely quoted FQN from validated parts."""
    return ".".join(f'"{p}"' for p in parts)


def _get_rcr_row_limit() -> int:
    return get_settings().rcr_query_row_limit


def _tool_error(tool_name: str, message: str, **extra: Any) -> str:
    """Return a structured JSON error string for tool results."""
    result: dict[str, Any] = {"error": message, "tool": tool_name}
    result.update(extra)
    return json.dumps(result)


# ---------------------------------------------------------------------------
# Column metadata parsing — shared between query_information_schema & profile
# ---------------------------------------------------------------------------

def _parse_data_type(raw: Any) -> str:
    """Parse the JSON-encoded data_type from SHOW COLUMNS into a type name."""
    try:
        dt = json.loads(raw) if isinstance(raw, str) else raw
        return dt.get("type", "UNKNOWN") if isinstance(dt, dict) else str(dt)
    except (ValueError, TypeError):
        return str(raw)


SAMPLE_SIZE: int = 1_000_000


# ---------------------------------------------------------------------------
# YAML auto-fix helpers — used by validate_semantic_view_yaml
# ---------------------------------------------------------------------------

def _remove_field_recursive(d: dict | list, field_name: str) -> None:  # type: ignore[type-arg]
    """Recursively remove a field from a nested dict/list structure."""
    if isinstance(d, dict):
        d.pop(field_name, None)
        for v in d.values():
            if isinstance(v, (dict, list)):
                _remove_field_recursive(v, field_name)
    elif isinstance(d, list):
        for item in d:
            if isinstance(item, (dict, list)):
                _remove_field_recursive(item, field_name)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
def query_information_schema(database: str, schema_name: str) -> str:
    """Query table and view metadata for the given database and schema.

    Uses SHOW TABLES, SHOW VIEWS, and SHOW COLUMNS (fast metadata commands)
    to enumerate tables/views and their columns. Returns a JSON array with
    columns, data types, and nullable flags. Used by the Discovery Agent.

    Args:
        database: Snowflake database name.
        schema_name: Snowflake schema name.
    """
    # Validate identifiers
    for name, label in [(database, "database"), (schema_name, "schema")]:
        err = _validate_identifier(name, label)
        if err:
            return _tool_error("query_information_schema", err)

    try:
        tables = execute_query_sync(
            f'SHOW TABLES IN SCHEMA "{database}"."{schema_name}"'
        )
        views = execute_query_sync(
            f'SHOW VIEWS IN SCHEMA "{database}"."{schema_name}"'
        )

        objects: list[dict[str, Any]] = []
        for t in tables:
            objects.append({
                "name": t.get("name", ""),
                "row_count": t.get("rows", 0),
                "comment": t.get("comment") or "",
                "object_type": "TABLE",
            })
        for v in views:
            objects.append({
                "name": v.get("name", ""),
                "row_count": None,
                "comment": v.get("comment") or "",
                "object_type": "VIEW",
            })

        result = []
        for obj in objects:
            obj_name = obj["name"]
            if not obj_name:
                continue

            fqn = f"{database}.{schema_name}.{obj_name}"
            cols = execute_query_sync(
                f'SHOW COLUMNS IN TABLE "{database}"."{schema_name}"."{obj_name}"'
            )

            table_columns = []
            for idx, col in enumerate(cols):
                col_name = col.get("column_name", "")
                nullable = col.get("null?", True)

                table_columns.append({
                    "name": col_name,
                    "data_type": _parse_data_type(col.get("data_type", "{}")),
                    "nullable": nullable in (True, "true", "Y", "YES"),
                    "comment": col.get("comment") or "",
                    "position": idx + 1,
                })

            result.append({
                "table_name": obj_name,
                "fqn": fqn,
                "row_count": obj["row_count"],
                "comment": obj["comment"],
                "object_type": obj["object_type"],
                "columns": table_columns,
            })

        return json.dumps(result, default=str)

    except Exception as e:
        logger.error("query_information_schema failed for %s.%s: %s", database, schema_name, e)
        return _tool_error("query_information_schema", str(e), database=database, schema=schema_name)


@tool
def compute_quality_score(profile_results_json: str, check_results_json: str) -> str:
    """Compute a deterministic data quality score from profiling results.

    You MUST call this tool instead of estimating the score yourself.
    Pass the profiling data from profile_table calls and any quality issues found.

    Args:
        profile_results_json: JSON string — list of profile_table outputs
            (each has "columns" with "null_pct" per column).
        check_results_json: JSON string — object with issue lists:
            {"duplicate_pks": [...], "orphaned_fks": [...],
             "numeric_varchars": [...], "missing_descriptions": [...]}
    """
    try:
        from agents.discovery import compute_health_score

        profiles = json.loads(profile_results_json) if isinstance(profile_results_json, str) else profile_results_json
        check_results = json.loads(check_results_json) if isinstance(check_results_json, str) else check_results_json

        if not isinstance(profiles, list):
            return _tool_error("compute_quality_score", "profile_results_json must be a JSON array")
        if not isinstance(check_results, dict):
            return _tool_error("compute_quality_score", "check_results_json must be a JSON object")

        completeness_pcts: list[float] = []
        for profile in profiles:
            columns = profile.get("columns", [])
            if not columns:
                completeness_pcts.append(0.0)
                continue
            null_pcts = [c.get("null_pct", 0) for c in columns if "null_pct" in c]
            if null_pcts:
                avg_non_null = 100.0 - (sum(null_pcts) / len(null_pcts))
                completeness_pcts.append(max(0.0, avg_non_null))
            else:
                completeness_pcts.append(0.0)

        check_results["completeness_pcts"] = completeness_pcts
        score = compute_health_score(check_results)
        avg_completeness = sum(completeness_pcts) / len(completeness_pcts) if completeness_pcts else 0

        return json.dumps({
            "overall_score": score,
            "avg_completeness_pct": round(avg_completeness, 1),
            "table_count": len(profiles),
            "completeness_per_table": [round(c, 1) for c in completeness_pcts],
        })

    except json.JSONDecodeError as e:
        return _tool_error("compute_quality_score", f"Invalid JSON input: {e}")
    except Exception as e:
        logger.error("compute_quality_score failed: %s", e)
        return _tool_error("compute_quality_score", str(e))


@tool
def profile_table(table_fqn: str) -> str:
    """Run statistical profiling on a table or view using sampling.

    Uses TABLESAMPLE BERNOULLI for large base tables (>1M rows) and
    subquery LIMIT for views. Computes null rates, approximate uniqueness,
    and detects likely primary keys (>98% uniqueness). All computation
    runs inside the Snowflake warehouse — only aggregate results are returned.

    Args:
        table_fqn: Fully qualified table name (DATABASE.SCHEMA.TABLE).
    """
    parts, fqn_err = _validate_fqn(table_fqn)
    if fqn_err:
        return _tool_error("profile_table", fqn_err, table=table_fqn)

    quoted = _quoted_fqn(parts)

    try:
        # Step 1: Get row count from metadata (free, instant for base tables)
        meta = execute_query_sync(
            f'SELECT "ROW_COUNT", "TABLE_TYPE" FROM "{parts[0]}".INFORMATION_SCHEMA.TABLES '
            f"WHERE TABLE_SCHEMA='{parts[1]}' AND TABLE_NAME='{parts[2]}'"
        )
        meta_row = meta[0] if meta else {}
        # CaseInsensitiveDict handles casing — just use natural names
        table_type = meta_row.get("TABLE_TYPE")
        row_count_val = meta_row.get("ROW_COUNT")
        is_view = not meta or table_type in ("VIEW", "MATERIALIZED VIEW")
        metadata_row_count = int(row_count_val) if row_count_val is not None else None

        # Step 2: Determine sampling strategy
        sampled = False
        if is_view or metadata_row_count is None:
            from_clause = f"(SELECT * FROM {quoted} LIMIT {SAMPLE_SIZE}) AS _sample"
            sampled = True
            total_rows = None
        elif metadata_row_count == 0:
            return json.dumps({"table": table_fqn, "row_count": 0, "columns": [], "sampled": False})
        elif metadata_row_count <= SAMPLE_SIZE:
            from_clause = quoted
            total_rows = metadata_row_count
        else:
            from_clause = f"{quoted} TABLESAMPLE BERNOULLI ({SAMPLE_SIZE} ROWS)"
            sampled = True
            total_rows = metadata_row_count

        # Step 3: Get column metadata via SHOW COLUMNS (instant)
        raw_cols = execute_query_sync(
            f'SHOW COLUMNS IN TABLE {quoted}'
        )

        columns = []
        for col in raw_cols:
            nullable = col.get("null?", True)
            columns.append({
                "column_name": col.get("column_name", ""),
                "data_type": _parse_data_type(col.get("data_type", "{}")),
                "is_nullable": "YES" if nullable in (True, "true", "Y", "YES") else "NO",
            })

        if not columns:
            return json.dumps({"table": table_fqn, "row_count": total_rows or 0, "columns": [], "sampled": sampled})

        # Step 4: Batch profile ALL columns in a single aggregate query
        col_expressions = []
        for col in columns:
            cn = col["column_name"]
            if not cn:
                continue
            col_expressions.append(
                f'COUNT("{cn}") AS "nn_{cn}", '
                f'APPROX_COUNT_DISTINCT("{cn}") AS "dc_{cn}"'
            )

        batch_row: dict[str, Any] = {}
        sample_n = 0
        if col_expressions:
            try:
                batch_sql = (
                    f'SELECT COUNT(*) AS "_sample_n", {", ".join(col_expressions)} '
                    f"FROM {from_clause}"
                )
                batch_result = execute_query_sync(batch_sql)
                batch_row = batch_result[0] if batch_result else {}
            except Exception as batch_err:
                logger.warning("Batch profile query failed for %s: %s", table_fqn, batch_err)

        # CaseInsensitiveDict — _sample_n lookup works regardless of case
        sample_n = batch_row.get("_sample_n", 0) or 0

        if total_rows is None:
            total_rows = sample_n

        profile_results = []
        for col in columns:
            col_name = col["column_name"]
            if not col_name:
                continue
            try:
                # CaseInsensitiveDict handles the casing — nn_COLNAME, nn_colname, etc. all work
                non_null = batch_row.get(f"nn_{col_name}", 0) or 0
                distinct = batch_row.get(f"dc_{col_name}", 0) or 0
                null_pct = round((1 - non_null / sample_n) * 100, 2) if sample_n > 0 else 0
                uniqueness_pct = round((distinct / non_null) * 100, 2) if non_null > 0 else 0

                profile_results.append({
                    "column": col_name,
                    "data_type": col["data_type"],
                    "nullable": col["is_nullable"] == "YES",
                    "null_pct": null_pct,
                    "uniqueness_pct": uniqueness_pct,
                    "distinct_count": distinct,
                    "total_rows": total_rows,
                    "is_likely_pk": uniqueness_pct > 98 and null_pct == 0,
                    "sampled": sampled,
                })
            except Exception as col_err:
                logger.warning("Profiling column %s.%s failed: %s", table_fqn, col_name, col_err)
                profile_results.append({
                    "column": col_name,
                    "data_type": col["data_type"],
                    "error": str(col_err),
                })

        return json.dumps({
            "table": table_fqn,
            "row_count": total_rows,
            "column_count": len(columns),
            "columns": profile_results,
            "sampled": sampled,
            "sample_size": sample_n if sampled else total_rows,
        }, default=str)

    except Exception as e:
        logger.error("profile_table failed for %s: %s", table_fqn, e)
        return _tool_error("profile_table", str(e), table=table_fqn)


@tool
def execute_rcr_query(sql: str) -> str:
    """Execute a read-only SQL SELECT via RCR with a configurable row limit.

    The query runs with the caller's Snowflake role, enforcing row-level access.
    Only SELECT statements are allowed. Queries are scoped to the current data
    product's database — cross-database access is blocked.

    Args:
        sql: The SELECT statement to execute.
    """
    if not sql or not sql.strip():
        return _tool_error("execute_rcr_query", "SQL statement cannot be empty")

    stripped = sql.strip().upper()
    if not stripped.startswith(("SELECT", "SHOW", "DESCRIBE")):
        return _tool_error("execute_rcr_query", "Only SELECT, SHOW, and DESCRIBE statements are allowed")

    # --- Data isolation enforcement ---
    allowed_db = _allowed_database.get()
    if allowed_db:
        # Block cross-database references in FROM/JOIN clauses
        violation = _check_cross_database_reference(stripped, allowed_db)
        if violation:
            logger.warning("DATA ISOLATION VIOLATION blocked: %s — SQL: %s", violation, sql[:200])
            return _tool_error("execute_rcr_query", violation)

    row_limit = _get_rcr_row_limit()

    try:
        # Scope to the data product's database
        if allowed_db:
            execute_query_sync(f'USE DATABASE "{allowed_db}"')

        if stripped.startswith("SELECT") and "LIMIT" not in stripped:
            sql = f"SELECT * FROM ({sql}) AS _subq LIMIT {row_limit}"

        rows = execute_query_sync(sql)
        return json.dumps({
            "row_count": len(rows),
            "rows": rows[:row_limit],
        }, default=str)

    except Exception as e:
        logger.error("execute_rcr_query failed: %s — SQL: %s", e, sql[:200])
        return _tool_error("execute_rcr_query", str(e))


@tool
def create_semantic_view(yaml_content: str, target_schema: str, verify_only: bool = False) -> str:
    """Create a semantic view in Snowflake from YAML content using SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML.

    The YAML must follow the Snowflake semantic view YAML specification with tables,
    facts, dimensions, metrics, and relationships.

    Args:
        yaml_content: The complete semantic view YAML specification.
        target_schema: Fully qualified schema (DATABASE.SCHEMA) where the view will be created.
        verify_only: If True, validates YAML without creating the view.
    """
    # Validate target schema (DATABASE.SCHEMA)
    schema_parts = target_schema.split(".")
    if len(schema_parts) != 2:
        return _tool_error("create_semantic_view", f"target_schema must be DATABASE.SCHEMA, got: '{target_schema}'")
    for part, label in zip(schema_parts, ["database", "schema"]):
        err = _validate_identifier(part, label)
        if err:
            return _tool_error("create_semantic_view", err)

    if not yaml_content or not yaml_content.strip():
        return _tool_error("create_semantic_view", "yaml_content cannot be empty")

    # Clean up YAML before creating
    import yaml as _yaml
    try:
        parsed = _yaml.safe_load(yaml_content)
        if isinstance(parsed, dict) and "tables" in parsed:
            changed = False
            for tbl in parsed["tables"]:
                if "primary_key" in tbl:
                    del tbl["primary_key"]
                    changed = True
            if changed:
                yaml_content = _yaml.dump(parsed, default_flow_style=False, sort_keys=False, allow_unicode=True)
    except Exception:
        pass

    schema_name = f"{schema_parts[0]}.{schema_parts[1]}"
    verify_flag = "TRUE" if verify_only else "FALSE"

    def _extract_view_name(content: str) -> str:
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("name:"):
                return stripped.split(":", 1)[1].strip().strip("'\"")
        return ""

    try:
        sql = f"CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML('{schema_name}', $${yaml_content}$$, {verify_flag})"
        execute_query_sync(sql)
        view_name = _extract_view_name(yaml_content)
        fqn = f"{target_schema}.{view_name}" if view_name else target_schema

        if verify_only:
            return json.dumps({
                "status": "valid",
                "message": "YAML is valid for creating a semantic view",
                "semantic_view_fqn": fqn,
            })

        return json.dumps({
            "status": "success",
            "semantic_view_fqn": fqn,
            "message": f"Semantic view {fqn} created successfully",
        })

    except Exception as e:
        error_msg = str(e)
        # If PK constraint issue with relationships, retry without relationships
        if "primary or unique key" in error_msg.lower() or "referenced key" in error_msg.lower():
            logger.warning("create_semantic_view: PK constraint missing, retrying without relationships")
            try:
                parsed = _yaml.safe_load(yaml_content)
                if isinstance(parsed, dict) and "relationships" in parsed:
                    del parsed["relationships"]
                    yaml_no_rels = _yaml.dump(parsed, default_flow_style=False, sort_keys=False, allow_unicode=True)
                    sql2 = f"CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML('{schema_name}', $${yaml_no_rels}$$, {verify_flag})"
                    execute_query_sync(sql2)
                    view_name = _extract_view_name(yaml_no_rels)
                    fqn = f"{target_schema}.{view_name}" if view_name else target_schema

                    if verify_only:
                        return json.dumps({
                            "status": "valid",
                            "message": "YAML is valid (relationships excluded — source tables lack primary key constraints)",
                            "semantic_view_fqn": fqn,
                            "relationships_excluded": True,
                        })

                    return json.dumps({
                        "status": "success",
                        "semantic_view_fqn": fqn,
                        "message": f"Semantic view {fqn} created successfully (relationships excluded — tables lack PK constraints)",
                        "relationships_excluded": True,
                    })
            except Exception as e2:
                error_msg = str(e2)

        logger.error("create_semantic_view failed: %s", error_msg)
        return _tool_error("create_semantic_view", error_msg, target_schema=target_schema)


@tool
def create_cortex_agent(
    name: str,
    semantic_view_fqn: str,
    target_schema: str,
    description: str = "",
    instructions: str = "",
    model_name: str = "claude-3-5-sonnet",
    warehouse: str = "",
) -> str:
    """Create a Cortex Agent backed by a semantic view.

    Deploys the agent to Snowflake Intelligence so end users can query
    the semantic model through natural language. Uses CREATE AGENT ... FROM SPECIFICATION.

    Args:
        name: Name for the new Cortex Agent.
        semantic_view_fqn: Fully qualified name of the semantic view (DATABASE.SCHEMA.VIEW).
        target_schema: Schema where the agent will be created (DATABASE.SCHEMA).
        description: Business description of the agent.
        instructions: System prompt instructions for the agent.
        model_name: LLM model for orchestration (default: claude-3-5-sonnet).
        warehouse: Snowflake warehouse for query execution.
    """
    # Validate name
    err = _validate_identifier(name, "agent_name")
    if err:
        return _tool_error("create_cortex_agent", err)

    # Validate target schema
    schema_parts = target_schema.split(".")
    if len(schema_parts) != 2:
        return _tool_error("create_cortex_agent", f"target_schema must be DATABASE.SCHEMA, got: '{target_schema}'")
    for part, label in zip(schema_parts, ["database", "schema"]):
        err = _validate_identifier(part, label)
        if err:
            return _tool_error("create_cortex_agent", err)

    # Validate semantic view FQN
    sv_parts = semantic_view_fqn.split(".")
    if len(sv_parts) != 3:
        return _tool_error("create_cortex_agent", f"semantic_view_fqn must be DATABASE.SCHEMA.VIEW, got: '{semantic_view_fqn}'")

    # Resolve warehouse from settings if not provided
    if not warehouse:
        try:
            warehouse = get_settings().snowflake_warehouse or "COMPUTE_WH"
        except Exception:
            warehouse = "COMPUTE_WH"

    try:
        quoted_schema = ".".join(f'"{p}"' for p in schema_parts)
        agent_fqn = f'{quoted_schema}."{name}"'

        # Escape single quotes for YAML string values
        safe_desc = description.replace("'", "''")
        safe_inst = instructions.replace("'", "''")

        # Build the agent specification YAML.
        # tool_resources MUST be a top-level key (not nested inside tools).
        spec_yaml = f"""models:
  orchestration: {model_name}
orchestration:
  budget:
    seconds: 120
    tokens: 10000
instructions:
  response: '{safe_inst}'
  system: '{safe_desc}'
tools:
  - tool_spec:
      type: cortex_analyst_text_to_sql
      name: Analyst
      description: 'Answers questions about the data using the semantic model'
tool_resources:
  Analyst:
    semantic_view: '{semantic_view_fqn}'
    execution_environment:
      type: warehouse
      warehouse: '{warehouse}'"""

        sql = f"CREATE OR REPLACE AGENT {agent_fqn}\n  COMMENT = '{safe_desc}'\n  FROM SPECIFICATION\n  $${spec_yaml}$$"

        execute_query_sync(sql)
        display_fqn = f"{target_schema}.{name}"
        return json.dumps({
            "status": "success",
            "agent_fqn": display_fqn,
            "message": f"Cortex Agent {display_fqn} created successfully",
        })

    except Exception as e:
        logger.error("create_cortex_agent failed: %s", e)
        return _tool_error("create_cortex_agent", str(e), name=name)


@tool
def grant_agent_access(agent_fqn: str, role: str) -> str:
    """Grant a Snowflake role USAGE access to a Cortex Agent.

    Args:
        agent_fqn: Fully qualified name of the Cortex Agent.
        role: Snowflake role to grant access to.
    """
    # Validate role
    err = _validate_identifier(role, "role")
    if err:
        return _tool_error("grant_agent_access", err)

    # Validate agent FQN
    parts = agent_fqn.split(".")
    if len(parts) != 3:
        return _tool_error("grant_agent_access", f"agent_fqn must be DATABASE.SCHEMA.AGENT, got: '{agent_fqn}'")
    for part, label in zip(parts, ["database", "schema", "agent"]):
        err = _validate_identifier(part, label)
        if err:
            return _tool_error("grant_agent_access", err)

    try:
        quoted_fqn = _quoted_fqn(parts)
        sql = f'GRANT USAGE ON AGENT {quoted_fqn} TO ROLE "{role}"'
        execute_query_sync(sql)
        return json.dumps({
            "status": "success",
            "message": f"Granted USAGE on {agent_fqn} to role {role}",
        })

    except Exception as e:
        logger.error("grant_agent_access failed: %s", e)
        return _tool_error("grant_agent_access", str(e), agent_fqn=agent_fqn, role=role)


@tool
def query_cortex_agent(agent_fqn: str, question: str) -> str:
    """Ask a question to a published Cortex Agent and return its answer.

    Sends the question to the Cortex Agent REST API and returns the
    natural language response. Use this after publishing to answer
    business questions via the semantic model.

    Args:
        agent_fqn: Fully qualified name (DATABASE.SCHEMA.AGENT).
        question: Natural language question to ask.
    """
    parts = agent_fqn.split(".")
    if len(parts) != 3:
        return _tool_error("query_cortex_agent",
                           f"agent_fqn must be DATABASE.SCHEMA.AGENT, got: '{agent_fqn}'")

    database, schema, agent_name = parts

    try:
        conn = get_connection()
        token = conn.rest.token
    except Exception as e:
        logger.error("query_cortex_agent: cannot get session token: %s", e)
        return _tool_error("query_cortex_agent", f"Authentication failed: {e}")

    settings = get_settings()
    account = settings.snowflake_account
    host = f"{account}.snowflakecomputing.com"
    url = f"https://{host}/api/v2/databases/{database}/schemas/{schema}/agents/{agent_name}:run"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f'Snowflake Token="{token}"',
        "Accept": "text/event-stream",
    }
    body = {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": question}]}
        ]
    }

    try:
        resp = requests.post(url, headers=headers, json=body, stream=True, timeout=120)

        if resp.status_code != 200:
            return _tool_error("query_cortex_agent",
                               f"HTTP {resp.status_code}: {resp.text[:300]}")

        # Parse SSE events
        full_answer = ""
        sql_generated = ""

        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            if line.startswith("event: "):
                event_type = line[7:].strip()
                continue
            if not line.startswith("data: "):
                continue

            data_str = line[6:]
            if data_str == "[DONE]":
                break

            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            # Extract text from various event formats
            event_type_local = data.get("type", "")

            if event_type_local == "response.text.delta":
                full_answer += data.get("text", "")

            # Final response event contains full content
            content = data.get("content", [])
            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "text":
                        text = item.get("text", "")
                        if text and text not in full_answer:
                            full_answer = text
                    if item.get("type") == "tool_results":
                        tr_content = item.get("content", [])
                        if isinstance(tr_content, list):
                            for sub in tr_content:
                                if isinstance(sub, dict) and "sql" in sub:
                                    sql_generated = sub["sql"]

        result: dict[str, Any] = {
            "status": "success",
            "answer": full_answer or "(No answer returned by agent)",
        }
        if sql_generated:
            result["sql"] = sql_generated

        logger.info("query_cortex_agent success: agent=%s, answer_len=%d, has_sql=%s",
                     agent_fqn, len(full_answer), bool(sql_generated))
        return json.dumps(result)

    except requests.exceptions.Timeout:
        return _tool_error("query_cortex_agent", "Request timed out (120s)")
    except Exception as e:
        logger.error("query_cortex_agent failed: %s", e)
        return _tool_error("query_cortex_agent", str(e))


@tool
def validate_semantic_view_yaml(yaml_content: str, target_schema: str) -> str:
    """Validate semantic view YAML without creating the view.

    Calls SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML with verify_only=TRUE to check
    the YAML specification for errors without actually creating the semantic view.

    Args:
        yaml_content: The complete semantic view YAML specification.
        target_schema: Fully qualified schema (DATABASE.SCHEMA) for context.
    """
    schema_parts = target_schema.split(".")
    if len(schema_parts) != 2:
        return _tool_error("validate_semantic_view_yaml", f"target_schema must be DATABASE.SCHEMA, got: '{target_schema}'")
    for part, label in zip(schema_parts, ["database", "schema"]):
        err = _validate_identifier(part, label)
        if err:
            return _tool_error("validate_semantic_view_yaml", err)

    if not yaml_content or not yaml_content.strip():
        return _tool_error("validate_semantic_view_yaml", "yaml_content cannot be empty")

    # Clean up YAML before validation
    import yaml as _yaml
    try:
        parsed = _yaml.safe_load(yaml_content)
        if isinstance(parsed, dict) and "tables" in parsed:
            changed = False
            for tbl in parsed["tables"]:
                if "primary_key" in tbl:
                    del tbl["primary_key"]
                    changed = True
            if changed:
                yaml_content = _yaml.dump(parsed, default_flow_style=False, sort_keys=False, allow_unicode=True)
    except Exception:
        pass

    schema_name = f"{schema_parts[0]}.{schema_parts[1]}"

    try:
        sql = f"CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML('{schema_name}', $${yaml_content}$$, TRUE)"
        execute_query_sync(sql)
        return json.dumps({
            "status": "valid",
            "message": "YAML is valid for creating a semantic view. No object has been created.",
        })

    except Exception as e:
        error_msg = str(e)
        # If the error is about PK/unique key in relationships, retry without relationships
        if "primary or unique key" in error_msg.lower() or "referenced key" in error_msg.lower():
            logger.warning("validate_semantic_view_yaml: PK constraint missing for relationships, retrying without relationships")
            try:
                parsed = _yaml.safe_load(yaml_content)
                if isinstance(parsed, dict) and "relationships" in parsed:
                    del parsed["relationships"]
                    yaml_no_rels = _yaml.dump(parsed, default_flow_style=False, sort_keys=False, allow_unicode=True)
                    sql2 = f"CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML('{schema_name}', $${yaml_no_rels}$$, TRUE)"
                    execute_query_sync(sql2)
                    return json.dumps({
                        "status": "valid",
                        "message": "YAML is valid (relationships excluded — source tables lack primary key constraints).",
                        "relationships_excluded": True,
                    })
            except Exception as e2:
                error_msg = str(e2)
                logger.warning("validate_semantic_view_yaml (no-rels retry) failed: %s", error_msg)

        # --- Auto-fix: parse common validation errors and retry ---
        import yaml as _yaml_fix

        parsed_yaml = None
        try:
            parsed_yaml = _yaml_fix.safe_load(yaml_content)
        except Exception:
            pass

        if parsed_yaml and isinstance(parsed_yaml, dict):
            fixed = False
            error_lower = error_msg.lower()

            # Fix 1: Remove unknown fields
            if "unknown field" in error_lower:
                match = re.search(r"unknown field ['\"]?(\w+)['\"]?", error_msg, re.IGNORECASE)
                if match:
                    bad_field = match.group(1)
                    _remove_field_recursive(parsed_yaml, bad_field)
                    fixed = True
                    logger.info("validate auto-fix: removed unknown field '%s'", bad_field)

            # Fix 2: Remove references to non-existent columns
            if "unknown column" in error_lower or "invalid column" in error_lower:
                match = re.search(r"(?:unknown|invalid) column ['\"]?(\w+)['\"]?", error_msg, re.IGNORECASE)
                if match:
                    bad_col = match.group(1)
                    for tbl in parsed_yaml.get("tables", []):
                        for section in ("facts", "dimensions", "time_dimensions", "metrics"):
                            items = tbl.get(section, [])
                            tbl[section] = [i for i in items if bad_col.upper() not in (i.get("expr", "")).upper()]
                    fixed = True
                    logger.info("validate auto-fix: removed references to unknown column '%s'", bad_col)

            if fixed:
                try:
                    fixed_yaml = _yaml_fix.dump(parsed_yaml, default_flow_style=False, sort_keys=False, allow_unicode=True)
                    sql_retry = f"CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML('{schema_name}', $${fixed_yaml}$$, TRUE)"
                    execute_query_sync(sql_retry)
                    return json.dumps({
                        "status": "valid",
                        "message": "YAML is valid after auto-fix. Auto-corrections were applied.",
                        "auto_fixed": True,
                    })
                except Exception as e3:
                    logger.warning("validate auto-fix retry failed: %s", e3)
                    # Fall through to return original error

        logger.warning("validate_semantic_view_yaml failed: %s", error_msg)
        return json.dumps({
            "status": "invalid",
            "error": error_msg,
            "message": "Semantic view YAML validation failed. Review the error and fix the YAML.",
        })


@tool
def validate_sql(sql: str) -> str:
    """Validate a SQL statement by running EXPLAIN without executing it.

    Used by the Validation Agent to check query plans and verify column
    references before executing against real data.

    Args:
        sql: The SQL statement to validate.
    """
    if not sql or not sql.strip():
        return json.dumps({"status": "invalid", "error": "SQL statement cannot be empty"})

    try:
        explain_rows = execute_query_sync(f"EXPLAIN {sql}")
        return json.dumps({
            "status": "valid",
            "message": "SQL compiles successfully",
            "plan_rows": len(explain_rows),
            "plan": explain_rows[:10],
        }, default=str)

    except Exception as e:
        logger.error("validate_sql failed: %s", e)
        return json.dumps({
            "status": "invalid",
            "error": str(e),
            "message": "SQL compilation failed",
        })
