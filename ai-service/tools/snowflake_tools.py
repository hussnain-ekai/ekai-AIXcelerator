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

from langchain_core.tools import tool

from config import get_settings
from services.snowflake import execute_query_sync

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
def create_semantic_view(yaml_content: str, view_name: str, target_schema: str) -> str:
    """Create or replace a semantic view in Snowflake from YAML content.

    The YAML must follow the Snowflake semantic view specification with fully
    qualified table names.

    Args:
        yaml_content: The complete semantic view YAML specification.
        view_name: Name for the semantic view.
        target_schema: Fully qualified schema (DATABASE.SCHEMA) where the view will be created.
    """
    # Validate view name
    err = _validate_identifier(view_name, "view_name")
    if err:
        return _tool_error("create_semantic_view", err)

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

    try:
        quoted_schema = ".".join(f'"{p}"' for p in schema_parts)
        sql = f'CREATE OR REPLACE SEMANTIC VIEW {quoted_schema}."{view_name}" AS $${yaml_content}$$'

        execute_query_sync(sql)
        fqn = f"{target_schema}.{view_name}"
        return json.dumps({
            "status": "success",
            "semantic_view_fqn": fqn,
            "message": f"Semantic view {fqn} created successfully",
        })

    except Exception as e:
        logger.error("create_semantic_view failed: %s", e)
        return _tool_error("create_semantic_view", str(e), view_name=view_name)


@tool
def create_cortex_agent(
    name: str,
    semantic_view_fqn: str,
    target_schema: str,
    description: str = "",
    instructions: str = "",
) -> str:
    """Create a Cortex Agent backed by a semantic view.

    Deploys the agent to Snowflake Intelligence so end users can query
    the semantic model through natural language.

    Args:
        name: Name for the new Cortex Agent.
        semantic_view_fqn: Fully qualified name of the semantic view.
        target_schema: Schema where the agent will be created.
        description: Business description of the agent.
        instructions: System prompt instructions for the agent.
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

    try:
        quoted_schema = ".".join(f'"{p}"' for p in schema_parts)
        agent_fqn = f'{quoted_schema}."{name}"'
        # Escape single quotes in description/instructions for SQL string literals
        safe_desc = description.replace("'", "''")
        safe_inst = instructions.replace("'", "''")

        sql = f"""CREATE CORTEX AGENT {agent_fqn}
        WITH
          SEMANTIC_VIEW = '{semantic_view_fqn}'
          DESCRIPTION = '{safe_desc}'
          INSTRUCTIONS = '{safe_inst}'
          MODEL = 'claude-3-5-sonnet'"""

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
        sql = f'GRANT USAGE ON CORTEX AGENT {quoted_fqn} TO ROLE "{role}"'
        execute_query_sync(sql)
        return json.dumps({
            "status": "success",
            "message": f"Granted USAGE on {agent_fqn} to role {role}",
        })

    except Exception as e:
        logger.error("grant_agent_access failed: %s", e)
        return _tool_error("grant_agent_access", str(e), agent_fqn=agent_fqn, role=role)


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
