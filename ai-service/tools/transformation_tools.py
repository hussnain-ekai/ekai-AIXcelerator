"""LangChain tools for data transformation operations.

Tools create Snowflake VIEWs in the EKAIX.{DP}_CURATED schema to transform
bronze/silver data into curated-quality tables suitable for semantic modeling.

Two-tier DDL generation:
    - Tier 1: Snowflake Cortex AI (Arctic) generates DDL natively inside Snowflake
    - Tier 2: Template-based assembler with safe_cast rules (fallback)
    - EXPLAIN validation before DDL execution catches errors early
    - Batch processing handles all tables in one tool call
"""

import json
import logging
from typing import Any

from langchain_core.tools import tool

from config import get_settings
from services.snowflake import get_connection
from tools.ddl import (
    execute_ddl,
    extract_create_statement,
    generate_ddl_via_cortex,
    is_date_type,
    is_numeric_type,
    is_text_type,
    resolve_fqn_from_allowed,
    safe_cast,
    tool_error,
    validate_ddl_with_explain,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_curated_schema() -> str:
    """Return the curated schema name for the current data product."""
    from tools.postgres_tools import get_data_product_name
    from tools.naming import curated_schema
    name = get_data_product_name()
    if not name:
        raise RuntimeError("Data product name not set in context")
    return curated_schema(name)


def _profile_table(table_fqn: str) -> dict[str, Any]:
    """Profile a source table and return structured results.

    Returns dict with keys: table, row_count, sample_size, duplicate_rate,
    columns (list of column profiles), maturity.
    """
    from tools.snowflake_tools import _validate_fqn, _quoted_fqn

    table_fqn = resolve_fqn_from_allowed(table_fqn)
    parts, err = _validate_fqn(table_fqn)
    if err:
        raise ValueError(f"Invalid FQN {table_fqn}: {err}")

    quoted = _quoted_fqn(parts)
    conn = get_connection()
    cur = conn.cursor()

    # Get row count
    cur.execute(f"SELECT COUNT(*) AS cnt FROM {quoted}")
    row_count = cur.fetchone()[0] or 0

    # Get columns
    cur.execute(f"SHOW COLUMNS IN TABLE {quoted}")
    raw_cols = cur.fetchall()
    col_desc = [d[0] for d in cur.description] if cur.description else []

    columns: list[dict[str, Any]] = []
    for row in raw_cols:
        row_dict = dict(zip(col_desc, row))
        columns.append({
            "column_name": row_dict.get("column_name", ""),
            "data_type": row_dict.get("data_type", ""),
        })

    if not columns:
        cur.close()
        return {"table": table_fqn, "row_count": row_count, "columns": [], "sample_size": 0, "duplicate_rate": 0.0, "maturity": {}}

    # Batch profile: null counts, distinct counts
    exprs = []
    for col in columns:
        cn = col["column_name"]
        if cn:
            exprs.append(
                f'COUNT("{cn}") AS "nn_{cn}", '
                f'APPROX_COUNT_DISTINCT("{cn}") AS "dc_{cn}"'
            )

    limit = min(row_count, 10000)
    from_clause = f"(SELECT * FROM {quoted} LIMIT {limit})" if row_count > 10000 else quoted

    batch_row: dict[str, Any] = {}
    if exprs:
        batch_sql = f'SELECT COUNT(*) AS "_sample_n", {", ".join(exprs)} FROM {from_clause}'
        cur.execute(batch_sql)
        batch_desc = [d[0] for d in cur.description] if cur.description else []
        batch_result = cur.fetchone()
        if batch_result:
            batch_row = dict(zip(batch_desc, batch_result))

    sample_n = batch_row.get("_sample_n", 0) or 0

    # Duplicate rate via HASH(*)
    dup_rate = 0.0
    if sample_n > 0:
        try:
            cur.execute(
                f"SELECT COUNT(*) AS total, COUNT(DISTINCT HASH(*)) AS distinct_hashes "
                f"FROM (SELECT * FROM {quoted} LIMIT {limit})"
            )
            dup_row = cur.fetchone()
            if dup_row:
                total, distinct = dup_row[0] or 0, dup_row[1] or 0
                if total > 0:
                    dup_rate = max(0.0, 1.0 - distinct / total)
        except Exception:
            pass

    # Build column profiles
    profile_cols = []
    for col in columns:
        cn = col["column_name"]
        if not cn:
            continue
        non_null = batch_row.get(f"nn_{cn}", 0) or 0
        distinct = batch_row.get(f"dc_{cn}", 0) or 0
        null_pct = round((1 - non_null / sample_n) * 100, 2) if sample_n > 0 else 0
        uniqueness_pct = round((distinct / non_null) * 100, 2) if non_null > 0 else 0

        profile_cols.append({
            "column": cn,
            "data_type": col["data_type"],
            "null_pct": null_pct,
            "uniqueness_pct": uniqueness_pct,
            "distinct_count": distinct,
            "is_likely_pk": uniqueness_pct > 98 and null_pct == 0,
        })

    # Maturity classification
    from agents.discovery import classify_data_maturity
    maturity = classify_data_maturity(profile_cols, duplicate_rate=dup_rate)

    cur.close()

    return {
        "table": table_fqn,
        "row_count": row_count,
        "sample_size": sample_n,
        "duplicate_rate": round(dup_rate, 4),
        "columns": profile_cols,
        "maturity": maturity,
    }


def _generate_ddl_from_template(
    source_fqn: str,
    target_fqn: str,
    transforms: list[dict[str, Any]],
) -> str:
    """Generate CREATE OR REPLACE VIEW DDL from transformation specs.

    Includes ALL source columns: applies requested transforms to specified
    columns and passes through all remaining columns unchanged.
    """
    from tools.snowflake_tools import _validate_fqn, _quoted_fqn

    source_fqn = resolve_fqn_from_allowed(source_fqn)

    src_parts, src_err = _validate_fqn(source_fqn)
    if src_err:
        raise ValueError(f"Invalid source FQN: {src_err}")

    tgt_parts, tgt_err = _validate_fqn(target_fqn)
    if tgt_err:
        raise ValueError(f"Invalid target FQN: {tgt_err}")

    quoted_src = _quoted_fqn(src_parts)
    quoted_tgt = _quoted_fqn(tgt_parts)

    # Fetch ALL source columns so we can pass through non-transformed ones
    all_source_cols: list[str] = []
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(f"SHOW COLUMNS IN TABLE {quoted_src}")
        for row in cur.fetchall():
            all_source_cols.append(row[2])  # column_name at index 2
        cur.close()
    except Exception as e:
        logger.warning("Could not fetch source columns for %s: %s", source_fqn, e)

    select_exprs: list[str] = []
    dedup_partition_cols: list[str] = []
    dedup_order_col: str = ""

    # Track which columns have explicit transforms (case-insensitive)
    transformed_col_upper: set[str] = set()
    for t in transforms:
        col = t.get("column", "")
        if col and t.get("type") != "dedup":
            transformed_col_upper.add(col.upper())

    for t in transforms:
        col = t.get("column", "")
        transform_type = t.get("type", "pass_through")
        target_name = t.get("target_name", col)
        # Use actual column name from source if available (preserves case)
        actual_col = col
        for sc in all_source_cols:
            if sc.upper() == col.upper():
                actual_col = sc
                break
        quoted_col = f'"{actual_col}"'
        quoted_target = f'"{target_name}"'

        if transform_type == "pass_through":
            if col != target_name:
                select_exprs.append(f"{quoted_col} AS {quoted_target}")
            else:
                select_exprs.append(quoted_col)

        elif transform_type == "cast":
            target_type = t.get("target_type", "VARCHAR")
            source_type = t.get("source_type", "VARCHAR")
            default_val = t.get("default", None)
            cast_expr = safe_cast(quoted_col, source_type, target_type)
            if default_val is not None:
                expr = f"COALESCE({cast_expr}, {default_val})"
            else:
                expr = cast_expr
            select_exprs.append(f"{expr} AS {quoted_target}")

        elif transform_type == "coalesce":
            default_val = t.get("default", "''")
            select_exprs.append(f"COALESCE({quoted_col}, {default_val}) AS {quoted_target}")

        elif transform_type == "rename":
            select_exprs.append(f"{quoted_col} AS {quoted_target}")

        elif transform_type == "dedup":
            dedup_partition_cols = t.get("partition_by", [])
            dedup_order_col = t.get("order_by", "")

        elif transform_type == "expression":
            expr = t.get("expression", quoted_col)
            select_exprs.append(f"{expr} AS {quoted_target}")

        else:
            select_exprs.append(quoted_col)

    # Add pass-through for ALL remaining source columns not in transforms
    for col in all_source_cols:
        if col.upper() not in transformed_col_upper:
            select_exprs.append(f'"{col}"')

    if not select_exprs:
        raise ValueError("No columns to select")

    select_clause = ",\n    ".join(select_exprs)

    if dedup_partition_cols and dedup_order_col:
        partition_cols = ", ".join(f'"{c}"' for c in dedup_partition_cols)
        ddl = f"""CREATE OR REPLACE VIEW {quoted_tgt} AS
WITH _ranked AS (
  SELECT
    {select_clause},
    ROW_NUMBER() OVER (
      PARTITION BY {partition_cols}
      ORDER BY "{dedup_order_col}" DESC
    ) AS _rn
  FROM {quoted_src}
)
SELECT * EXCLUDE (_rn) FROM _ranked WHERE _rn = 1"""
    else:
        ddl = f"""CREATE OR REPLACE VIEW {quoted_tgt} AS
SELECT
    {select_clause}
FROM {quoted_src}"""

    return ddl


def _build_cortex_prompt_for_view(
    source_fqn: str,
    target_fqn: str,
    column_profiles: list[dict[str, Any]],
    transform_specs: list[dict[str, Any]],
) -> str:
    """Build a Cortex AI prompt for VIEW DDL generation."""
    from tools.snowflake_tools import _validate_fqn, _quoted_fqn

    tgt_parts, tgt_err = _validate_fqn(target_fqn)
    quoted_tgt = _quoted_fqn(tgt_parts) if not tgt_err else target_fqn

    col_lines = []
    for cp in column_profiles:
        col_lines.append(
            f"  {cp['column']}: {cp['data_type']} "
            f"(null_pct: {cp.get('null_pct', 0)}%, distinct: {cp.get('distinct_count', 0)})"
        )

    transform_lines = []
    for ts in transform_specs:
        col = ts.get("column", "")
        t_type = ts.get("type", "pass_through")
        if t_type == "cast":
            transform_lines.append(f"  - {col}: cast from {ts.get('source_type', '?')} to {ts.get('target_type', '?')}")
        elif t_type == "coalesce":
            transform_lines.append(f"  - {col}: coalesce with default {ts.get('default', 'NULL')}")
        elif t_type == "rename":
            transform_lines.append(f"  - {col}: rename to {ts.get('target_name', col)}")
        elif t_type == "dedup":
            transform_lines.append(f"  - dedup: partition by {ts.get('partition_by', [])}, order by {ts.get('order_by', '')}")
        elif t_type == "expression":
            transform_lines.append(f"  - {ts.get('target_name', col)}: expression {ts.get('expression', '')}")
        else:
            transform_lines.append(f"  - {col}: pass through unchanged")

    return f"""Generate a Snowflake CREATE OR REPLACE VIEW statement.
Source table: {source_fqn}
Target view: {quoted_tgt}

Source columns:
{chr(10).join(col_lines)}

Transformations requested:
{chr(10).join(transform_lines)}

Rules:
- Use TRY_TO_DATE, TRY_TO_TIMESTAMP, TRY_CAST for safe type conversions
- Use COALESCE for null handling
- Quote all column names with double quotes
- For numeric to date conversions, use TRY_TO_DATE(TO_VARCHAR(col))
- Return ONLY the SQL statement, no explanation, no markdown fences"""


def _validate_result(source_fqn: str, target_fqn: str) -> dict[str, Any]:
    """Validate a transformation by comparing source and target tables."""
    from tools.snowflake_tools import _validate_fqn, _quoted_fqn

    source_fqn = resolve_fqn_from_allowed(source_fqn)

    src_parts, src_err = _validate_fqn(source_fqn)
    if src_err:
        raise ValueError(f"Invalid source: {src_err}")

    tgt_parts, tgt_err = _validate_fqn(target_fqn)
    if tgt_err:
        raise ValueError(f"Invalid target: {tgt_err}")

    quoted_src = _quoted_fqn(src_parts)
    quoted_tgt = _quoted_fqn(tgt_parts)

    checks: dict[str, Any] = {"source": source_fqn, "target": target_fqn, "passed": True, "issues": []}

    conn = get_connection()
    cur = conn.cursor()

    # Row counts
    cur.execute(f"SELECT COUNT(*) FROM {quoted_src}")
    src_count = cur.fetchone()[0] or 0
    cur.execute(f"SELECT COUNT(*) FROM {quoted_tgt}")
    tgt_count = cur.fetchone()[0] or 0

    checks["source_row_count"] = src_count
    checks["target_row_count"] = tgt_count

    if tgt_count == 0 and src_count > 0:
        checks["passed"] = False
        checks["issues"].append("Target table has 0 rows but source has data")
    elif src_count > 0:
        ratio = tgt_count / src_count
        checks["row_ratio"] = round(ratio, 4)
        if ratio > 1.05:
            checks["issues"].append(
                f"Target has MORE rows ({tgt_count}) than source ({src_count}) -- possible fan-out"
            )

    # Column types
    cur.execute(f"SHOW COLUMNS IN TABLE {quoted_tgt}")
    tgt_cols = cur.fetchall()
    col_desc = [d[0] for d in cur.description] if cur.description else []
    tgt_col_types = {}
    for row in tgt_cols:
        row_dict = dict(zip(col_desc, row))
        tgt_col_types[row_dict.get("column_name", "")] = row_dict.get("data_type", "")
    checks["target_columns"] = len(tgt_col_types)

    # Null rates on target (sample)
    if tgt_col_types:
        null_exprs = [
            f'ROUND((1 - COUNT("{cn}") / NULLIF(COUNT(*), 0)) * 100, 2) AS "null_{cn}"'
            for cn in list(tgt_col_types.keys())[:20]
            if cn
        ]
        if null_exprs:
            null_sql = f"SELECT COUNT(*) AS cnt, {', '.join(null_exprs)} FROM {quoted_tgt}"
            cur.execute(null_sql)
            null_desc = [d[0] for d in cur.description] if cur.description else []
            null_result = cur.fetchone()
            if null_result:
                null_data = dict(zip(null_desc, null_result))
                high_null_cols = [
                    k.replace("null_", "") for k, v in null_data.items()
                    if k.startswith("null_") and v is not None and v > 50
                ]
                if high_null_cols:
                    checks["issues"].append(
                        f"High null rate (>50%) in target columns: {', '.join(high_null_cols)}"
                    )

    # Sample rows
    cur.execute(f"SELECT * FROM {quoted_tgt} LIMIT 5")
    sample_desc = [d[0] for d in cur.description] if cur.description else []
    sample_rows = cur.fetchall()
    checks["sample_rows"] = [dict(zip(sample_desc, row)) for row in sample_rows]

    cur.close()

    if checks["issues"]:
        checks["passed"] = False

    return checks


async def _register_layer(data_product_id: str, mapping: dict[str, str]) -> None:
    """Register the working layer mapping to Redis and PostgreSQL."""
    from services import redis as redis_service
    from services import postgres as pg_service

    settings = get_settings()

    client = await redis_service.get_client(settings.redis_url)
    cache_key = f"cache:working_layer:{data_product_id}"
    await redis_service.set_json(client, cache_key, mapping, ttl=86400)

    if pg_service._pool is not None:
        pool = pg_service._pool
        sql = """
        UPDATE data_products
        SET state = COALESCE(state, '{}'::jsonb) || jsonb_build_object('working_layer', $1::jsonb)
        WHERE id = $2::uuid
        """
        await pg_service.execute(pool, sql, json.dumps(mapping), data_product_id)

    logger.info(
        "Registered working layer for %s: %d table mappings",
        data_product_id, len(mapping),
    )


# ---------------------------------------------------------------------------
# @tool functions (public API)
# ---------------------------------------------------------------------------


@tool
def profile_source_table(table_fqn: str) -> str:
    """Profile a source table for transformation planning.

    Returns column types, null percentages, distinct counts, duplicate rate,
    and data maturity classification.

    Args:
        table_fqn: Fully qualified table name (DATABASE.SCHEMA.TABLE)
    """
    try:
        result = _profile_table(table_fqn)
        return json.dumps(result, default=str)
    except Exception as e:
        logger.error("profile_source_table failed for %s: %s", table_fqn, e)
        return tool_error("profile_source_table", str(e), table=table_fqn)


@tool
async def register_transformed_layer(
    data_product_id: str,
    table_mapping_json: str,
) -> str:
    """Register transformed tables as the working layer for semantic modeling.

    Stores a mapping of original FQNs to transformed FQNs so downstream
    agents use the clean versions.

    Args:
        data_product_id: UUID of the data product
        table_mapping_json: JSON object mapping original FQN to transformed FQN.
    """
    try:
        mapping = json.loads(table_mapping_json)
    except json.JSONDecodeError as e:
        return tool_error("register_transformed_layer", f"Invalid JSON: {e}")

    if not isinstance(mapping, dict) or not mapping:
        return tool_error("register_transformed_layer", "Mapping must be a non-empty JSON object")

    try:
        await _register_layer(data_product_id, mapping)
        return json.dumps({
            "status": "success",
            "data_product_id": data_product_id,
            "table_count": len(mapping),
            "mapping": mapping,
        })
    except Exception as e:
        logger.error("register_transformed_layer failed: %s", e)
        return tool_error("register_transformed_layer", str(e))


@tool
async def transform_tables_batch(
    data_product_id: str,
    transform_plan_json: str,
) -> str:
    """Execute transformations for all tables in a single batch.

    Processes each table: profile -> Cortex DDL (Tier 1) -> EXPLAIN validate ->
    template fallback (Tier 2) -> execute -> verify -> register.

    Args:
        data_product_id: UUID of the data product
        transform_plan_json: JSON object with source table FQNs as keys.
            Each value has "target_fqn" and "transformations" (array of specs).
    """
    try:
        plan = json.loads(transform_plan_json)
    except json.JSONDecodeError as e:
        return tool_error("transform_tables_batch", f"Invalid JSON: {e}")

    if not isinstance(plan, dict) or not plan:
        return tool_error("transform_tables_batch", "Plan must be a non-empty JSON object")

    results: list[dict[str, Any]] = []
    table_mapping: dict[str, str] = {}

    for raw_source_fqn, spec in plan.items():
        source_fqn = resolve_fqn_from_allowed(raw_source_fqn)

        table_result: dict[str, Any] = {
            "source": source_fqn,
            "target": spec.get("target_fqn", ""),
            "status": "pending",
            "tier_used": None,
            "issues": [],
        }

        target_fqn = spec.get("target_fqn", "")
        transforms = spec.get("transformations", [])

        if not target_fqn or not transforms:
            table_result["status"] = "failed"
            table_result["issues"].append("Missing target_fqn or transformations")
            results.append(table_result)
            continue

        # Step 1: Profile
        try:
            profile = _profile_table(source_fqn)
            table_result["source_row_count"] = profile.get("row_count", 0)
            column_profiles = profile.get("columns", [])
        except Exception as e:
            table_result["status"] = "failed"
            table_result["issues"].append(f"Profiling failed: {e}")
            results.append(table_result)
            continue

        # Step 2: Try Cortex DDL (Tier 1)
        ddl: str | None = None
        tier_used = "template"

        prompt = _build_cortex_prompt_for_view(source_fqn, target_fqn, column_profiles, transforms)
        cortex_ddl = generate_ddl_via_cortex(prompt)
        if cortex_ddl:
            is_valid, explain_err = validate_ddl_with_explain(cortex_ddl)
            if is_valid:
                ddl = cortex_ddl
                tier_used = "cortex"
                logger.info("Cortex DDL passed EXPLAIN for %s", source_fqn)
            else:
                logger.warning("Cortex DDL failed EXPLAIN for %s: %s", source_fqn, explain_err[:150])

        # Step 3: Template fallback (Tier 2)
        if ddl is None:
            try:
                ddl = _generate_ddl_from_template(source_fqn, target_fqn, transforms)
                is_valid, explain_err = validate_ddl_with_explain(ddl)
                if not is_valid:
                    table_result["status"] = "failed"
                    table_result["tier_used"] = "template"
                    table_result["issues"].append(f"Template DDL failed EXPLAIN: {explain_err[:200]}")
                    results.append(table_result)
                    continue
            except Exception as e:
                table_result["status"] = "failed"
                table_result["tier_used"] = "template"
                table_result["issues"].append(f"Template DDL generation failed: {e}")
                results.append(table_result)
                continue

        table_result["tier_used"] = tier_used

        # Step 4: Execute DDL
        success, exec_msg = execute_ddl(ddl)
        if not success:
            if tier_used == "cortex":
                logger.warning("Cortex DDL execution failed for %s, retrying with template", source_fqn)
                try:
                    ddl = _generate_ddl_from_template(source_fqn, target_fqn, transforms)
                    is_valid, _ = validate_ddl_with_explain(ddl)
                    if is_valid:
                        success, exec_msg = execute_ddl(ddl)
                        if success:
                            table_result["tier_used"] = "template (cortex retry)"
                except Exception:
                    pass

            if not success:
                table_result["status"] = "failed"
                table_result["issues"].append(f"DDL execution failed: {exec_msg}")
                results.append(table_result)
                continue

        # Step 5: Validate result
        try:
            validation = _validate_result(source_fqn, target_fqn)
            table_result["target_row_count"] = validation.get("target_row_count", 0)
            table_result["target_columns"] = validation.get("target_columns", 0)

            if validation.get("passed"):
                table_result["status"] = "success"
                table_mapping[source_fqn] = target_fqn
            else:
                val_issues = validation.get("issues", [])
                critical = [i for i in val_issues if "0 rows" in i or "fan-out" in i]
                if critical:
                    table_result["status"] = "warning"
                    table_result["issues"].extend(val_issues)
                else:
                    table_result["status"] = "success"
                    table_result["issues"].extend(val_issues)
                    table_mapping[source_fqn] = target_fqn
        except Exception as e:
            table_result["status"] = "warning"
            table_result["issues"].append(f"Validation query failed: {e}")
            table_mapping[source_fqn] = target_fqn

        results.append(table_result)

    # Register working layer
    if table_mapping:
        try:
            await _register_layer(data_product_id, table_mapping)
        except Exception as e:
            logger.error("Batch: register_layer failed: %s", e)

    success_count = sum(1 for r in results if r["status"] == "success")
    warning_count = sum(1 for r in results if r["status"] == "warning")
    failed_count = sum(1 for r in results if r["status"] == "failed")

    return json.dumps({
        "summary": {
            "total": len(results),
            "success": success_count,
            "warning": warning_count,
            "failed": failed_count,
            "tables_registered": len(table_mapping),
        },
        "tables": results,
        "mapping": table_mapping,
    }, default=str)
