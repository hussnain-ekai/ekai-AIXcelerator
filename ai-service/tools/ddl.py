"""Shared DDL generation infrastructure for all Snowflake object creation.

Consolidates the two-tier DDL generation pattern used by both the
transformation agent (VIEWs in _CURATED schema) and the modeling agent
(Dynamic Tables in _MARTS schema).

Two-tier approach:
    - Tier 1: Snowflake Cortex AI (Arctic) generates DDL natively inside Snowflake
    - Tier 2: Template-based assembler with _safe_cast rules (fallback)

Robustness:
    - EXPLAIN validation catches SQL compilation errors before execution
    - Column case quoting handles Snowflake's case-sensitivity rules
    - FQN auto-resolution fixes bare/partial table names from the LLM
"""

import json
import logging
import re
from typing import Any

from config import get_settings
from services.snowflake import get_connection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Generic tool error helper
# ---------------------------------------------------------------------------


def tool_error(tool_name: str, message: str, **extra: Any) -> str:
    """Return a structured JSON error string for tool results."""
    result: dict[str, Any] = {"error": message, "tool": tool_name}
    result.update(extra)
    return json.dumps(result)


# ---------------------------------------------------------------------------
# Type-checking helpers
# ---------------------------------------------------------------------------


def is_text_type(dtype: str) -> bool:
    """Check if a Snowflake type is text-based."""
    upper = dtype.upper().split("(")[0].strip()
    return upper in (
        "VARCHAR", "TEXT", "STRING", "CHAR", "CHARACTER",
        "NVARCHAR", "NCHAR",
    )


def is_numeric_type(dtype: str) -> bool:
    """Check if a Snowflake type is numeric."""
    upper = dtype.upper().split("(")[0].strip()
    return upper in (
        "NUMBER", "NUMERIC", "DECIMAL", "INT", "INTEGER", "BIGINT",
        "SMALLINT", "TINYINT", "FLOAT", "FLOAT4", "FLOAT8", "DOUBLE",
        "REAL", "BYTEINT",
    )


def is_date_type(dtype: str) -> bool:
    """Check if a Snowflake type is date/timestamp."""
    upper = dtype.upper().split("(")[0].strip()
    return upper in (
        "DATE", "DATETIME", "TIMESTAMP", "TIMESTAMP_NTZ",
        "TIMESTAMP_LTZ", "TIMESTAMP_TZ",
    )


def safe_cast(quoted_col: str, source_type: str, target_type: str) -> str:
    """Generate the correct Snowflake cast expression for a source->target type pair.

    TRY_CAST only works for VARCHAR->numeric. All other conversions use
    direct :: cast or Snowflake's TRY_TO_* functions.
    """
    src_text = is_text_type(source_type)
    src_numeric = is_numeric_type(source_type)
    tgt_upper = target_type.upper().split("(")[0].strip()

    # TEXT -> numeric: TRY_CAST is correct
    if src_text and is_numeric_type(target_type):
        return f"TRY_CAST({quoted_col} AS {target_type})"

    # TEXT -> DATE: use TRY_TO_DATE
    if src_text and tgt_upper == "DATE":
        return f"TRY_TO_DATE({quoted_col})"

    # TEXT -> TIMESTAMP variants: use TRY_TO_TIMESTAMP
    if src_text and tgt_upper.startswith("TIMESTAMP"):
        return f"TRY_TO_TIMESTAMP({quoted_col})"

    # TEXT -> BOOLEAN: use TRY_TO_BOOLEAN
    if src_text and tgt_upper == "BOOLEAN":
        return f"TRY_TO_BOOLEAN({quoted_col})"

    # numeric -> TEXT: use TO_VARCHAR
    if src_numeric and is_text_type(target_type):
        return f"TO_VARCHAR({quoted_col})"

    # numeric -> numeric (FLOAT->NUMBER, NUMBER->FLOAT, etc.): direct cast
    if src_numeric and is_numeric_type(target_type):
        return f"{quoted_col}::{target_type}"

    # numeric -> DATE: convert via string intermediate
    if src_numeric and tgt_upper == "DATE":
        return f"TRY_TO_DATE(TO_VARCHAR({quoted_col}))"

    # numeric -> TIMESTAMP variants: convert via string intermediate
    if src_numeric and tgt_upper.startswith("TIMESTAMP"):
        return f"TRY_TO_TIMESTAMP(TO_VARCHAR({quoted_col}))"

    # date -> TEXT: use TO_VARCHAR
    if is_date_type(source_type) and is_text_type(target_type):
        return f"TO_VARCHAR({quoted_col})"

    # date -> TIMESTAMP or TIMESTAMP -> DATE: direct cast
    if is_date_type(source_type) and is_date_type(target_type):
        return f"{quoted_col}::{target_type}"

    # Fallback: direct cast
    return f"{quoted_col}::{target_type}"


# ---------------------------------------------------------------------------
# FQN resolution
# ---------------------------------------------------------------------------


def resolve_fqn_from_allowed(table_fqn: str) -> str:
    """Resolve a table FQN against the allowed_tables list from data isolation context.

    Handles bare table names (1-part), schema.table (2-part), and wrong-schema
    FQNs (3-part) by matching against the allowed list.
    """
    from tools.snowflake_tools import _allowed_tables

    allowed = _allowed_tables.get()
    if not allowed:
        return table_fqn

    # Exact match (case-insensitive)
    upper_fqn = table_fqn.upper()
    for t in allowed:
        if t.upper() == upper_fqn:
            return t

    # Table name portion match
    parts = table_fqn.split(".")
    if len(parts) == 3:
        table_name_only = parts[2].upper()
    elif len(parts) == 1:
        table_name_only = parts[0].upper()
    elif len(parts) == 2:
        table_name_only = parts[1].upper()
    else:
        return table_fqn

    for t in allowed:
        t_parts = t.split(".")
        if len(t_parts) == 3 and t_parts[2].upper() == table_name_only:
            logger.warning("FQN auto-corrected: %s -> %s", table_fqn, t)
            return t

    return table_fqn


# ---------------------------------------------------------------------------
# EXPLAIN validation
# ---------------------------------------------------------------------------


def validate_ddl_with_explain(ddl: str) -> tuple[bool, str]:
    """Validate DDL by running EXPLAIN on its SELECT portion.

    Extracts the SELECT after the AS keyword and runs EXPLAIN to catch
    SQL compilation errors before actually executing the DDL.

    Returns (is_valid, error_message).
    """
    match = re.search(r"\bAS\s*\n?(.*)", ddl, re.IGNORECASE | re.DOTALL)
    if not match:
        return True, ""  # Can't extract SELECT — skip validation

    select_sql = match.group(1).strip().rstrip(";")
    if not select_sql.upper().startswith("SELECT") and not select_sql.upper().startswith("WITH"):
        return True, ""

    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(f"EXPLAIN {select_sql}")
        cur.fetchall()
        cur.close()
        return True, ""
    except Exception as e:
        error_msg = str(e)
        logger.info("EXPLAIN validation failed: %s", error_msg[:200])
        return False, error_msg


# ---------------------------------------------------------------------------
# Cortex AI DDL generation (Tier 1)
# ---------------------------------------------------------------------------


def extract_create_statement(text: str) -> str | None:
    """Extract a CREATE statement from Cortex AI response, stripping markdown fences."""
    text = re.sub(r"```(?:sql|snowflake)?\s*\n?", "", text)
    text = re.sub(r"```\s*$", "", text)
    text = text.strip()

    match = re.search(
        r"(CREATE\s+(?:OR\s+REPLACE\s+)?(?:DYNAMIC\s+TABLE|VIEW)\b.*)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if match:
        ddl = match.group(1).strip().rstrip(";")
        # Strip CTEs from Dynamic Table DDL — Snowflake doesn't support them.
        # Replace "... AS\nWITH cte AS (\n  SELECT ...\n)\nSELECT ..."
        # with "... AS\nSELECT ..." by converting the CTE into a subquery.
        ddl = _strip_cte_from_dynamic_table(ddl)
        return ddl

    if text.upper().startswith("CREATE"):
        return text.rstrip(";")

    return None


def _strip_cte_from_dynamic_table(ddl: str) -> str:
    """Convert CTE in Dynamic Table DDL to a subquery.

    Cortex AI sometimes generates:
        CREATE ... DYNAMIC TABLE ... AS
        WITH cte_name AS (SELECT ...) SELECT ... FROM cte_name
    which Snowflake rejects. This rewrites it to use a subquery instead.
    """
    if "DYNAMIC TABLE" not in ddl.upper():
        return ddl

    # Find the AS keyword that separates DDL header from the SELECT
    as_match = re.search(r'\bAS\s*\n', ddl, re.IGNORECASE)
    if not as_match:
        return ddl

    select_part = ddl[as_match.end():].strip()
    if not select_part.upper().startswith("WITH"):
        return ddl  # No CTE, nothing to fix

    # Extract: WITH <name> AS ( <cte_body> ) <final_select>
    cte_match = re.match(
        r'WITH\s+(\w+)\s+AS\s*\(\s*(.*?)\s*\)\s*(SELECT\b.*)',
        select_part,
        re.IGNORECASE | re.DOTALL,
    )
    if not cte_match:
        return ddl  # Complex CTE we can't parse — return as-is

    cte_name = cte_match.group(1)
    cte_body = cte_match.group(2).strip()
    final_select = cte_match.group(3).strip()

    # Replace references to cte_name in final_select with the subquery
    subquery = f"({cte_body}) AS {cte_name}"
    rewritten = re.sub(
        rf'\b{re.escape(cte_name)}\b',
        subquery,
        final_select,
        count=1,
        flags=re.IGNORECASE,
    )

    header = ddl[:as_match.end()]
    result = f"{header}{rewritten}"
    logger.info("Stripped CTE from Dynamic Table DDL (rewrote as subquery)")
    return result


def generate_ddl_via_cortex(prompt: str) -> str | None:
    """Send a DDL generation prompt to Snowflake Cortex AI (Arctic).

    Returns the extracted DDL string on success, None if Cortex is
    unavailable or fails. Uses TRY_COMPLETE which returns NULL gracefully.
    """
    settings = get_settings()
    model_name = settings.cortex_ddl_model

    escaped_prompt = prompt.replace("'", "''")
    sql = (
        f"SELECT SNOWFLAKE.CORTEX.TRY_COMPLETE('{model_name}', "
        f"'{escaped_prompt}') AS ddl"
    )

    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(sql)
        row = cur.fetchone()
        cur.close()

        if not row or row[0] is None:
            logger.info("Cortex TRY_COMPLETE returned NULL — Cortex unavailable")
            return None

        raw_response = str(row[0]).strip()
        if not raw_response:
            logger.info("Cortex returned empty response")
            return None

        ddl = extract_create_statement(raw_response)
        if not ddl:
            logger.warning(
                "Cortex response didn't contain a valid CREATE statement: %s",
                raw_response[:200],
            )
            return None

        logger.info("Cortex generated DDL (%d chars)", len(ddl))
        return ddl

    except Exception as e:
        logger.warning("Cortex DDL generation failed (will fall back): %s", e)
        return None


# ---------------------------------------------------------------------------
# DDL execution
# ---------------------------------------------------------------------------


_ALLOWED_DDL_PREFIXES = (
    "CREATE OR REPLACE DYNAMIC TABLE",
    "CREATE DYNAMIC TABLE",
    "CREATE OR REPLACE VIEW",
    "CREATE VIEW",
    "CREATE SCHEMA",
)


def execute_ddl(ddl: str) -> tuple[bool, str]:
    """Execute a CREATE VIEW/DYNAMIC TABLE DDL statement.

    Before executing, ensures the target schema exists in the EKAIX database.
    Returns (success, message).
    """
    from tools.naming import ensure_schema

    ddl_upper = ddl.strip().upper()
    if not any(ddl_upper.startswith(p) for p in _ALLOWED_DDL_PREFIXES):
        return False, "Only CREATE [OR REPLACE] VIEW/DYNAMIC TABLE/SCHEMA statements are allowed."

    try:
        conn = get_connection()
        cur = conn.cursor()

        # Ensure target schema exists
        fqn_match = re.search(
            r'(?:DYNAMIC\s+TABLE|VIEW)\s+"([^"]+)"\."([^"]+)"\."([^"]+)"',
            ddl, re.IGNORECASE,
        )
        if fqn_match:
            schema = fqn_match.group(2)
            try:
                ensure_schema(schema)
                logger.info("Ensured schema exists: EKAIX.%s", schema)
            except Exception as schema_err:
                logger.warning("ensure_schema failed (may already exist): %s", schema_err)

        cur.execute(ddl)
        result = cur.fetchone()
        cur.close()
        return True, str(result) if result else "OK"

    except Exception as e:
        logger.error("execute_ddl failed: %s -- DDL: %s", e, ddl[:200])
        return False, str(e)


# ---------------------------------------------------------------------------
# Column case-sensitivity quoting
# ---------------------------------------------------------------------------


_RESERVED_COL_NAMES = {
    "START", "STOP", "DATE", "VALUE", "ORDER", "KEY", "DEFAULT",
    "COMMENT", "TYPE", "COLUMN", "RESULT", "POSITION", "LEVEL",
    "REPLACE", "IDENTITY", "FILE", "COPY", "FORMAT", "CURRENT",
    "ROW", "INCREMENT", "TRIM", "EXTRACT", "ACCOUNT", "NUMBER",
    "TIME", "TIMESTAMP", "INTERVAL", "CONNECTION", "CONSTRAINT",
    "TRIGGER", "GRANT", "ROLE", "USER", "GROUP", "SHARE",
}


def quote_lowercase_columns_in_sql(sql: str) -> str:
    """Quote column identifiers in SQL that need quoting for Snowflake.

    Handles two cases:
    - Lowercase/mixed-case columns: case-insensitive match and quote with
      the actual column name from SHOW COLUMNS (e.g., DDL has ``ID`` but
      real column is ``Id`` → replaced with ``"Id"``).
    - Snowflake reserved words used as column names: always quoted even
      when uppercase (e.g., ``START`` → ``"START"``).

    Already-quoted identifiers are left unchanged.
    """
    try:
        conn = get_connection()
        cur = conn.cursor()

        fqn_pattern = re.compile(
            r'"([^"]+)"\."([^"]+)"\."([^"]+)"|(\w+)\.(\w+)\.(\w+)',
        )
        # actual column name → needs quoting
        needs_quoting: set[str] = set()

        seen_tables: set[str] = set()
        for m in fqn_pattern.finditer(sql):
            if m.group(1):
                db, schema, table = m.group(1), m.group(2), m.group(3)
            else:
                db, schema, table = m.group(4), m.group(5), m.group(6)
            fqn_key = f"{db}.{schema}.{table}".upper()
            if fqn_key in seen_tables:
                continue
            seen_tables.add(fqn_key)

            try:
                cur.execute(f'SHOW COLUMNS IN TABLE "{db}"."{schema}"."{table}"')
                for row in cur.fetchall():
                    col_name = row[2]
                    # Quote if: not fully uppercase, OR is a reserved word
                    if col_name != col_name.upper():
                        needs_quoting.add(col_name)
                    elif col_name.upper() in _RESERVED_COL_NAMES:
                        needs_quoting.add(col_name)
            except Exception as col_err:
                logger.debug("Could not fetch columns for %s: %s", fqn_key, col_err)

        if not needs_quoting:
            cur.close()
            return sql

        result = sql
        for col in sorted(needs_quoting, key=len, reverse=True):
            # Case-insensitive match so e.g. DDL's "ID" matches source "Id"
            pattern = re.compile(
                r'(?<!")(?<!\w)' + re.escape(col) + r'(?!\w)(?!")',
                re.IGNORECASE,
            )
            result = pattern.sub(f'"{col}"', result)

        cur.close()
        logger.info("Quoted %d columns in DDL SQL", len(needs_quoting))
        return result

    except Exception as e:
        logger.warning("quote_lowercase_columns_in_sql failed (non-fatal): %s", e)
        return sql


def uppercase_table_name_in_ddl(ddl: str) -> str:
    """Uppercase the target table name in a CREATE DYNAMIC TABLE DDL.

    Avoids creating case-sensitive lowercase tables in Snowflake.
    """
    def _upper_table(m: re.Match) -> str:
        prefix = m.group(1)
        db = m.group(2)
        schema = m.group(3)
        table = m.group(4)
        return f'{prefix}"{db}"."{schema}"."{table.upper()}"'

    return re.sub(
        r'(DYNAMIC\s+TABLE\s+)"([^"]+)"\."([^"]+)"\."([^"]+)"',
        _upper_table,
        ddl,
        count=1,
        flags=re.IGNORECASE,
    )
