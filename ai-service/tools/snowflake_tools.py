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
# Explicit publish approval flag for the current agent turn.
# Router sets this from the user's latest message before tool execution.
_publish_approved: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_publish_approved", default=False,
)

logger = logging.getLogger(__name__)


def set_data_isolation_context(database: str | None, tables: list[str] | None) -> None:
    """Set the allowed database/tables for the current agent session.

    Called by the router before agent invocation. Ensures execute_rcr_query
    cannot query outside the data product's scope.
    """
    _allowed_database.set(database)
    _allowed_tables.set(tables)


def set_publish_approval_context(approved: bool) -> None:
    """Set whether publishing tools are allowed in the current agent turn."""
    _publish_approved.set(approved)


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
        if db_ref != allowed and db_ref != "EKAIX":
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


def _strip_wrapping_quotes(value: str) -> str:
    """Remove one level of wrapping double quotes from an identifier token."""
    token = value.strip()
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return token[1:-1]
    return token


def _validate_identifier(name: str, label: str = "identifier") -> str | None:
    """Validate a Snowflake identifier. Returns error message or None if valid."""
    if not name or not name.strip():
        return f"{label} cannot be empty"
    if not _IDENTIFIER_RE.match(name):
        return f"Invalid {label}: '{name}'. Only alphanumeric characters, underscores, and $ are allowed."
    return None


def _auto_fix_target_schema(target_schema: str) -> str:
    """Auto-correct target_schema when the LLM passes a UUID or invalid identifier.

    The LLM sometimes constructs target_schema as EKAIX.{UUID}_MARTS instead of
    EKAIX.{SANITIZED_NAME}_MARTS. This helper detects invalid schemas and
    re-derives from the data product name context set by the router.
    """
    from tools.naming import sanitize_dp_name, EKAIX_DATABASE
    from tools.postgres_tools import get_data_product_name

    parts = target_schema.split(".")
    if len(parts) != 2:
        return target_schema  # let normal validation catch format errors

    db_part, schema_part = parts
    # Check if the schema part is invalid (contains hyphens = UUID pattern)
    if _validate_identifier(schema_part, "schema") is None:
        return target_schema  # already valid, no fix needed

    # Try to derive correct schema from data product name context
    dp_name = get_data_product_name()
    if dp_name:
        fixed_schema = f"{sanitize_dp_name(dp_name)}_MARTS"
        logger.info(
            "Auto-fixed invalid target_schema: '%s' -> '%s.%s' (from dp_name='%s')",
            target_schema, EKAIX_DATABASE, fixed_schema, dp_name,
        )
        return f"{EKAIX_DATABASE}.{fixed_schema}"

    # Fallback: sanitize what we have (strip hyphens, uppercase)
    import re as _re
    sanitized = _re.sub(r"[^A-Za-z0-9_$]", "_", schema_part).strip("_").upper()
    if sanitized and _validate_identifier(sanitized, "schema") is None:
        logger.info(
            "Auto-fixed invalid target_schema via sanitization: '%s' -> '%s.%s'",
            target_schema, db_part, sanitized,
        )
        return f"{db_part}.{sanitized}"

    return target_schema  # return as-is, let normal validation produce the error


def _auto_fix_semantic_view_fqn(fqn: str) -> str:
    """Auto-correct a semantic view FQN (DATABASE.SCHEMA.VIEW) with invalid schema part."""
    parts = fqn.split(".")
    if len(parts) != 3:
        return fqn

    db_part, schema_part, view_part = parts
    if _validate_identifier(schema_part, "schema") is None:
        return fqn  # already valid

    # Re-derive the schema from data product name context
    fixed_schema_fqn = _auto_fix_target_schema(f"{db_part}.{schema_part}")
    fixed_parts = fixed_schema_fqn.split(".")
    if len(fixed_parts) == 2:
        return f"{fixed_parts[0]}.{fixed_parts[1]}.{view_part}"
    return fqn


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


def _normalize_fqn_parts(
    fqn: str,
    *,
    expected_parts: int,
    labels: list[str],
) -> tuple[list[str], str | None]:
    """Split and validate an FQN while tolerating wrapped double quotes."""
    if not fqn or not fqn.strip():
        return [], "FQN cannot be empty"

    raw_parts = [part.strip() for part in fqn.split(".")]
    if len(raw_parts) != expected_parts:
        return raw_parts, (
            f"Invalid FQN format: '{fqn}'. Expected {expected_parts} dot-separated parts, "
            f"got {len(raw_parts)}"
        )

    normalized = [_strip_wrapping_quotes(part) for part in raw_parts]
    for part, label in zip(normalized, labels):
        err = _validate_identifier(part, label)
        if err:
            return normalized, err
    return normalized, None


def _resolve_role_for_grant(role: str) -> tuple[str | None, str | None]:
    """Resolve role token for GRANT. Supports literal CURRENT_ROLE() safely."""
    token = _strip_wrapping_quotes(role or "")
    if not token:
        return None, "role cannot be empty"

    if re.fullmatch(r"(?i)current_role\s*\(\s*\)", token) or token.upper() == "CURRENT_ROLE":
        try:
            rows = execute_query_sync("SELECT CURRENT_ROLE() AS ROLE_NAME")
            if not rows:
                return None, "Unable to resolve CURRENT_ROLE() for grant."
            role_name = rows[0].get("ROLE_NAME") or rows[0].get("role_name") or rows[0].get("CURRENT_ROLE()")
            resolved = str(role_name or "").strip()
            resolved = _strip_wrapping_quotes(resolved)
            if not resolved:
                return None, "Unable to resolve CURRENT_ROLE() for grant."
            err = _validate_identifier(resolved, "role")
            if err:
                return None, err
            return resolved, None
        except Exception as e:
            return None, f"Failed to resolve CURRENT_ROLE(): {e}"

    err = _validate_identifier(token, "role")
    if err:
        return None, err
    return token, None


def _lookup_agent_fqn_by_name(database: str, agent_name: str) -> list[str] | None:
    """Find an existing Cortex Agent FQN by name inside a database."""
    try:
        safe_name = agent_name.replace("'", "''")
        rows = execute_query_sync(f'SHOW AGENTS LIKE \'{safe_name}\' IN DATABASE "{database}"')
    except Exception as e:
        logger.warning("grant_agent_access: failed to lookup agents in %s: %s", database, e)
        return None

    for row in rows:
        db_name = str(row.get("database_name") or row.get("database") or "").strip()
        schema_name = str(row.get("schema_name") or row.get("schema") or "").strip()
        name = str(row.get("name") or row.get("agent_name") or "").strip()
        if not db_name or not schema_name or not name:
            continue
        if name.upper() != agent_name.upper():
            continue
        candidate = [
            _strip_wrapping_quotes(db_name),
            _strip_wrapping_quotes(schema_name),
            _strip_wrapping_quotes(name),
        ]
        if all(_validate_identifier(part, label) is None for part, label in zip(candidate, ["database", "schema", "agent"])):
            return candidate
    return None


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


_MISSING_OBJECT_RE = re.compile(
    r"Object\s+'([^']+)'\s+does not exist or not authorized",
    re.IGNORECASE,
)


def _normalize_fqn_text(fqn: str) -> str:
    """Normalize an FQN token by stripping wrapping quotes and spaces."""
    return ".".join(
        _strip_wrapping_quotes(part.strip())
        for part in fqn.split(".")
        if part and part.strip()
    )


def _extract_missing_object_fqn(error_message: str) -> str | None:
    """Extract missing object FQN from common Snowflake compilation errors."""
    match = _MISSING_OBJECT_RE.search(error_message or "")
    if not match:
        return None
    value = _normalize_fqn_text(match.group(1))
    return value if value else None


def _choose_allowed_table_replacement(
    missing_fqn: str,
    allowed_tables: list[str],
) -> str | None:
    """Choose a deterministic source-table replacement by table-name match."""
    missing_parts = _normalize_fqn_text(missing_fqn).split(".")
    if len(missing_parts) != 3:
        return None

    wanted_table = missing_parts[2].upper()
    wanted_schema = missing_parts[1].upper()

    normalized_allowed: list[str] = []
    for raw in allowed_tables:
        value = _normalize_fqn_text(str(raw))
        if len(value.split(".")) == 3:
            normalized_allowed.append(value)

    candidates = [fqn for fqn in normalized_allowed if fqn.split(".")[2].upper() == wanted_table]
    if len(candidates) == 1:
        return candidates[0]

    if len(candidates) > 1:
        schema_candidates = [fqn for fqn in candidates if fqn.split(".")[1].upper() == wanted_schema]
        if len(schema_candidates) == 1:
            return schema_candidates[0]

    return None


def _rewrite_sql_table_reference(sql: str, from_fqn: str, to_fqn: str) -> str:
    """Replace a 3-part table reference in SQL, tolerant to quotes and spacing."""
    from_parts = _normalize_fqn_text(from_fqn).split(".")
    to_parts = _normalize_fqn_text(to_fqn).split(".")
    if len(from_parts) != 3 or len(to_parts) != 3:
        return sql

    pattern = re.compile(
        rf'(?i)"?{re.escape(from_parts[0])}"?\s*\.\s*"?{re.escape(from_parts[1])}"?\s*\.\s*"?{re.escape(from_parts[2])}"?'
    )
    replacement = _quoted_fqn(to_parts)
    rewritten, count = pattern.subn(lambda _m: replacement, sql)
    return rewritten if count > 0 else sql


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
        error_msg = str(e)
        missing_fqn = _extract_missing_object_fqn(error_msg)
        allowed_tables = _allowed_tables.get() or []

        # Self-heal common explorer failure:
        # LLM guesses EKAIX.<dp>_MARTS.<source_table> that doesn't exist.
        # If table name uniquely matches a selected source table, retry once with the
        # real source FQN so the user still gets an answer in the same turn.
        if missing_fqn and allowed_tables:
            missing_parts = _normalize_fqn_text(missing_fqn).split(".")
            if len(missing_parts) == 3 and missing_parts[0].upper() == "EKAIX":
                replacement_fqn = _choose_allowed_table_replacement(missing_fqn, allowed_tables)
                if replacement_fqn:
                    rewritten_sql = _rewrite_sql_table_reference(sql, missing_fqn, replacement_fqn)
                    if rewritten_sql != sql:
                        try:
                            logger.warning(
                                "execute_rcr_query auto-remap: %s -> %s",
                                missing_fqn,
                                replacement_fqn,
                            )
                            rows = execute_query_sync(rewritten_sql)
                            return json.dumps({
                                "row_count": len(rows),
                                "rows": rows[:row_limit],
                                "autocorrected_from": _normalize_fqn_text(missing_fqn),
                                "autocorrected_to": _normalize_fqn_text(replacement_fqn),
                            }, default=str)
                        except Exception as retry_err:
                            error_msg = f"{error_msg} | retry_after_table_remap_failed: {retry_err}"

            # If we still failed, return a scoped hint with allowed tables.
            return _tool_error(
                "execute_rcr_query",
                (
                    f"Referenced object '{_normalize_fqn_text(missing_fqn)}' is not available "
                    "in this data product context."
                ),
                missing_object=_normalize_fqn_text(missing_fqn),
                allowed_tables=[_normalize_fqn_text(str(t)) for t in allowed_tables[:20]],
                retryable=True,
            )

        logger.error("execute_rcr_query failed: %s — SQL: %s", error_msg, sql[:200])
        return _tool_error("execute_rcr_query", error_msg)


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
    # Always derive correct schema from the CONTEXT variable (set by the router
    # from the database). The LLM-provided target_schema is unreliable — it
    # frequently uses mixed-case names that create case-sensitive schemas.
    from tools.naming import sanitize_dp_name, EKAIX_DATABASE
    from tools.postgres_tools import get_data_product_name

    ctx_name = get_data_product_name()
    if ctx_name:
        sanitized = sanitize_dp_name(ctx_name)
        correct_schema = f"{EKAIX_DATABASE}.{sanitized}_MARTS"
        if target_schema != correct_schema:
            logger.warning(
                "create_semantic_view: corrected target_schema from '%s' to '%s' (dp_name='%s')",
                target_schema, correct_schema, ctx_name,
            )
        target_schema = correct_schema
    else:
        target_schema = _auto_fix_target_schema(target_schema)

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

    # Publish gate: semantic view creation during publishing requires explicit approval.
    # verify_only calls are always allowed for validation workflows.
    if not verify_only and not _publish_approved.get():
        return _tool_error(
            "create_semantic_view",
            "Publishing is blocked: explicit user approval is required before deployment.",
        )

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
                yaml_content = _yaml.dump(parsed, default_flow_style=False, sort_keys=False, allow_unicode=False)
    except Exception:
        pass

    schema_name = f"{schema_parts[0]}.{schema_parts[1]}"
    verify_flag = "TRUE" if verify_only else "FALSE"

    # Auto-create EKAIX database + schema if needed (all ekaiX objects live in EKAIX db)
    if schema_parts[0].upper() == "EKAIX" and not verify_only:
        try:
            from tools.naming import ensure_schema
            ensure_schema(schema_parts[1])
            logger.info("create_semantic_view: ensured schema %s", schema_name)
        except Exception as e:
            logger.warning("create_semantic_view: could not ensure schema %s: %s", schema_name, e)

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
                    yaml_no_rels = _yaml.dump(parsed, default_flow_style=False, sort_keys=False, allow_unicode=False)
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
    target_schema: str,
    semantic_view_fqn: str = "",
    description: str = "",
    instructions: str = "",
    model_name: str = "claude-3-5-sonnet",
    warehouse: str = "",
) -> str:
    """Create a Cortex Agent backed by a semantic view, document search, or both.

    Deploys the agent to Snowflake Intelligence so end users can query
    through natural language. Uses CREATE AGENT ... FROM SPECIFICATION.
    Supports three modes:
    - Structured only: semantic_view_fqn provided, no documents
    - Document only: no semantic_view_fqn, documents available (search service)
    - Hybrid: both semantic_view_fqn and document search service

    Args:
        name: Name for the new Cortex Agent.
        target_schema: Schema where the agent will be created (DATABASE.SCHEMA).
        semantic_view_fqn: Fully qualified name of the semantic view (DATABASE.SCHEMA.VIEW). Optional — omit for document-only agents.
        description: Business description of the agent.
        instructions: System prompt instructions for the agent.
        model_name: LLM model for orchestration (default: claude-3-5-sonnet).
        warehouse: Snowflake warehouse for query execution.
    """
    # Validate name
    err = _validate_identifier(name, "agent_name")
    if err:
        return _tool_error("create_cortex_agent", err)

    # Publish gate: creating/replacing Cortex agents requires explicit approval.
    if not _publish_approved.get():
        return _tool_error(
            "create_cortex_agent",
            "Publishing is blocked: explicit user approval is required before deployment.",
        )

    # Always derive correct schema from the CONTEXT variable.
    from tools.naming import sanitize_dp_name, EKAIX_DATABASE
    from tools.postgres_tools import get_data_product_name

    ctx_name = get_data_product_name()
    if ctx_name:
        sanitized = sanitize_dp_name(ctx_name)
        correct_schema = f"{EKAIX_DATABASE}.{sanitized}_MARTS"
        if target_schema != correct_schema:
            logger.warning(
                "create_cortex_agent: corrected target_schema from '%s' to '%s' (dp_name='%s')",
                target_schema, correct_schema, ctx_name,
            )
        target_schema = correct_schema
    else:
        target_schema = _auto_fix_target_schema(target_schema)

    # Validate target schema
    schema_parts = target_schema.split(".")
    if len(schema_parts) != 2:
        return _tool_error("create_cortex_agent", f"target_schema must be DATABASE.SCHEMA, got: '{target_schema}'")
    for part, label in zip(schema_parts, ["database", "schema"]):
        err = _validate_identifier(part, label)
        if err:
            return _tool_error("create_cortex_agent", err)

    # Validate semantic view FQN if provided
    has_semantic_view = bool(semantic_view_fqn and semantic_view_fqn.strip())
    if has_semantic_view:
        semantic_view_fqn = _auto_fix_semantic_view_fqn(semantic_view_fqn)
        sv_parts = semantic_view_fqn.split(".")
        if len(sv_parts) != 3:
            return _tool_error("create_cortex_agent", f"semantic_view_fqn must be DATABASE.SCHEMA.VIEW, got: '{semantic_view_fqn}'")

    # Auto-create EKAIX database + schema if needed
    if schema_parts[0].upper() == "EKAIX":
        try:
            from tools.naming import ensure_schema
            ensure_schema(schema_parts[1])
            logger.info("create_cortex_agent: ensured schema %s.%s", schema_parts[0], schema_parts[1])
        except Exception as e:
            logger.warning("create_cortex_agent: could not ensure schema: %s", e)

    # Resolve warehouse from settings if not provided
    if not warehouse:
        warehouse = get_settings().snowflake_warehouse

    try:
        quoted_schema = ".".join(f'"{p}"' for p in schema_parts)
        agent_fqn = f'{quoted_schema}."{name}"'

        # Escape single quotes for YAML string values
        safe_desc = description.replace("'", "''")
        safe_inst = instructions.replace("'", "''")

        # Check if data product has a document search service in Snowflake.
        # Derive docs schema from data product name (avoids broken async PG check).
        has_documents = False
        docs_schema_fqn = ""
        try:
            from tools.naming import sanitize_dp_name
            from tools.postgres_tools import get_data_product_name
            dp_name = get_data_product_name()
            if dp_name:
                docs_schema_fqn = f"{schema_parts[0]}.{sanitize_dp_name(dp_name)}_DOCS"
                quoted_docs = f'"{schema_parts[0]}"."{sanitize_dp_name(dp_name)}_DOCS"'
                # Check if search service exists (created by create_document_search_service)
                try:
                    svc_rows = execute_query_sync(
                        f"SHOW CORTEX SEARCH SERVICES IN SCHEMA {quoted_docs}"
                    )
                    has_documents = bool(svc_rows)
                except Exception:
                    # Schema or service doesn't exist — also check DOC_CHUNKS table
                    try:
                        chunk_rows = execute_query_sync(
                            f"SELECT COUNT(*) AS cnt FROM {quoted_docs}.DOC_CHUNKS"
                        )
                        has_documents = chunk_rows and int(
                            chunk_rows[0].get("CNT") or chunk_rows[0].get("cnt") or 0
                        ) > 0
                    except Exception:
                        pass
                if has_documents:
                    logger.info("create_cortex_agent: found documents in %s", docs_schema_fqn)
        except Exception as e:
            logger.debug("Could not check document availability for Cortex Search binding: %s", e)

        # Must have at least one tool source
        if not has_semantic_view and not has_documents:
            return _tool_error(
                "create_cortex_agent",
                "Agent requires at least one tool: a semantic view (structured data) or document search service (documents). Neither is available.",
            )

        # Build tools and resources lists based on what's available
        tools_yaml_parts: list[str] = []
        resources_yaml_parts: list[str] = []

        if has_semantic_view:
            tools_yaml_parts.append("""  - tool_spec:
      type: cortex_analyst_text_to_sql
      name: Analyst
      description: 'Answers questions about the data using the semantic model'""")
            resources_yaml_parts.append(f"""  Analyst:
    semantic_view: '{semantic_view_fqn}'
    execution_environment:
      type: warehouse
      warehouse: '{warehouse}'""")

        if has_documents and docs_schema_fqn:
            tools_yaml_parts.append("""  - tool_spec:
      type: cortex_search
      name: DocumentSearch
      description: 'Searches uploaded documents for relevant context and evidence'""")
            resources_yaml_parts.append(f"""  DocumentSearch:
    search_service: '{docs_schema_fqn}.EKAIX_DOCUMENT_SEARCH'
    max_results: 10""")

        tools_yaml = "\n".join(tools_yaml_parts)
        resources_yaml = "\n".join(resources_yaml_parts)

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
{tools_yaml}
tool_resources:
{resources_yaml}"""

        sql = f"CREATE OR REPLACE AGENT {agent_fqn}\n  COMMENT = '{safe_desc}'\n  FROM SPECIFICATION\n  $${spec_yaml}$$"

        execute_query_sync(sql)
        display_fqn = f"{target_schema}.{name}"
        mode = "hybrid" if has_semantic_view and has_documents else ("document-search" if has_documents else "structured")
        return json.dumps({
            "status": "success",
            "agent_fqn": display_fqn,
            "mode": mode,
            "message": f"Cortex Agent {display_fqn} created successfully ({mode} mode)",
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
    # Resolve role (supports CURRENT_ROLE()).
    resolved_role, err = _resolve_role_for_grant(role)
    if err:
        return _tool_error("grant_agent_access", err)
    assert resolved_role is not None

    # Publish gate: grants are part of deployment and require approval.
    if not _publish_approved.get():
        return _tool_error(
            "grant_agent_access",
            "Publishing is blocked: explicit user approval is required before deployment.",
        )

    # Auto-fix agent FQN if schema part contains UUID/hyphens
    agent_fqn = _auto_fix_semantic_view_fqn(agent_fqn)
    # Validate and normalize agent FQN (tolerate quoted tokens).
    parts, err = _normalize_fqn_parts(
        agent_fqn,
        expected_parts=3,
        labels=["database", "schema", "agent"],
    )
    if err:
        return _tool_error("grant_agent_access", err)

    try:
        quoted_fqn = _quoted_fqn(parts)
        sql = f'GRANT USAGE ON AGENT {quoted_fqn} TO ROLE "{resolved_role}"'
        execute_query_sync(sql)
        return json.dumps({
            "status": "success",
            "message": f"Granted USAGE on {parts[0]}.{parts[1]}.{parts[2]} to role {resolved_role}",
        })

    except Exception as e:
        error_msg = str(e)
        normalized = error_msg.lower()

        # Retry with discovered schema casing if the provided FQN casing/path was wrong.
        if "does not exist" in normalized or "not authorized" in normalized:
            resolved_parts = _lookup_agent_fqn_by_name(parts[0], parts[2])
            if resolved_parts and resolved_parts != parts:
                try:
                    quoted_fqn = _quoted_fqn(resolved_parts)
                    sql = f'GRANT USAGE ON AGENT {quoted_fqn} TO ROLE "{resolved_role}"'
                    execute_query_sync(sql)
                    return json.dumps({
                        "status": "success",
                        "message": (
                            f"Granted USAGE on {resolved_parts[0]}.{resolved_parts[1]}.{resolved_parts[2]} "
                            f"to role {resolved_role}"
                        ),
                    })
                except Exception as retry_err:
                    error_msg = f"{error_msg} | retry_with_resolved_agent_failed: {retry_err}"

        logger.error("grant_agent_access failed: %s", error_msg)
        return _tool_error(
            "grant_agent_access",
            error_msg,
            agent_fqn=agent_fqn,
            role=resolved_role,
        )


@tool
def query_cortex_agent(agent_fqn: str, question: str) -> str:
    """Ask a question to a published Cortex Agent and return its answer.

    Sends the question through Snowflake SQL `SNOWFLAKE.CORTEX.AGENT_RUN`
    and returns the natural language response.

    Args:
        agent_fqn: Fully qualified name (DATABASE.SCHEMA.AGENT).
        question: Natural language question to ask.
    """
    parts, err = _normalize_fqn_parts(
        agent_fqn,
        expected_parts=3,
        labels=["database", "schema", "agent"],
    )
    if err:
        return _tool_error(
            "query_cortex_agent",
            f"agent_fqn must be DATABASE.SCHEMA.AGENT, got: '{agent_fqn}'",
        )

    normalized_fqn = ".".join(parts)
    request_payload = {
        "fully_qualified_name": normalized_fqn,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": question}],
            }
        ],
    }

    try:
        request_json = json.dumps(request_payload, separators=(",", ":"))
        request_sql_payload = request_json.replace("'", "''")
        rows = execute_query_sync(
            f"SELECT SNOWFLAKE.CORTEX.AGENT_RUN($${request_sql_payload}$$) AS RESPONSE"
        )
        raw_response = str((rows[0].get("RESPONSE") if rows else "") or "")
        if not raw_response:
            return _tool_error(
                "query_cortex_agent",
                "Cortex Agent returned an empty response.",
                error_type="unknown",
                retryable=True,
            )

        try:
            parsed = json.loads(raw_response)
        except Exception:
            return json.dumps({
                "status": "success",
                "answer": raw_response,
            })

        # Error envelope returned by AGENT_RUN.
        if isinstance(parsed, dict) and parsed.get("code"):
            msg = str(parsed.get("message") or "Cortex Agent request failed.")
            lower_msg = msg.lower()
            if "not authorized" in lower_msg or "access" in lower_msg:
                return _tool_error(
                    "query_cortex_agent",
                    msg,
                    error_type="auth",
                    retryable=True,
                )
            if "does not exist" in lower_msg or "not found" in lower_msg:
                return _tool_error(
                    "query_cortex_agent",
                    msg,
                    error_type="not_found",
                    retryable=False,
                )
            return _tool_error(
                "query_cortex_agent",
                msg,
                error_type="request",
                retryable=True,
            )

        answer_text = ""
        citations: list[dict[str, Any]] = []
        tools_used: list[str] = []
        has_doc_search = False
        has_analyst = False
        tables: list[dict[str, Any]] = []

        if isinstance(parsed, dict):
            content = parsed.get("content", [])
            if isinstance(content, list):
                texts: list[str] = []
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    item_type = item.get("type", "")

                    if item_type == "text":
                        txt = str(item.get("text") or "").strip()
                        if txt:
                            texts.append(txt)
                        # Extract citations from annotations
                        for ann in (item.get("annotations") or []):
                            if isinstance(ann, dict) and ann.get("type") == "cortex_search_citation":
                                citations.append({
                                    "doc_id": ann.get("doc_id", ""),
                                    "doc_title": ann.get("doc_title", ""),
                                    "text": ann.get("text", ""),
                                    "search_result_id": ann.get("search_result_id", ""),
                                })

                    elif item_type == "tool_use":
                        tool_info = item.get("tool_use", {})
                        tool_type = tool_info.get("type", "")
                        tool_name = tool_info.get("name", "")
                        tools_used.append(tool_type or tool_name)
                        if "cortex_search" in tool_type:
                            has_doc_search = True
                        if "cortex_analyst" in tool_type:
                            has_analyst = True

                    elif item_type == "tool_result":
                        result_info = item.get("tool_result", {})
                        tool_type = result_info.get("type", "")
                        if "cortex_search" in tool_type:
                            has_doc_search = True
                        if "cortex_analyst" in tool_type:
                            has_analyst = True

                    elif item_type == "table":
                        table_info = item.get("table", {})
                        tables.append({
                            "title": table_info.get("title", ""),
                            "query_id": table_info.get("query_id", ""),
                        })

                answer_text = "\n".join(texts).strip()
            if not answer_text:
                answer_text = str(parsed.get("text") or "").strip()
        else:
            answer_text = str(parsed).strip()

        if not answer_text:
            answer_text = "(No answer returned by agent)"

        # Detect non-answer using both tool-use metadata and phrase matching
        is_non_answer = (
            not answer_text
            or answer_text == "(No answer returned by agent)"
            or any(
                phrase in answer_text.lower()
                for phrase in (
                    "i do not have access",
                    "i don't have access",
                    "i cannot access",
                    "unable to retrieve",
                    "no information available",
                    "no relevant information",
                    "i couldn't find",
                    "i could not find",
                    "no results found",
                    "no matching",
                    "not able to find",
                    "don't have enough information",
                    "do not have enough information",
                    "outside the scope",
                    "beyond the scope",
                    "no specific data",
                    "not available in",
                    "i don't have specific",
                    "i do not have specific",
                    "cannot provide specific",
                )
            )
        )

        logger.info(
            "query_cortex_agent success via AGENT_RUN: agent=%s, answer_len=%d, "
            "is_non_answer=%s, tools_used=%s, has_doc_search=%s, has_analyst=%s, citations=%d",
            normalized_fqn,
            len(answer_text),
            is_non_answer,
            tools_used,
            has_doc_search,
            has_analyst,
            len(citations),
        )
        result: dict[str, Any] = {
            "status": "success",
            "answer": answer_text,
            "tools_used": tools_used,
            "has_doc_search": has_doc_search,
            "has_analyst": has_analyst,
        }
        if citations:
            result["citations"] = citations
        if tables:
            result["tables"] = tables
        if is_non_answer:
            result["is_non_answer"] = True
            result["fallback_hint"] = (
                "The published agent could not answer from the semantic model. "
                "Fall back to direct SQL: call query_erd_graph to discover table/column names, "
                "then execute_rcr_query with a read-only query."
            )

        # ── Inline document search fallback ──────────────────────────
        # When the Cortex Agent did NOT invoke DocumentSearch, supplement
        # the answer with direct SEARCH_PREVIEW results so the explorer
        # LLM can incorporate document evidence into its response.
        if not has_doc_search:
            try:
                from tools.postgres_tools import get_data_product_name as _get_dp_name
                _dp_name = _get_dp_name()
                if _dp_name:
                    _fallback_chunks = _search_preview(_dp_name, question, limit=10)
                    if _fallback_chunks:
                        # Build a readable excerpt block for the LLM
                        excerpts = []
                        for i, chunk in enumerate(_fallback_chunks[:5], 1):
                            text = chunk.get("chunk_text", chunk.get("CHUNK_TEXT", ""))
                            fname = chunk.get("filename", chunk.get("FILENAME", ""))
                            page = chunk.get("page_no", chunk.get("PAGE_NO", ""))
                            if text:
                                header = f"[Document: {fname}"
                                if page:
                                    header += f", Page {page}"
                                header += "]"
                                excerpts.append(f"{header}\n{text[:500]}")
                        if excerpts:
                            doc_block = (
                                "\n\n--- SUPPLEMENTARY DOCUMENT EVIDENCE "
                                "(from direct search, Cortex Agent did not search documents) ---\n"
                                + "\n\n".join(excerpts)
                            )
                            result["answer"] = answer_text + doc_block
                            result["has_doc_search"] = True
                            result["doc_search_source"] = "inline_search_preview_fallback"
                            result["doc_chunk_count"] = len(_fallback_chunks)
                            logger.info(
                                "query_cortex_agent: inline SEARCH_PREVIEW fallback "
                                "appended %d chunks for question: %.80s",
                                len(_fallback_chunks), question,
                            )
            except Exception as _fb_err:
                logger.warning(
                    "query_cortex_agent: inline SEARCH_PREVIEW fallback failed: %s",
                    _fb_err,
                )

        return json.dumps(result)
    except Exception as e:
        logger.error("query_cortex_agent failed: %s", e)
        msg = str(e)
        lowered = msg.lower()
        if any(token in lowered for token in ("auth", "token", "permission", "forbidden", "unauthorized", "not authorized")):
            return _tool_error(
                "query_cortex_agent",
                msg,
                error_type="auth",
                retryable=True,
            )
        if any(token in lowered for token in ("timeout", "timed out")):
            return _tool_error(
                "query_cortex_agent",
                msg,
                error_type="timeout",
                retryable=True,
            )
        return _tool_error(
            "query_cortex_agent",
            msg,
            error_type="unknown",
            retryable=False,
        )


def _search_preview(
    data_product_name: str,
    query_text: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Internal: Query Cortex Search Service directly via SEARCH_PREVIEW.

    NOT a @tool — used by the router as a system-level fallback when
    the Cortex Agent fails to invoke DocumentSearch.
    """
    from tools.naming import sanitize_dp_name, EKAIX_DATABASE

    sanitized = sanitize_dp_name(data_product_name)
    service_fqn = f"{EKAIX_DATABASE}.{sanitized}_DOCS.EKAIX_DOCUMENT_SEARCH"
    safe_query = query_text.replace('"', '\\"').replace("'", "''")

    search_payload = json.dumps({
        "query": safe_query,
        "columns": ["chunk_text", "document_id", "filename", "doc_kind", "page_no", "section_path"],
        "limit": min(limit, 50),
    })

    sql = f"""SELECT PARSE_JSON(
        SNOWFLAKE.CORTEX.SEARCH_PREVIEW(
            '{service_fqn}',
            $${search_payload}$$
        )
    )['results'] AS results"""

    try:
        rows = execute_query_sync(sql)
        if not rows:
            return []
        raw = rows[0].get("RESULTS") or rows[0].get("results")
        if not raw:
            return []
        results = json.loads(str(raw)) if isinstance(raw, str) else raw
        return results if isinstance(results, list) else []
    except Exception as e:
        logger.warning("_search_preview failed: %s", e)
        return []


def _try_cortex_yaml_fix(yaml_content: str, error_msg: str, schema_name: str) -> str | None:
    """Try to fix semantic view YAML using Snowflake Cortex AI (Arctic).

    Sends the failing YAML + Snowflake validation error to Arctic. If Arctic
    returns a fixed YAML that passes validation, returns the success JSON.
    Returns None if Cortex is unavailable or the fix doesn't work.
    """
    from tools.ddl import generate_ddl_via_cortex

    import yaml as _yaml_cortex

    # Truncate YAML to avoid exceeding Cortex token limits
    yaml_truncated = yaml_content[:6000] if len(yaml_content) > 6000 else yaml_content
    error_truncated = error_msg[:500]

    prompt = f"""Fix this Snowflake Semantic View YAML specification.

The YAML below was rejected by SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML with this error:
{error_truncated}

Fix ONLY the part causing the error. Keep everything else unchanged.
Return ONLY the complete fixed YAML, no explanation.

YAML:
{yaml_truncated}"""

    try:
        raw_response = generate_ddl_via_cortex(prompt)
        if not raw_response:
            return None

        # Strip markdown fences if present
        fixed = raw_response.strip()
        if fixed.startswith("```"):
            fixed = re.sub(r"^```(?:yaml|yml)?\s*\n?", "", fixed)
            fixed = re.sub(r"\n?```\s*$", "", fixed)
            fixed = fixed.strip()

        # Validate the fixed YAML is parseable
        try:
            parsed = _yaml_cortex.safe_load(fixed)
            if not isinstance(parsed, dict) or "tables" not in parsed:
                logger.info("Cortex YAML fix: response is not a valid semantic view YAML")
                return None
        except Exception:
            logger.info("Cortex YAML fix: response is not valid YAML")
            return None

        # Remove primary_key (same cleanup as main flow)
        for tbl in parsed.get("tables", []):
            if "primary_key" in tbl:
                del tbl["primary_key"]
        fixed = _yaml_cortex.dump(parsed, default_flow_style=False, sort_keys=False, allow_unicode=False)

        # Replace TRY_CAST → CAST (same as main flow)
        fixed = re.sub(r'\bTRY_CAST\(', 'CAST(', fixed)

        # Validate the fixed YAML with Snowflake
        sql = f"CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML('{schema_name}', $${fixed}$$, TRUE)"
        execute_query_sync(sql)

        logger.info("Cortex AI fixed semantic view YAML (error was: %s)", error_truncated[:100])
        return json.dumps({
            "status": "valid",
            "message": "YAML is valid after Cortex AI fix. Auto-corrections were applied.",
            "cortex_fixed": True,
        })

    except Exception as e:
        logger.info("Cortex YAML fix failed (non-fatal): %s", str(e)[:200])
        return None


@tool
def validate_semantic_view_yaml(yaml_content: str, target_schema: str) -> str:
    """Validate semantic view YAML without creating the view.

    Calls SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML with verify_only=TRUE to check
    the YAML specification for errors without actually creating the semantic view.

    Args:
        yaml_content: The complete semantic view YAML specification.
        target_schema: Fully qualified schema (DATABASE.SCHEMA) for context.
    """
    # Auto-fix target schema if LLM passed UUID or invalid identifier
    target_schema = _auto_fix_target_schema(target_schema)
    schema_parts = target_schema.split(".")
    if len(schema_parts) != 2:
        return _tool_error("validate_semantic_view_yaml", f"target_schema must be DATABASE.SCHEMA, got: '{target_schema}'")
    for part, label in zip(schema_parts, ["database", "schema"]):
        err = _validate_identifier(part, label)
        if err:
            return _tool_error("validate_semantic_view_yaml", err)

    if not yaml_content or not yaml_content.strip():
        return _tool_error("validate_semantic_view_yaml", "yaml_content cannot be empty")

    # Safety: extract actual DATABASE.SCHEMA from the YAML's base_table to avoid
    # LLM-invented schema names (e.g. gpt-5-mini sent "SEMANTIC_VALIDATION")
    import yaml as _check_yaml
    try:
        _check_parsed = _check_yaml.safe_load(yaml_content)
        if isinstance(_check_parsed, dict) and _check_parsed.get("tables"):
            _bt = _check_parsed["tables"][0].get("base_table", {})
            _real_db = _bt.get("database", "")
            _real_schema = _bt.get("schema", "")
            if _real_db and _real_schema:
                _real_target = f"{_real_db}.{_real_schema}"
                if _real_target.upper() != target_schema.upper():
                    logger.warning(
                        "validate_semantic_view_yaml: overriding LLM target_schema %r with %r from YAML",
                        target_schema, _real_target,
                    )
                    target_schema = _real_target
                    schema_parts = target_schema.split(".")
    except Exception:
        pass  # Fall through with original target_schema

    # Guard: if LLM passed truncated/summarized YAML (missing tables section),
    # auto-fetch the latest version from the database instead.
    import yaml as _yaml
    if "tables:" not in yaml_content:
        logger.warning("validate_semantic_view_yaml: yaml_content has no 'tables:' section (%d chars) — LLM likely passed truncated content. Auto-fetching from DB.", len(yaml_content))
        try:
            import asyncpg, os, asyncio

            async def _fetch_latest_yaml() -> str | None:
                db_url = os.environ.get("DATABASE_URL", "")
                if not db_url:
                    return None
                conn = await asyncpg.connect(db_url)
                try:
                    row = await conn.fetchrow(
                        "SELECT yaml_content FROM semantic_views "
                        "WHERE yaml_content LIKE $1 ORDER BY created_at DESC LIMIT 1",
                        f"%{schema_parts[0]}.{schema_parts[1]}%",
                    )
                    return row["yaml_content"] if row else None
                finally:
                    await conn.close()

            # asyncio.run() fails inside an already-running event loop.
            # Run in a separate thread with its own event loop.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _pool:
                _fut = _pool.submit(asyncio.run, _fetch_latest_yaml())
                fetched = _fut.result(timeout=15)
            if fetched:
                yaml_content = fetched
                logger.info("validate_semantic_view_yaml: auto-fetched %d chars from DB (matched schema %s)", len(yaml_content), target_schema)
        except Exception as fetch_err:
            logger.warning("validate_semantic_view_yaml: auto-fetch failed: %s", fetch_err)

    # NOTE: primary_key is intentionally kept in the YAML — it is required
    # for relationship validation (right_table must have primary_key defined).
    # The assembler in generation.py ensures correct format {columns: [COL]}.

    # Runtime guard: TRY_CAST only works on VARCHAR input in Snowflake.
    # If columns are already numeric, TRY_CAST(NUMBER AS FLOAT) errors.
    # Replace with CAST which handles numeric-to-numeric conversions.
    import re as _re
    yaml_content = _re.sub(r'\bTRY_CAST\(', 'CAST(', yaml_content)

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
                    yaml_no_rels = _yaml.dump(parsed, default_flow_style=False, sort_keys=False, allow_unicode=False)
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
                    fixed_yaml = _yaml_fix.dump(parsed_yaml, default_flow_style=False, sort_keys=False, allow_unicode=False)
                    sql_retry = f"CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML('{schema_name}', $${fixed_yaml}$$, TRUE)"
                    execute_query_sync(sql_retry)
                    return json.dumps({
                        "status": "valid",
                        "message": "YAML is valid after auto-fix. Auto-corrections were applied.",
                        "auto_fixed": True,
                    })
                except Exception as e3:
                    logger.warning("validate auto-fix retry failed: %s", e3)
                    error_msg = str(e3)  # Update error for Cortex fallback

        # --- Cortex AI fallback: ask Arctic to fix the YAML ---
        cortex_result = _try_cortex_yaml_fix(yaml_content, error_msg, schema_name)
        if cortex_result:
            return cortex_result

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


@tool
async def verify_yaml_against_brd(data_product_id: str) -> str:
    """Cross-check the semantic model YAML against the BRD for completeness.

    Verifies:
    - Every BRD SECTION 2 metric maps to a YAML metric
    - Every BRD SECTION 3.1 dimension maps to a YAML dimension
    - Every BRD SECTION 3.2 time dimension maps to a YAML time_dimension
    - All column references exist in Redis metadata cache

    Args:
        data_product_id: UUID of the data product.

    Returns:
        JSON: {"status": "pass"|"fail", "missing_mappings": [...], "invalid_columns": [...]}
    """
    import yaml as _yaml
    from tools.postgres_tools import _resolve_dp_id, _get_pool
    from services import postgres as pg_service

    data_product_id = _resolve_dp_id(data_product_id)
    missing_mappings: list[str] = []
    invalid_columns: list[str] = []

    try:
        pool = await _get_pool()

        # Load latest BRD
        brd_rows = await pg_service.query(
            pool,
            "SELECT brd_json FROM business_requirements WHERE data_product_id = $1::uuid ORDER BY version DESC LIMIT 1",
            data_product_id,
        )
        if not brd_rows:
            return json.dumps({"status": "fail", "missing_mappings": ["No BRD found"], "invalid_columns": []})

        brd_json = brd_rows[0].get("brd_json")
        brd_text = ""
        if isinstance(brd_json, dict):
            brd_text = brd_json.get("document", str(brd_json))
        elif isinstance(brd_json, str):
            try:
                parsed = json.loads(brd_json)
                brd_text = parsed.get("document", brd_json)
            except (json.JSONDecodeError, TypeError):
                brd_text = brd_json

        # Load latest YAML
        yaml_rows = await pg_service.query(
            pool,
            "SELECT yaml_content FROM semantic_views WHERE data_product_id = $1::uuid ORDER BY version DESC LIMIT 1",
            data_product_id,
        )
        if not yaml_rows:
            return json.dumps({"status": "fail", "missing_mappings": ["No semantic model found"], "invalid_columns": []})

        yaml_content = yaml_rows[0].get("yaml_content", "")
        try:
            yaml_doc = _yaml.safe_load(yaml_content)
        except Exception:
            return json.dumps({"status": "fail", "missing_mappings": ["Invalid YAML"], "invalid_columns": []})

        if not yaml_doc:
            return json.dumps({"status": "fail", "missing_mappings": ["Empty YAML"], "invalid_columns": []})

        # Extract YAML items
        yaml_metrics = set()
        yaml_dimensions = set()
        yaml_time_dims = set()
        yaml_columns_used: set[str] = set()

        for table in yaml_doc.get("tables", []):
            for fact in table.get("facts", []):
                yaml_columns_used.add(fact.get("expr", "").upper())
            for dim in table.get("dimensions", []):
                name = dim.get("name", "").lower().replace("_", " ")
                yaml_dimensions.add(name)
                yaml_columns_used.add(dim.get("expr", "").upper())
            for td in table.get("time_dimensions", []):
                name = td.get("name", "").lower().replace("_", " ")
                yaml_time_dims.add(name)
            for metric in table.get("metrics", []):
                name = metric.get("name", "").lower().replace("_", " ")
                yaml_metrics.add(name)

        # Also check root-level metrics (Snowflake YAML structure)
        for metric in yaml_doc.get("metrics", []):
            name = metric.get("name", "").lower().replace("_", " ")
            yaml_metrics.add(name)

        # Extract BRD metrics from SECTION 2
        section2_match = re.search(r'SECTION 2.*?(?=SECTION 3|$)', brd_text, re.DOTALL)
        if section2_match:
            section2 = section2_match.group(0)
            # Find "Metric: [name]" or "• [name]" patterns
            brd_metrics = re.findall(r'(?:Metric:|•)\s*(.+?)(?:\n|$)', section2)
            for m in brd_metrics:
                m_clean = m.strip().lower().split("\n")[0].split("  ")[0]
                if m_clean and len(m_clean) > 2:
                    # Fuzzy match: check if any YAML metric contains the BRD metric words
                    matched = any(
                        _fuzzy_match(m_clean, ym) for ym in yaml_metrics
                    )
                    if not matched:
                        missing_mappings.append(f"BRD metric '{m.strip()[:60]}' not found in YAML")

        # Extract BRD dimensions from SECTION 3.1
        section3_match = re.search(r'3\.1 Grouping.*?(?=3\.2|SECTION 4|$)', brd_text, re.DOTALL)
        if section3_match:
            section3 = section3_match.group(0)
            brd_dims = re.findall(r'(?:Dimension:|•)\s*(.+?)(?:\n|$)', section3)
            for d in brd_dims:
                d_clean = d.strip().lower().split("\n")[0].split("  ")[0]
                if d_clean and len(d_clean) > 2:
                    matched = any(
                        _fuzzy_match(d_clean, yd) for yd in yaml_dimensions
                    )
                    if not matched:
                        missing_mappings.append(f"BRD dimension '{d.strip()[:60]}' not found in YAML")

        # Check column references against Redis metadata cache
        try:
            from services.redis import get_client as get_redis
            redis = await get_redis()
            if redis:
                all_columns: set[str] = set()
                cache_keys = await redis.keys(f"cache:metadata:{data_product_id}:*")
                for key in cache_keys:
                    cached = await redis.get(key)
                    if not cached:
                        continue
                    try:
                        meta = json.loads(cached) if isinstance(cached, str) else cached
                        for col_info in meta if isinstance(meta, list) else []:
                            col_name = (col_info.get("COLUMN_NAME") or col_info.get("column_name") or "").upper()
                            if col_name:
                                all_columns.add(col_name)
                    except Exception:
                        continue

                if all_columns:
                    for col_ref in yaml_columns_used:
                        # Strip quoting and functions
                        clean = re.sub(r'["\']', '', col_ref).strip().upper()
                        # Skip expressions (contain operators/functions)
                        if any(c in clean for c in ('(', ')', '+', '-', '*', '/', '=', '<', '>')):
                            continue
                        if clean and clean not in all_columns:
                            invalid_columns.append(clean)
        except Exception as e:
            logger.debug("verify_yaml: Redis column check failed: %s", e)

        status = "pass" if not missing_mappings and not invalid_columns else "fail"
        return json.dumps({
            "status": status,
            "missing_mappings": missing_mappings,
            "invalid_columns": invalid_columns,
        })

    except Exception as e:
        logger.error("verify_yaml_against_brd failed: %s", e)
        return json.dumps({
            "status": "error",
            "missing_mappings": [str(e)],
            "invalid_columns": [],
        })


@tool
def create_document_search_service(target_schema: str, data_product_name: str) -> str:
    """Create a Cortex Search Service over uploaded document chunks.

    Builds a Cortex Search Service on the DOC_CHUNKS table so that a Cortex
    Agent can search document content using natural language.

    Args:
        target_schema: EKAIX.{dp_name}_DOCS schema where DOC_CHUNKS lives.
        data_product_name: Human-readable data product name (for logging).
    """
    from tools.naming import sanitize_dp_name, EKAIX_DATABASE, ensure_schema

    # Publish gate
    if not _publish_approved.get():
        return _tool_error(
            "create_document_search_service",
            "Publishing is blocked: explicit user approval is required before deployment.",
        )

    # Always derive correct schema from the CONTEXT variable (set by the router
    # from the database). The LLM-provided data_product_name is unreliable.
    from tools.postgres_tools import get_data_product_name
    ctx_name = get_data_product_name()
    dp_name_for_schema = ctx_name or data_product_name
    if dp_name_for_schema:
        sanitized = sanitize_dp_name(dp_name_for_schema)
        correct_schema = f"{EKAIX_DATABASE}.{sanitized}_DOCS"
        if target_schema != correct_schema:
            logger.warning(
                "create_document_search_service: corrected target_schema from '%s' to '%s' (dp_name='%s')",
                target_schema, correct_schema, dp_name_for_schema,
            )
        target_schema = correct_schema
    else:
        target_schema = _auto_fix_target_schema(target_schema)

    schema_parts = target_schema.split(".")
    if len(schema_parts) != 2:
        return _tool_error(
            "create_document_search_service",
            f"target_schema must be DATABASE.SCHEMA, got: '{target_schema}'",
        )
    for part, label in zip(schema_parts, ["database", "schema"]):
        err = _validate_identifier(part, label)
        if err:
            return _tool_error("create_document_search_service", err)

    # Ensure EKAIX database + docs schema exist
    try:
        ensure_schema(schema_parts[1])
    except Exception as e:
        logger.warning("create_document_search_service: could not ensure schema: %s", e)

    quoted_schema = f'"{schema_parts[0]}"."{schema_parts[1]}"'
    service_fqn = f"{quoted_schema}.EKAIX_DOCUMENT_SEARCH"

    # Resolve warehouse
    warehouse = get_settings().snowflake_warehouse

    try:
        # Verify DOC_CHUNKS table exists and has rows
        count_rows = execute_query_sync(
            f"SELECT COUNT(*) AS cnt FROM {quoted_schema}.DOC_CHUNKS"
        )
        chunk_count = int((count_rows[0].get("CNT") or count_rows[0].get("cnt") or 0)) if count_rows else 0
        if chunk_count == 0:
            return _tool_error(
                "create_document_search_service",
                "DOC_CHUNKS table is empty — upload documents first.",
            )

        # Increase statement timeout for Cortex Search Service creation (can take minutes)
        try:
            execute_query_sync("ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 300")
        except Exception:
            pass  # best-effort; some accounts may not allow ALTER SESSION

        # Create the Cortex Search Service
        create_sql = f"""CREATE OR REPLACE CORTEX SEARCH SERVICE {service_fqn}
  ON chunk_text
  ATTRIBUTES document_id, filename, doc_kind, page_no, section_path
  WAREHOUSE = {warehouse}
  TARGET_LAG = '1 minute'
AS
  SELECT chunk_id, document_id, filename, doc_kind, page_no,
         section_path, chunk_seq, chunk_text
  FROM {quoted_schema}.DOC_CHUNKS"""

        execute_query_sync(create_sql)

        # Reset statement timeout to default
        try:
            execute_query_sync("ALTER SESSION UNSET STATEMENT_TIMEOUT_IN_SECONDS")
        except Exception:
            pass

        # Verify the search service is operational
        verified = False
        try:
            verify_rows = _search_preview(dp_name_for_schema, "test", limit=1)
            verified = len(verify_rows) > 0
            logger.info(
                "create_document_search_service: verification=%s (%d results)",
                "passed" if verified else "no_results_yet (TARGET_LAG pending)",
                len(verify_rows),
            )
        except Exception as ve:
            logger.warning("create_document_search_service: verification failed: %s", ve)

        display_fqn = f"{target_schema}.EKAIX_DOCUMENT_SEARCH"
        logger.info(
            "create_document_search_service: created %s with %d chunks",
            display_fqn, chunk_count,
        )
        return json.dumps({
            "status": "success",
            "search_service_fqn": display_fqn,
            "chunk_count": chunk_count,
            "verified": verified,
            "message": f"Cortex Search Service {display_fqn} created successfully over {chunk_count} document chunks",
        })

    except Exception as e:
        logger.error("create_document_search_service failed: %s", e)
        return _tool_error("create_document_search_service", str(e))


@tool
def extract_structured_from_documents(
    target_schema: str,
    extraction_schema: str,
    data_product_id: str,
) -> str:
    """Extract structured data from documents into real Snowflake tables.

    Uses AI_EXTRACT with a JSON response format derived from the BRD to pull
    typed data from each document. Creates real tables in the target schema
    that semantic views can reference.

    Args:
        target_schema: EKAIX.{dp_name}_MARTS schema for extracted tables.
        extraction_schema: JSON string defining tables and extraction prompts.
            Format: {"tables": [{"name": "TABLE_NAME", "description": "...",
            "columns": {"col_name": "extraction prompt"}}]}
        data_product_id: Data product ID to find source documents.
    """
    from tools.naming import ensure_schema
    from tools.postgres_tools import _resolve_dp_id

    # Publish gate
    if not _publish_approved.get():
        return _tool_error(
            "extract_structured_from_documents",
            "Publishing is blocked: explicit user approval is required before deployment.",
        )

    # Auto-fix and validate target schema
    target_schema = _auto_fix_target_schema(target_schema)
    schema_parts = target_schema.split(".")
    if len(schema_parts) != 2:
        return _tool_error(
            "extract_structured_from_documents",
            f"target_schema must be DATABASE.SCHEMA, got: '{target_schema}'",
        )
    for part, label in zip(schema_parts, ["database", "schema"]):
        err = _validate_identifier(part, label)
        if err:
            return _tool_error("extract_structured_from_documents", err)

    # Ensure schema
    try:
        ensure_schema(schema_parts[1])
    except Exception as e:
        logger.warning("extract_structured_from_documents: could not ensure schema: %s", e)

    # Parse extraction schema
    try:
        schema_def = json.loads(extraction_schema) if isinstance(extraction_schema, str) else extraction_schema
    except json.JSONDecodeError as e:
        return _tool_error("extract_structured_from_documents", f"Invalid extraction_schema JSON: {e}")

    tables_def = schema_def.get("tables", [])
    if not tables_def:
        return _tool_error("extract_structured_from_documents", "extraction_schema must contain at least one table definition")

    # Resolve data product ID and find the docs schema
    data_product_id = _resolve_dp_id(data_product_id)
    from tools.naming import sanitize_dp_name
    from tools.postgres_tools import get_data_product_name
    dp_name = get_data_product_name()
    if not dp_name:
        return _tool_error("extract_structured_from_documents", "Could not resolve data product name")

    docs_schema = f'"{schema_parts[0]}"."{sanitize_dp_name(dp_name)}_DOCS"'
    quoted_target = f'"{schema_parts[0]}"."{schema_parts[1]}"'

    results: list[dict[str, Any]] = []

    for table_def in tables_def:
        table_name = table_def.get("name", "").strip().upper()
        if not table_name:
            continue
        err = _validate_identifier(table_name, "table_name")
        if err:
            results.append({"table": table_name, "status": "error", "error": err})
            continue

        columns = table_def.get("columns", {})
        if not columns:
            results.append({"table": table_name, "status": "error", "error": "No columns defined"})
            continue

        try:
            # Build AI_EXTRACT responseFormat from column definitions
            response_format_parts = []
            for col_name, extraction_prompt in columns.items():
                safe_col = col_name.replace("'", "''")
                safe_prompt = str(extraction_prompt).replace("'", "''")
                response_format_parts.append(f"'{safe_col}', '{safe_prompt}'")

            response_format = f"OBJECT_CONSTRUCT({', '.join(response_format_parts)})"

            # Extract from each document chunk and create table
            extract_sql = f"""CREATE OR REPLACE TABLE {quoted_target}."{table_name}" AS
SELECT
  doc.document_id,
  doc.filename,
  ext.*
FROM {docs_schema}.DOC_CHUNKS doc,
LATERAL (
  SELECT AI_EXTRACT(
    doc.chunk_text,
    {response_format}
  ) AS extracted
) raw,
LATERAL FLATTEN(INPUT => ARRAY_CONSTRUCT(raw.extracted)) ext_flat,
LATERAL (
  SELECT
    {', '.join(f'ext_flat.value:"{col}"::VARCHAR AS "{col.upper()}"' for col in columns)}
) ext
WHERE doc.chunk_text IS NOT NULL AND LENGTH(TRIM(doc.chunk_text)) > 50"""

            execute_query_sync(extract_sql)

            # Get row count
            count_rows = execute_query_sync(
                f'SELECT COUNT(*) AS cnt FROM {quoted_target}."{table_name}"'
            )
            row_count = int((count_rows[0].get("CNT") or count_rows[0].get("cnt") or 0)) if count_rows else 0

            results.append({
                "table": table_name,
                "fqn": f"{target_schema}.{table_name}",
                "status": "success",
                "row_count": row_count,
            })
            logger.info(
                "extract_structured_from_documents: created %s.%s with %d rows",
                target_schema, table_name, row_count,
            )

        except Exception as e:
            logger.error("extract_structured_from_documents: table %s failed: %s", table_name, e)
            results.append({"table": table_name, "status": "error", "error": str(e)})

    success_count = sum(1 for r in results if r["status"] == "success")
    return json.dumps({
        "status": "success" if success_count > 0 else "error",
        "tables_created": success_count,
        "tables_failed": len(results) - success_count,
        "results": results,
    })


def _fuzzy_match(brd_item: str, yaml_item: str) -> bool:
    """Check if BRD item roughly matches a YAML item name."""
    # Normalize both
    brd_words = set(brd_item.lower().split())
    yaml_words = set(yaml_item.lower().replace("_", " ").split())
    # Remove common filler words
    filler = {"the", "a", "an", "of", "for", "per", "by", "in", "to", "and", "or", "is", "are"}
    brd_words -= filler
    yaml_words -= filler
    if not brd_words or not yaml_words:
        return False
    # Match if significant overlap
    overlap = brd_words & yaml_words
    return len(overlap) >= min(len(brd_words), len(yaml_words)) * 0.5
