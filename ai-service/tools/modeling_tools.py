"""LangChain tools for Gold/Marts layer modeling operations.

Tools create star schema (fact + dimension) Dynamic Tables in the
EKAIX.{DP}_MARTS schema, validate grain constraints, and persist
documentation artifacts (data catalog, business glossary, metrics
definitions, validation rules).

Two-tier DDL generation (same pattern as transformation tools):
    - Tier 1: Snowflake Cortex AI (Arctic) generates DDL natively
    - Tier 2: LLM-provided SELECT wrapped in CREATE DYNAMIC TABLE
    - EXPLAIN validation before execution catches SQL errors early
    - Batch processing handles all tables in one tool call
"""

import json
import logging
import re
from typing import Any
from uuid import uuid4

from langchain_core.tools import tool

from config import get_settings
from services.snowflake import get_connection
from services import postgres as pg_service
from tools.ddl import (
    execute_ddl,
    generate_ddl_via_cortex,
    quote_lowercase_columns_in_sql,
    tool_error,
    uppercase_table_name_in_ddl,
    validate_ddl_with_explain,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_marts_schema() -> str:
    """Return the marts schema name for the current data product."""
    from tools.postgres_tools import get_data_product_name
    from tools.naming import marts_schema
    name = get_data_product_name()
    if not name:
        raise RuntimeError("Data product name not set in context")
    return marts_schema(name)


def _get_target_lag() -> str:
    return getattr(get_settings(), "transformation_target_lag", "1 hour")


async def _get_pool() -> Any:
    """Return the global PostgreSQL pool, raising if not initialized."""
    if pg_service._pool is None:
        raise RuntimeError("PostgreSQL pool not initialized.")
    return pg_service._pool


def _normalize_json(raw: str) -> str:
    """Normalize LLM output to valid JSON string."""
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        parsed = {"document": raw}
    return json.dumps(parsed)


def _build_gold_ddl(
    target_table_name: str,
    select_sql: str,
    target_lag: str = "",
) -> str:
    """Build a CREATE OR REPLACE DYNAMIC TABLE DDL string.

    Applies column case quoting and table name uppercasing.
    """
    from tools.naming import EKAIX_DATABASE

    database = EKAIX_DATABASE
    marts = _get_marts_schema()
    lag = target_lag or _get_target_lag()
    settings = get_settings()
    warehouse = settings.snowflake_warehouse

    target_table_upper = target_table_name.upper()
    target_fqn = f'"{database}"."{marts}"."{target_table_upper}"'

    ddl = f"""CREATE OR REPLACE DYNAMIC TABLE {target_fqn}
  TARGET_LAG = '{lag}'
  WAREHOUSE = "{warehouse}"
AS
{select_sql}"""

    # Quote lowercase column references
    ddl = quote_lowercase_columns_in_sql(ddl)
    # Uppercase table name (safety net)
    ddl = uppercase_table_name_in_ddl(ddl)

    return ddl


def _build_cortex_prompt_for_gold(
    target_fqn: str,
    table_type: str,
    source_tables_desc: str,
    requirements: str,
    warehouse: str,
    target_lag: str,
) -> str:
    """Build a Cortex AI prompt for Dynamic Table DDL generation."""
    return f"""Generate a Snowflake CREATE OR REPLACE DYNAMIC TABLE statement.
Target: {target_fqn}
Table type: {table_type}
Target lag: {target_lag}
Warehouse: {warehouse}

Source tables and their columns:
{source_tables_desc}

Requirements:
{requirements}

Rules:
- CRITICAL: Do NOT use WITH clauses or CTEs. Dynamic Tables do not support CTEs.
  The SELECT must come directly after the AS keyword.
- The exact format MUST be:
  CREATE OR REPLACE DYNAMIC TABLE {target_fqn}
    TARGET_LAG = '{target_lag}'
    WAREHOUSE = "{warehouse}"
  AS
  SELECT ... FROM ... ;
- Use TRY_TO_DATE, TRY_TO_TIMESTAMP, TRY_CAST for safe type conversions
- TRY_CAST only works from VARCHAR to numeric. For FLOAT to NUMBER use direct :: cast
- Use COALESCE for null handling
- Quote all column names with double quotes
- Use fully qualified table names with double quotes
- Return ONLY the complete SQL statement, no explanation"""


# ---------------------------------------------------------------------------
# Guardrail warnings (surfaced to LLM, not hard blockers)
# ---------------------------------------------------------------------------

_MONETARY_KEYWORDS = {
    "COST", "REVENUE", "AMOUNT", "TOTAL",
    "PRICE", "FEE", "CHARGE", "PAYMENT", "BALANCE", "SALARY", "WAGE",
    "EXPENSE", "INCOME", "PROFIT", "LOSS", "BUDGET", "SPEND",
    "PREMIUM", "DEDUCTIBLE", "COPAY", "REIMBURSEMENT", "COVERAGE",
}


def _check_measureless_facts(tables: list[dict]) -> list[str]:
    """Warn if a fact table SELECT has no numeric columns."""
    warnings: list[str] = []
    for spec in tables:
        if spec.get("type") != "fact":
            continue
        name = spec.get("name", "")
        sql = spec.get("select_sql", "").upper()
        has_numeric = any(
            kw in sql
            for kw in (
                "NUMBER", "FLOAT", "DECIMAL", "INT", "NUMERIC",
                "SUM(", "AVG(", "COUNT(", "AMOUNT", "COST", "TOTAL",
                "REVENUE", "QUANTITY", "DURATION", "PRICE",
            )
        )
        if not has_numeric:
            warnings.append(
                f"FACT WARNING: {name} has no numeric measures in its SELECT. "
                f"Consider reclassifying as a dimension or confirming it is a "
                f"factless fact (event tracking only)."
            )
    return warnings


def _check_source_mirrors(tables: list[dict]) -> list[str]:
    """Warn if a FACT table is a direct copy of a source (no transformation).

    Dimensions are legitimately simple projections from source tables
    (e.g., SELECT Id, Name, City FROM source) so they are not flagged.
    """
    warnings: list[str] = []
    for spec in tables:
        if spec.get("type") == "dimension":
            continue  # Dimensions are expected to be simple projections
        name = spec.get("name", "")
        sql = spec.get("select_sql", "").upper().strip()
        has_join = " JOIN " in sql
        has_agg = any(fn in sql for fn in ("GROUP BY", "SUM(", "AVG(", "COUNT(", "MIN(", "MAX("))
        has_case = "CASE " in sql or "CASE\n" in sql
        has_coalesce = "COALESCE(" in sql
        has_cast = "CAST(" in sql or "TRY_CAST(" in sql or "::" in sql
        has_where = " WHERE " in sql
        has_union = " UNION " in sql
        if not any([has_join, has_agg, has_case, has_coalesce, has_cast, has_where, has_union]):
            warnings.append(
                f"MIRROR WARNING: {name} appears to be a direct copy of the "
                f"source table with no joins, aggregations, or transformations. "
                f"Consider whether actual modeling was applied."
            )
    return warnings


def _check_measures_in_dimensions(tables: list[dict]) -> list[str]:
    """Warn if a dimension table SELECT includes monetary/aggregate-sounding columns.

    Skips keywords that only appear as function calls (e.g. COUNT(*) in CTEs)
    rather than as column names or aliases.
    """
    warnings: list[str] = []
    for spec in tables:
        if spec.get("type") != "dimension":
            continue
        name = spec.get("name", "")
        sql = spec.get("select_sql", "").upper()
        # Only flag keywords that appear as column names/aliases, not inside
        # function calls like COUNT(*), SUM(), AVG() which are part of CTEs
        found = []
        for kw in _MONETARY_KEYWORDS:
            if kw not in sql:
                continue
            # Check if kw appears as a standalone word NOT followed by "("
            import re
            if re.search(r'\b' + re.escape(kw) + r'\b(?!\s*\()', sql):
                found.append(kw)
        if found:
            warnings.append(
                f"DIMENSION WARNING: {name} contains potential measures "
                f"({', '.join(sorted(found)[:5])}). Consider moving these to "
                f"a fact table."
            )
    return warnings


def _check_missing_dim_date(tables: list[dict]) -> list[str]:
    """Warn if facts have date columns but no DIM_DATE is in the batch."""
    has_dim_date = any(
        "DATE" in spec.get("name", "").upper()
        and spec.get("type") == "dimension"
        for spec in tables
    )
    if has_dim_date:
        return []

    facts_with_dates: list[str] = []
    for spec in tables:
        if spec.get("type") != "fact":
            continue
        sql = spec.get("select_sql", "").upper()
        if any(kw in sql for kw in ("DATE", "TIMESTAMP", "_AT", "_ON", "_DT")):
            facts_with_dates.append(spec.get("name", ""))

    if facts_with_dates:
        return [
            f"DATE WARNING: No date dimension found in the batch, but "
            f"these fact tables have date columns: {', '.join(facts_with_dates)}. "
            f"Consider adding a date dimension for time-based analysis."
        ]
    return []


# ---------------------------------------------------------------------------
# Snowflake DDL Tools
# ---------------------------------------------------------------------------


@tool
def create_gold_table(ddl: str) -> str:
    """Execute a CREATE DYNAMIC TABLE statement to build a marts layer table.

    Creates fact or dimension tables in the EKAIX.{DP}_MARTS schema.
    Before executing, runs EXPLAIN validation and ensures the target schema exists.

    Args:
        ddl: The DDL statement to execute (CREATE OR REPLACE DYNAMIC TABLE ...)
    """
    ddl_upper = ddl.strip().upper()

    if not ddl_upper.startswith("CREATE OR REPLACE DYNAMIC TABLE") and \
       not ddl_upper.startswith("CREATE DYNAMIC TABLE") and \
       not ddl_upper.startswith("CREATE SCHEMA"):
        return tool_error(
            "create_gold_table",
            "Only CREATE [OR REPLACE] DYNAMIC TABLE and CREATE SCHEMA statements are allowed.",
        )

    try:
        # Quote lowercase columns and uppercase table name
        ddl = quote_lowercase_columns_in_sql(ddl)
        ddl = uppercase_table_name_in_ddl(ddl)

        # Ensure target schema exists
        if "DYNAMIC TABLE" in ddl.strip().upper():
            from tools.naming import ensure_schema
            fqn_match = re.search(
                r'DYNAMIC\s+TABLE\s+"([^"]+)"\."([^"]+)"\."([^"]+)"',
                ddl, re.IGNORECASE,
            )
            if fqn_match:
                schema = fqn_match.group(2)
                try:
                    ensure_schema(schema)
                except Exception as schema_err:
                    logger.warning("ensure_schema failed (may already exist): %s", schema_err)

        # EXPLAIN validation
        is_valid, explain_err = validate_ddl_with_explain(ddl)
        if not is_valid:
            logger.warning("Gold DDL failed EXPLAIN: %s", explain_err[:200])
            return tool_error(
                "create_gold_table",
                f"DDL failed pre-execution validation: {explain_err}",
                hint="Fix the SELECT statement and retry.",
            )

        conn = get_connection()
        cur = conn.cursor()
        cur.execute(ddl)
        result = cur.fetchone()
        cur.close()

        return json.dumps({
            "status": "success",
            "message": "Gold table created successfully",
            "result": str(result) if result else "OK",
        })

    except Exception as e:
        logger.error("create_gold_table failed: %s — DDL: %s", e, ddl[:200])
        return tool_error("create_gold_table", str(e))


@tool
def generate_gold_table_ddl(
    source_fqn: str,
    target_table_name: str,
    table_type: str,
    select_sql: str,
    target_lag: str = "",
) -> str:
    """Generate CREATE OR REPLACE DYNAMIC TABLE DDL for a marts layer table.

    Uses EXPLAIN pre-validation to catch SQL compilation errors early.

    Args:
        source_fqn: One of the source Silver table FQNs (DATABASE.SCHEMA.TABLE).
        target_table_name: Name of the Gold table (e.g., FACT_ENCOUNTERS, DIM_PATIENT).
        table_type: Either "fact" or "dimension".
        select_sql: Complete SELECT statement that defines the table content.
        target_lag: Refresh lag (default from config, typically "1 hour").
    """
    from tools.snowflake_tools import _validate_fqn

    _, src_err = _validate_fqn(source_fqn)
    if src_err:
        return tool_error("generate_gold_table_ddl", f"Invalid source FQN: {src_err}")

    ddl = _build_gold_ddl(target_table_name, select_sql, target_lag)

    from tools.naming import EKAIX_DATABASE
    marts = _get_marts_schema()
    target_table_upper = target_table_name.upper()

    # EXPLAIN pre-validation
    is_valid, explain_err = validate_ddl_with_explain(ddl)
    if not is_valid:
        logger.warning(
            "Gold DDL EXPLAIN failed for %s.%s.%s: %s",
            EKAIX_DATABASE, marts, target_table_upper, explain_err[:200],
        )
        return json.dumps({
            "ddl": ddl,
            "target_fqn": f"{EKAIX_DATABASE}.{marts}.{target_table_upper}",
            "table_type": table_type,
            "explain_error": explain_err,
            "hint": "The SELECT has a SQL compilation error. Fix the SELECT and retry.",
        })

    return json.dumps({
        "ddl": ddl,
        "target_fqn": f"{EKAIX_DATABASE}.{marts}.{target_table_upper}",
        "table_type": table_type,
    })


@tool
async def create_gold_tables_batch(
    data_product_id: str,
    tables_json: str,
) -> str:
    """Create all Gold layer tables (fact + dimension) in a single batch.

    Processes each table: build DDL -> EXPLAIN validate -> Cortex fallback
    if EXPLAIN fails -> execute -> validate grain. Handles all tables in one
    tool call to avoid recursion limit issues.

    Args:
        data_product_id: UUID of the data product
        tables_json: JSON array of table specs. Each spec has:
            - name: Target table name (e.g., "FACT_ENCOUNTERS", "DIM_PATIENT")
            - type: "fact" or "dimension"
            - select_sql: Complete SELECT statement
            - grain_columns: (optional, for facts) Comma-separated grain columns
            - source_fqn: One source table FQN for context
    """
    try:
        tables = json.loads(tables_json)
    except json.JSONDecodeError as e:
        return tool_error("create_gold_tables_batch", f"Invalid JSON: {e}")

    if not isinstance(tables, list) or not tables:
        return tool_error("create_gold_tables_batch", "tables_json must be a non-empty JSON array")

    # Run guardrail checks (warnings, not blockers)
    guardrail_warnings: list[str] = []
    guardrail_warnings.extend(_check_measureless_facts(tables))
    guardrail_warnings.extend(_check_source_mirrors(tables))
    guardrail_warnings.extend(_check_measures_in_dimensions(tables))
    guardrail_warnings.extend(_check_missing_dim_date(tables))
    if guardrail_warnings:
        for w in guardrail_warnings:
            logger.warning("Guardrail: %s", w)

    from tools.naming import EKAIX_DATABASE, ensure_schema

    marts = _get_marts_schema()
    settings = get_settings()
    warehouse = settings.snowflake_warehouse
    lag = _get_target_lag()

    # Ensure schema exists once
    try:
        ensure_schema(marts)
    except Exception as schema_err:
        logger.warning("ensure_schema failed (may already exist): %s", schema_err)

    # Load working layer cache for source FQN resolution
    working_layer: dict[str, str] = {}
    try:
        from tools.naming import curated_schema as _curated_schema_fn
        from tools.postgres_tools import get_data_product_name
        from services import redis as redis_service

        dp_name = get_data_product_name()
        curated = _curated_schema_fn(dp_name) if dp_name else ""

        client = await redis_service.get_client(settings.redis_url)
        wl_key = f"cache:working_layer:{data_product_id}"
        working_layer = await redis_service.get_json(client, wl_key) or {}
    except Exception as e:
        logger.warning("Failed to load working layer cache: %s", e)
        curated = ""

    results: list[dict[str, Any]] = []
    gold_mapping: dict[str, str] = {}

    for spec in tables:
        table_name = spec.get("name", "").upper()
        table_type = spec.get("type", "fact")
        select_sql = spec.get("select_sql", "")
        grain_columns = spec.get("grain_columns", "")
        source_fqn = spec.get("source_fqn", "")

        table_result: dict[str, Any] = {
            "name": table_name,
            "type": table_type,
            "status": "pending",
            "tier_used": "template",
            "issues": [],
        }

        if not table_name or not select_sql:
            table_result["status"] = "failed"
            table_result["issues"].append("Missing name or select_sql")
            results.append(table_result)
            continue

        target_fqn = f"{EKAIX_DATABASE}.{marts}.{table_name}"

        # Auto-fix source FQN references in SELECT SQL
        # LLM often uses wrong database (e.g., SYNTHEA.{curated} instead of EKAIX.{curated})
        if curated:
            select_sql = re.sub(
                rf'(?i)"?(\w+)"?\."?{re.escape(curated)}"?',
                f'"{EKAIX_DATABASE}"."{curated}"',
                select_sql,
            )
        # Replace original source FQNs with working layer FQNs
        for orig_fqn, working_fqn in working_layer.items():
            # Replace both quoted and unquoted references
            parts = orig_fqn.split(".")
            if len(parts) == 3:
                # Pattern: "DB"."SCHEMA"."TABLE" or DB.SCHEMA.TABLE
                pattern = rf'(?i)"?{re.escape(parts[0])}"?\."?{re.escape(parts[1])}"?\."?{re.escape(parts[2])}"?'
                w_parts = working_fqn.split(".")
                if len(w_parts) == 3:
                    replacement = f'"{w_parts[0]}"."{w_parts[1]}"."{w_parts[2]}"'
                    select_sql = re.sub(pattern, replacement, select_sql)

        # Fix references to curated tables that were never transformed
        # (Gold-quality tables skipped during transformation don't have
        # a curated VIEW — replace with the original source FQN)
        if curated:
            valid_curated_tables: set[str] = set()
            for wl_target in working_layer.values():
                wl_parts = wl_target.split(".")
                if len(wl_parts) == 3:
                    valid_curated_tables.add(wl_parts[2].upper())

            # Also check Snowflake for actual curated views (working_layer
            # cache may be incomplete if some views were pass-through)
            if not valid_curated_tables:
                try:
                    from services.snowflake import get_connection
                    conn = get_connection()
                    cur = conn.cursor()
                    cur.execute(f'SHOW VIEWS IN SCHEMA "{EKAIX_DATABASE}"."{curated}"')
                    for row in cur.fetchall():
                        view_name = row[1] if len(row) > 1 else ""
                        if view_name:
                            valid_curated_tables.add(view_name.upper())
                    cur.close()
                    logger.info("Loaded %d curated views from Snowflake", len(valid_curated_tables))
                except Exception as sf_err:
                    logger.debug("Could not fetch curated views: %s", sf_err)

            curated_ref_pattern = re.compile(
                rf'"?{re.escape(EKAIX_DATABASE)}"?\."?{re.escape(curated)}"?\."?(\w+)"?',
                re.IGNORECASE,
            )
            for cm in curated_ref_pattern.finditer(select_sql):
                ref_table = cm.group(1).upper()
                if ref_table not in valid_curated_tables:
                    from tools.snowflake_tools import _allowed_tables
                    allowed = _allowed_tables.get() or []
                    for orig in allowed:
                        orig_parts = orig.split(".")
                        if len(orig_parts) == 3 and orig_parts[2].upper() == ref_table:
                            repl = f'"{orig_parts[0]}"."{orig_parts[1]}"."{orig_parts[2]}"'
                            select_sql = select_sql.replace(cm.group(0), repl)
                            logger.warning(
                                "Fixed Gold DDL: replaced non-existent curated ref %s -> %s",
                                cm.group(0), orig,
                            )
                            break

        # Step 1: Build DDL from LLM-provided SELECT
        ddl = _build_gold_ddl(table_name, select_sql)

        # Step 2: EXPLAIN validate
        is_valid, explain_err = validate_ddl_with_explain(ddl)

        if not is_valid:
            logger.warning("Gold DDL EXPLAIN failed for %s: %s", table_name, explain_err[:150])

            # Step 2b: Try Cortex AI as fallback
            prompt = _build_cortex_prompt_for_gold(
                target_fqn=f'"{EKAIX_DATABASE}"."{marts}"."{table_name}"',
                table_type=table_type,
                source_tables_desc=f"Source: {source_fqn}\nOriginal SELECT (has errors):\n{select_sql[:500]}",
                requirements=f"Fix the SQL compilation error: {explain_err[:200]}",
                warehouse=warehouse,
                target_lag=lag,
            )
            cortex_ddl = generate_ddl_via_cortex(prompt)
            if cortex_ddl:
                cortex_ddl = quote_lowercase_columns_in_sql(cortex_ddl)
                cortex_ddl = uppercase_table_name_in_ddl(cortex_ddl)
                cortex_valid, cortex_err = validate_ddl_with_explain(cortex_ddl)
                if cortex_valid:
                    ddl = cortex_ddl
                    table_result["tier_used"] = "cortex"
                    is_valid = True
                    logger.info("Cortex fixed Gold DDL for %s", table_name)
                else:
                    logger.warning("Cortex Gold DDL also failed EXPLAIN for %s: %s", table_name, cortex_err[:150])

            if not is_valid:
                table_result["status"] = "failed"
                table_result["issues"].append(f"EXPLAIN failed: {explain_err[:200]}")
                results.append(table_result)
                continue

        # Step 3: Execute DDL
        success, exec_msg = execute_ddl(ddl)
        if not success:
            table_result["status"] = "failed"
            table_result["issues"].append(f"DDL execution failed: {exec_msg}")
            results.append(table_result)
            continue

        # Step 4: Validate grain (for fact tables)
        if table_type == "fact" and grain_columns:
            try:
                from tools.snowflake_tools import _validate_fqn, _quoted_fqn
                fqn_parts = [EKAIX_DATABASE, marts, table_name]
                quoted = _quoted_fqn(fqn_parts)
                cols = [c.strip() for c in grain_columns.split(",") if c.strip()]
                quoted_cols = ", ".join(f'"{c}"' for c in cols)

                conn = get_connection()
                cur = conn.cursor()
                cur.execute(f"SELECT COUNT(*) FROM {quoted}")
                total_rows = cur.fetchone()[0] or 0

                cur.execute(
                    f"SELECT {quoted_cols}, COUNT(*) AS cnt "
                    f"FROM {quoted} GROUP BY {quoted_cols} HAVING COUNT(*) > 1 LIMIT 5"
                )
                violations = cur.fetchall()
                cur.close()

                table_result["total_rows"] = total_rows
                if violations:
                    table_result["issues"].append(
                        f"Grain violation: {len(violations)}+ duplicate groups on ({grain_columns})"
                    )
            except Exception as e:
                table_result["issues"].append(f"Grain validation failed: {e}")

        table_result["status"] = "success"
        table_result["target_fqn"] = target_fqn

        # Track mapping for registration
        if source_fqn:
            gold_mapping[source_fqn] = target_fqn

        results.append(table_result)

    # Register Gold layer if we have mappings
    if gold_mapping:
        try:
            from services import redis as redis_service
            client = await redis_service.get_client(settings.redis_url)

            silver_key = f"cache:working_layer:{data_product_id}"
            silver_map = await redis_service.get_json(client, silver_key) or {}

            # Build complete source→gold chain
            complete_map: dict[str, str] = {}
            mapping_upper = {k.upper(): v.upper() for k, v in gold_mapping.items()}

            for source_fqn, silver_fqn in silver_map.items():
                gold_fqn = mapping_upper.get(silver_fqn.upper())
                if gold_fqn:
                    complete_map[source_fqn.upper()] = gold_fqn
                else:
                    complete_map[source_fqn.upper()] = silver_fqn

            if not silver_map:
                complete_map = {k.upper(): v.upper() for k, v in gold_mapping.items()}

            await redis_service.set_json(client, silver_key, complete_map, ttl=86400)

            gold_key = f"cache:gold_layer:{data_product_id}"
            await redis_service.set_json(client, gold_key, gold_mapping, ttl=86400)

            if pg_service._pool is not None:
                pool = pg_service._pool
                sql = """
                UPDATE data_products
                SET state = COALESCE(state, '{}'::jsonb)
                    || jsonb_build_object('gold_layer', $1::jsonb)
                    || jsonb_build_object('working_layer', $2::jsonb)
                WHERE id = $3::uuid
                """
                await pg_service.execute(
                    pool, sql,
                    json.dumps(gold_mapping),
                    json.dumps(complete_map),
                    data_product_id,
                )

            # Update Neo4j lineage
            _update_neo4j_lineage(data_product_id, gold_mapping, silver_map)

        except Exception as e:
            logger.error("Batch: gold layer registration failed: %s", e)

    success_count = sum(1 for r in results if r["status"] == "success")
    failed_count = sum(1 for r in results if r["status"] == "failed")

    return json.dumps({
        "summary": {
            "total": len(results),
            "success": success_count,
            "failed": failed_count,
            "tables_registered": len(gold_mapping),
        },
        "warnings": guardrail_warnings,
        "tables": results,
        "gold_mapping": gold_mapping,
    }, default=str)


@tool
def validate_gold_grain(
    table_fqn: str,
    grain_columns: str,
) -> str:
    """Validate that a Gold fact table has the correct grain (no duplicates).

    Args:
        table_fqn: Fully qualified table name (EKAIX.{DP}_MARTS.TABLE).
        grain_columns: Comma-separated columns that define the grain.
    """
    from tools.snowflake_tools import _validate_fqn, _quoted_fqn

    parts, err = _validate_fqn(table_fqn)
    if err:
        return tool_error("validate_gold_grain", f"Invalid FQN: {err}")

    parts = [p.upper() for p in parts]
    quoted = _quoted_fqn(parts)
    cols = [c.strip() for c in grain_columns.split(",") if c.strip()]

    if not cols:
        return tool_error("validate_gold_grain", "grain_columns cannot be empty")

    quoted_cols = ", ".join(f'"{c}"' for c in cols)

    try:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute(f"SELECT COUNT(*) FROM {quoted}")
        total_rows = cur.fetchone()[0] or 0

        grain_sql = f"""
        SELECT {quoted_cols}, COUNT(*) AS cnt
        FROM {quoted}
        GROUP BY {quoted_cols}
        HAVING COUNT(*) > 1
        LIMIT 10
        """
        cur.execute(grain_sql)
        desc = [d[0] for d in cur.description] if cur.description else []
        violations = [dict(zip(desc, row)) for row in cur.fetchall()]

        cur.execute(f"SELECT COUNT(*) FROM (SELECT {quoted_cols} FROM {quoted} GROUP BY {quoted_cols})")
        distinct_grains = cur.fetchone()[0] or 0

        cur.close()

        passed = len(violations) == 0

        return json.dumps({
            "table": table_fqn,
            "grain_columns": cols,
            "total_rows": total_rows,
            "distinct_grains": distinct_grains,
            "passed": passed,
            "duplicate_count": len(violations),
            "sample_violations": violations[:5] if violations else [],
            "message": "Grain validated — no duplicates" if passed
                       else f"GRAIN VIOLATION: {len(violations)}+ duplicate groups found",
        }, default=str)

    except Exception as e:
        logger.error("validate_gold_grain failed for %s: %s", table_fqn, e)
        return tool_error("validate_gold_grain", str(e), table=table_fqn)


# ---------------------------------------------------------------------------
# Documentation Artifact Tools (PostgreSQL persistence)
# ---------------------------------------------------------------------------


@tool
async def save_data_catalog(data_product_id: str, catalog_json: str) -> str:
    """Save a data catalog document for Gold layer tables.

    Args:
        data_product_id: UUID of the data product.
        catalog_json: JSON string containing the catalog.
    """
    pool = await _get_pool()
    cat_id = str(uuid4())
    clean_json = _normalize_json(catalog_json)

    sql = """
    INSERT INTO data_catalog (id, data_product_id, catalog_json, created_by, created_at)
    VALUES ($1::uuid, $2::uuid, $3::jsonb, $4, NOW())
    """
    await pg_service.execute(pool, sql, cat_id, data_product_id, clean_json, "system")

    logger.info("Saved data catalog for %s (id=%s)", data_product_id, cat_id)
    return json.dumps({"status": "ok", "catalog_id": cat_id})


@tool
async def save_business_glossary(data_product_id: str, glossary_json: str) -> str:
    """Save a business glossary mapping business terms to physical columns.

    Args:
        data_product_id: UUID of the data product.
        glossary_json: JSON string containing the glossary.
    """
    pool = await _get_pool()
    gl_id = str(uuid4())
    clean_json = _normalize_json(glossary_json)

    sql = """
    INSERT INTO business_glossary (id, data_product_id, glossary_json, created_by, created_at)
    VALUES ($1::uuid, $2::uuid, $3::jsonb, $4, NOW())
    """
    await pg_service.execute(pool, sql, gl_id, data_product_id, clean_json, "system")

    logger.info("Saved business glossary for %s (id=%s)", data_product_id, gl_id)
    return json.dumps({"status": "ok", "glossary_id": gl_id})


@tool
async def save_metrics_definitions(data_product_id: str, metrics_json: str) -> str:
    """Save KPI and metric definitions with formulas.

    Args:
        data_product_id: UUID of the data product.
        metrics_json: JSON string containing the metrics.
    """
    pool = await _get_pool()
    met_id = str(uuid4())
    clean_json = _normalize_json(metrics_json)

    sql = """
    INSERT INTO metrics_definitions (id, data_product_id, metrics_json, created_by, created_at)
    VALUES ($1::uuid, $2::uuid, $3::jsonb, $4, NOW())
    """
    await pg_service.execute(pool, sql, met_id, data_product_id, clean_json, "system")

    logger.info("Saved metrics definitions for %s (id=%s)", data_product_id, met_id)
    return json.dumps({"status": "ok", "metrics_id": met_id})


@tool
async def save_validation_rules(data_product_id: str, rules_json: str) -> str:
    """Save data validation rules for Gold layer tables.

    Args:
        data_product_id: UUID of the data product.
        rules_json: JSON string containing the rules.
    """
    pool = await _get_pool()
    rule_id = str(uuid4())
    clean_json = _normalize_json(rules_json)

    sql = """
    INSERT INTO validation_rules (id, data_product_id, rules_json, created_by, created_at)
    VALUES ($1::uuid, $2::uuid, $3::jsonb, $4, NOW())
    """
    await pg_service.execute(pool, sql, rule_id, data_product_id, clean_json, "system")

    logger.info("Saved validation rules for %s (id=%s)", data_product_id, rule_id)
    return json.dumps({"status": "ok", "rules_id": rule_id})


@tool
async def get_latest_data_catalog(data_product_id: str) -> str:
    """Retrieve the latest data catalog for a data product.

    Args:
        data_product_id: UUID of the data product.
    """
    pool = await _get_pool()
    rows = await pg_service.fetch(
        pool,
        "SELECT catalog_json, version, created_at FROM data_catalog WHERE data_product_id = $1::uuid ORDER BY version DESC LIMIT 1",
        data_product_id,
    )
    if not rows:
        return json.dumps({"status": "not_found", "data_product_id": data_product_id})
    row = rows[0]
    return json.dumps({"status": "ok", "version": row["version"], "catalog": row["catalog_json"], "created_at": str(row["created_at"])}, default=str)


@tool
async def get_latest_business_glossary(data_product_id: str) -> str:
    """Retrieve the latest business glossary for a data product.

    Args:
        data_product_id: UUID of the data product.
    """
    pool = await _get_pool()
    rows = await pg_service.fetch(
        pool,
        "SELECT glossary_json, version, created_at FROM business_glossary WHERE data_product_id = $1::uuid ORDER BY version DESC LIMIT 1",
        data_product_id,
    )
    if not rows:
        return json.dumps({"status": "not_found", "data_product_id": data_product_id})
    row = rows[0]
    return json.dumps({"status": "ok", "version": row["version"], "glossary": row["glossary_json"], "created_at": str(row["created_at"])}, default=str)


@tool
async def get_latest_metrics_definitions(data_product_id: str) -> str:
    """Retrieve the latest metrics/KPI definitions for a data product.

    Args:
        data_product_id: UUID of the data product.
    """
    pool = await _get_pool()
    rows = await pg_service.fetch(
        pool,
        "SELECT metrics_json, version, created_at FROM metrics_definitions WHERE data_product_id = $1::uuid ORDER BY version DESC LIMIT 1",
        data_product_id,
    )
    if not rows:
        return json.dumps({"status": "not_found", "data_product_id": data_product_id})
    row = rows[0]
    return json.dumps({"status": "ok", "version": row["version"], "metrics": row["metrics_json"], "created_at": str(row["created_at"])}, default=str)


@tool
async def get_latest_validation_rules(data_product_id: str) -> str:
    """Retrieve the latest validation rules for a data product.

    Args:
        data_product_id: UUID of the data product.
    """
    pool = await _get_pool()
    rows = await pg_service.fetch(
        pool,
        "SELECT rules_json, version, created_at FROM validation_rules WHERE data_product_id = $1::uuid ORDER BY version DESC LIMIT 1",
        data_product_id,
    )
    if not rows:
        return json.dumps({"status": "not_found", "data_product_id": data_product_id})
    row = rows[0]
    return json.dumps({"status": "ok", "version": row["version"], "rules": row["rules_json"], "created_at": str(row["created_at"])}, default=str)


@tool
async def register_gold_layer(
    data_product_id: str,
    gold_table_mapping_json: str,
) -> str:
    """Register Gold layer tables as the final working layer for semantic modeling.

    Stores source->gold mapping, updates Neo4j lineage graph.

    Args:
        data_product_id: UUID of the data product.
        gold_table_mapping_json: JSON object mapping Silver FQN to Gold FQN.
    """
    try:
        mapping = json.loads(gold_table_mapping_json)
    except json.JSONDecodeError as e:
        return tool_error("register_gold_layer", f"Invalid JSON: {e}")

    if not isinstance(mapping, dict) or not mapping:
        return tool_error("register_gold_layer", "Mapping must be a non-empty JSON object")

    try:
        from services import redis as redis_service

        settings = get_settings()
        client = await redis_service.get_client(settings.redis_url)

        silver_key = f"cache:working_layer:{data_product_id}"
        silver_map = await redis_service.get_json(client, silver_key) or {}

        complete_map: dict[str, str] = {}
        mapping_upper = {k.upper(): v.upper() for k, v in mapping.items()}

        for source_fqn, silver_fqn in silver_map.items():
            gold_fqn = mapping_upper.get(silver_fqn.upper())
            if gold_fqn:
                complete_map[source_fqn.upper()] = gold_fqn
            else:
                complete_map[source_fqn.upper()] = silver_fqn

        if not silver_map:
            complete_map = {k.upper(): v.upper() for k, v in mapping.items()}

        await redis_service.set_json(client, silver_key, complete_map, ttl=86400)

        gold_key = f"cache:gold_layer:{data_product_id}"
        await redis_service.set_json(client, gold_key, mapping, ttl=86400)

        if pg_service._pool is not None:
            pool = pg_service._pool
            sql = """
            UPDATE data_products
            SET state = COALESCE(state, '{}'::jsonb)
                || jsonb_build_object('gold_layer', $1::jsonb)
                || jsonb_build_object('working_layer', $2::jsonb)
            WHERE id = $3::uuid
            """
            await pg_service.execute(
                pool, sql,
                json.dumps(mapping),
                json.dumps(complete_map),
                data_product_id,
            )

        _update_neo4j_lineage(data_product_id, mapping, silver_map)

        logger.info(
            "Registered Gold layer for %s: %d table mappings",
            data_product_id, len(mapping),
        )

        return json.dumps({
            "status": "success",
            "data_product_id": data_product_id,
            "gold_table_count": len(mapping),
            "mapping": mapping,
        })

    except Exception as e:
        logger.error("register_gold_layer failed: %s", e)
        return tool_error("register_gold_layer", str(e))


def _update_neo4j_lineage(
    data_product_id: str,
    silver_to_gold: dict[str, str],
    source_to_silver: dict[str, str] | None = None,
) -> None:
    """Write lineage relationships to Neo4j."""
    try:
        from neo4j import GraphDatabase

        settings = get_settings()
        uri = settings.neo4j_uri
        user = settings.neo4j_user
        password = settings.neo4j_password.get_secret_value()

        if not uri:
            logger.warning("Neo4j not configured — skipping lineage update")
            return

        driver = GraphDatabase.driver(uri, auth=(user, password))

        with driver.session() as session:
            if source_to_silver:
                for source_fqn, silver_fqn in source_to_silver.items():
                    session.run(
                        """
                        MERGE (t:Table {fqn: $source_fqn})
                        SET t.data_product_id = $dp_id
                        MERGE (s:SilverTable {fqn: $silver_fqn})
                        SET s.data_product_id = $dp_id,
                            s.source_fqn = $source_fqn
                        MERGE (t)-[:TRANSFORMED_TO]->(s)
                        """,
                        source_fqn=source_fqn.upper(),
                        silver_fqn=silver_fqn.upper(),
                        dp_id=data_product_id,
                    )

            for silver_fqn, gold_fqn in silver_to_gold.items():
                gold_name = gold_fqn.split(".")[-1] if "." in gold_fqn else gold_fqn
                table_type = "fact" if gold_name.lower().startswith("fact_") else "dimension"

                session.run(
                    """
                    MERGE (s:SilverTable {fqn: $silver_fqn})
                    SET s.data_product_id = $dp_id
                    MERGE (g:GoldTable {fqn: $gold_fqn})
                    SET g.data_product_id = $dp_id,
                        g.table_type = $table_type
                    MERGE (s)-[:MODELED_TO]->(g)
                    """,
                    silver_fqn=silver_fqn.upper(),
                    gold_fqn=gold_fqn.upper(),
                    dp_id=data_product_id,
                    table_type=table_type,
                )

        driver.close()
        logger.info("Neo4j lineage updated: %d Silver→Gold relationships", len(silver_to_gold))

    except Exception as e:
        logger.warning("Neo4j lineage update failed (non-fatal): %s", e)


# ---------------------------------------------------------------------------
# OpenLineage Artifact
# ---------------------------------------------------------------------------


async def _generate_openlineage_json(data_product_id: str) -> dict[str, Any]:
    """Build an OpenLineage RunEvent JSON from Neo4j lineage + Redis metadata.

    Follows the OpenLineage v2-0-2 RunEvent spec:
    - inputs: source tables with schema + quality facets
    - outputs: gold tables with schema + column-level lineage facets
    """
    from datetime import datetime, timezone
    from neo4j import GraphDatabase
    from services import redis as redis_service
    from tools.postgres_tools import get_data_product_name

    settings = get_settings()
    dp_name = get_data_product_name() or "unknown"
    account = settings.snowflake_account or "unknown"
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    # --- 1. Query Neo4j for lineage graph ---
    source_tables: list[dict[str, Any]] = []
    silver_tables: list[dict[str, Any]] = []
    gold_tables: list[dict[str, Any]] = []
    source_to_silver: dict[str, str] = {}  # source_fqn -> silver_fqn
    silver_to_gold: dict[str, str] = {}    # silver_fqn -> gold_fqn

    try:
        uri = settings.neo4j_uri
        user = settings.neo4j_user
        password = settings.neo4j_password.get_secret_value()
        driver = GraphDatabase.driver(uri, auth=(user, password))

        with driver.session() as session:
            # Source → Silver
            result = session.run(
                """
                MATCH (t:Table)-[:TRANSFORMED_TO]->(s:SilverTable)
                WHERE t.data_product_id = $dp_id OR s.data_product_id = $dp_id
                RETURN t.fqn AS source_fqn, s.fqn AS silver_fqn
                """,
                dp_id=data_product_id,
            )
            for record in result:
                src = record["source_fqn"]
                slv = record["silver_fqn"]
                source_to_silver[src] = slv

            # Silver → Gold
            result = session.run(
                """
                MATCH (s:SilverTable)-[:MODELED_TO]->(g:GoldTable)
                WHERE s.data_product_id = $dp_id OR g.data_product_id = $dp_id
                RETURN s.fqn AS silver_fqn, g.fqn AS gold_fqn, g.table_type AS table_type
                """,
                dp_id=data_product_id,
            )
            for record in result:
                slv = record["silver_fqn"]
                gld = record["gold_fqn"]
                silver_to_gold[slv] = gld

        driver.close()
    except Exception as e:
        logger.warning("OpenLineage: Neo4j query failed (non-fatal): %s", e)

    # --- 2. Collect metadata from Redis discovery cache ---
    client = await redis_service.get_client(settings.redis_url)

    def _schema_facet(columns: list[dict[str, str]]) -> dict[str, Any]:
        return {
            "_producer": "https://ekai.io/ekaix/v1",
            "_schemaURL": "https://openlineage.io/spec/facets/1-1-1/SchemaDatasetFacet.json#/$defs/SchemaDatasetFacet",
            "fields": [{"name": c.get("name", ""), "type": c.get("type", "VARCHAR")} for c in columns],
        }

    # --- 3. Build input datasets (source tables) ---
    inputs: list[dict[str, Any]] = []
    all_source_fqns = set(source_to_silver.keys())
    # Also include sources that map directly to gold (no silver layer)
    if not all_source_fqns:
        # If no silver layer, check working_layer cache
        wl_key = f"cache:working_layer:{data_product_id}"
        wl_map = await redis_service.get_json(client, wl_key) or {}
        all_source_fqns = set(wl_map.keys())

    for src_fqn in all_source_fqns:
        ns = f"snowflake://{account}"
        # Try to get column metadata from Redis
        meta_key = f"cache:metadata:{src_fqn.upper()}"
        meta = await redis_service.get_json(client, meta_key)
        columns: list[dict[str, str]] = []
        if meta and isinstance(meta, list):
            columns = [{"name": c.get("COLUMN_NAME", c.get("column_name", "")),
                         "type": c.get("DATA_TYPE", c.get("data_type", "VARCHAR"))}
                        for c in meta]

        # Quality facet from discovery profile
        profile_key = f"cache:profile:{src_fqn.upper()}"
        profile = await redis_service.get_json(client, profile_key)
        facets: dict[str, Any] = {}
        if columns:
            facets["schema"] = _schema_facet(columns)
        if profile and isinstance(profile, dict):
            row_count = profile.get("row_count", 0)
            col_metrics: dict[str, Any] = {}
            for col_profile in profile.get("columns", []):
                cname = col_profile.get("column_name", "")
                if cname:
                    col_metrics[cname] = {
                        "nullCount": col_profile.get("null_count", 0),
                        "distinctCount": col_profile.get("distinct_count", 0),
                    }
            facets["dataQualityMetrics"] = {
                "_producer": "https://ekai.io/ekaix/v1",
                "_schemaURL": "https://openlineage.io/spec/facets/1-0-2/DataQualityMetricsInputDatasetFacet.json#/$defs/DataQualityMetricsInputDatasetFacet",
                "rowCount": row_count,
                "columnMetrics": col_metrics,
            }

        inputs.append({
            "namespace": ns,
            "name": src_fqn.upper(),
            "facets": facets,
        })

    # --- 4. Build output datasets (gold tables) ---
    outputs: list[dict[str, Any]] = []
    all_gold_fqns = set(silver_to_gold.values())

    for gold_fqn in all_gold_fqns:
        parts = gold_fqn.upper().split(".")
        # Get gold table columns from Snowflake
        gold_columns: list[dict[str, str]] = []
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(f'SHOW COLUMNS IN TABLE "{parts[0]}"."{parts[1]}"."{parts[2]}"' if len(parts) == 3
                        else f"SHOW COLUMNS IN TABLE {gold_fqn}")
            rows = cur.fetchall()
            desc = [d[0] for d in cur.description] if cur.description else []
            for row in rows:
                row_dict = dict(zip(desc, row))
                gold_columns.append({
                    "name": row_dict.get("column_name", ""),
                    "type": row_dict.get("data_type", "VARCHAR"),
                })
            cur.close()
        except Exception as e:
            logger.warning("OpenLineage: failed to fetch gold columns for %s: %s", gold_fqn, e)

        # Column-level lineage: best-effort name matching
        # Find the source FQN chain for this gold table
        source_fqn_for_gold = ""
        for slv, gld in silver_to_gold.items():
            if gld.upper() == gold_fqn.upper():
                # Find original source for this silver
                for src, s in source_to_silver.items():
                    if s.upper() == slv.upper():
                        source_fqn_for_gold = src.upper()
                        break
                if not source_fqn_for_gold:
                    source_fqn_for_gold = slv.upper()
                break

        col_lineage_fields: dict[str, Any] = {}
        if source_fqn_for_gold and gold_columns:
            # Get source columns for matching
            src_meta_key = f"cache:metadata:{source_fqn_for_gold}"
            src_meta = await redis_service.get_json(client, src_meta_key)
            src_col_names: set[str] = set()
            if src_meta and isinstance(src_meta, list):
                src_col_names = {c.get("COLUMN_NAME", c.get("column_name", "")).upper()
                                  for c in src_meta}

            for gc in gold_columns:
                gc_name = gc["name"].upper()
                if gc_name in src_col_names:
                    col_lineage_fields[gc_name] = {
                        "inputFields": [{
                            "namespace": f"snowflake://{account}",
                            "name": source_fqn_for_gold,
                            "field": gc_name,
                        }],
                        "transformationType": "IDENTITY",
                    }

        gold_ns = f"snowflake://{account}"
        gold_facets: dict[str, Any] = {}
        if gold_columns:
            gold_facets["schema"] = _schema_facet(gold_columns)
        if col_lineage_fields:
            gold_facets["columnLineage"] = {
                "_producer": "https://ekai.io/ekaix/v1",
                "_schemaURL": "https://openlineage.io/spec/facets/1-1-0/ColumnLineageDatasetFacet.json#/$defs/ColumnLineageDatasetFacet",
                "fields": col_lineage_fields,
            }

        outputs.append({
            "namespace": gold_ns,
            "name": gold_fqn.upper(),
            "facets": gold_facets,
        })

    # --- 5. Assemble RunEvent ---
    return {
        "eventType": "COMPLETE",
        "eventTime": now,
        "producer": "https://ekai.io/ekaix/v1",
        "schemaURL": "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent",
        "run": {
            "runId": data_product_id,
            "facets": {
                "processing_engine": {
                    "_producer": "https://ekai.io/ekaix/v1",
                    "_schemaURL": "https://openlineage.io/spec/facets/1-1-1/ProcessingEngineRunFacet.json#/$defs/ProcessingEngineRunFacet",
                    "version": "1.0",
                    "name": "ekaiX AIXcelerator",
                },
            },
        },
        "job": {
            "namespace": "ekaix",
            "name": dp_name,
            "facets": {
                "jobType": {
                    "_producer": "https://ekai.io/ekaix/v1",
                    "_schemaURL": "https://openlineage.io/spec/facets/2-0-2/JobTypeJobFacet.json#/$defs/JobTypeJobFacet",
                    "processingType": "BATCH",
                    "integration": "SNOWFLAKE",
                    "jobType": "TRANSFORMATION",
                },
            },
        },
        "inputs": inputs,
        "outputs": outputs,
    }


@tool
async def save_openlineage_artifact(data_product_id: str) -> str:
    """Generate and save an OpenLineage JSON artifact documenting the complete data lineage.

    Builds an OpenLineage v2 RunEvent from Neo4j lineage graph and Redis metadata cache.
    Stores the JSON in MinIO and registers the artifact in PostgreSQL.

    Args:
        data_product_id: UUID of the data product.
    """
    try:
        ol_json = await _generate_openlineage_json(data_product_id)

        content = json.dumps(ol_json, indent=2, default=str)

        from tools.minio_tools import upload_artifact_programmatic
        result = await upload_artifact_programmatic(
            data_product_id=data_product_id,
            artifact_type="lineage",
            filename="openlineage.json",
            content=content,
        )

        logger.info(
            "Saved OpenLineage artifact for %s: %d inputs, %d outputs",
            data_product_id, len(ol_json.get("inputs", [])), len(ol_json.get("outputs", [])),
        )

        return json.dumps({
            "status": "success",
            "artifact_id": result.get("artifact_id", ""),
            "version": result.get("version", 1),
            "input_count": len(ol_json.get("inputs", [])),
            "output_count": len(ol_json.get("outputs", [])),
        })

    except Exception as e:
        logger.error("save_openlineage_artifact failed: %s", e)
        return json.dumps({"status": "error", "message": str(e)})
