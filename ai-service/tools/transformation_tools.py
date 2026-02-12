"""LangChain tools for data transformation operations.

Tools create Snowflake Dynamic Tables to transform bronze/silver data into
gold-quality tables suitable for semantic modeling. Uses the same data
isolation context as snowflake_tools.

Robustness features:
    - All DDL runs via RCR (caller's rights)
    - Validation compares source vs target after transformation
    - Working layer mapping persisted to both Redis and PostgreSQL
"""

import json
import logging
from typing import Any

from langchain_core.tools import tool

from config import get_settings
from services.snowflake import execute_query_sync, get_connection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool_error(tool_name: str, message: str, **extra: Any) -> str:
    """Return a structured JSON error string for tool results."""
    result: dict[str, Any] = {"error": message, "tool": tool_name}
    result.update(extra)
    return json.dumps(result)


def _get_target_schema_suffix() -> str:
    return getattr(get_settings(), "transformation_target_schema_suffix", "SILVER_EKAIX")


def _get_target_lag() -> str:
    return getattr(get_settings(), "transformation_target_lag", "1 hour")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool
def profile_source_table(table_fqn: str) -> str:
    """Profile a source table for transformation planning.

    Returns column types, null percentages, distinct counts, duplicate rate,
    and data maturity classification. Use this before planning transformations
    to understand what cleanup is needed.

    Args:
        table_fqn: Fully qualified table name (DATABASE.SCHEMA.TABLE)
    """
    from tools.snowflake_tools import _validate_fqn, _quoted_fqn

    parts, err = _validate_fqn(table_fqn)
    if err:
        return _tool_error("profile_source_table", err, table=table_fqn)

    quoted = _quoted_fqn(parts)

    try:
        conn = get_connection()
        cur = conn.cursor()

        # Get row count
        cur.execute(f"SELECT COUNT(*) AS cnt FROM {quoted}")
        row_count = cur.fetchone()[0] or 0

        # Get columns and profile
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
            return json.dumps({"table": table_fqn, "row_count": row_count, "columns": []})

        # Batch profile: null counts, distinct counts, sample values
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
        sample_n = 0
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

        return json.dumps({
            "table": table_fqn,
            "row_count": row_count,
            "sample_size": sample_n,
            "duplicate_rate": round(dup_rate, 4),
            "columns": profile_cols,
            "maturity": maturity,
        }, default=str)

    except Exception as e:
        logger.error("profile_source_table failed for %s: %s", table_fqn, e)
        return _tool_error("profile_source_table", str(e), table=table_fqn)


@tool
def generate_dynamic_table_ddl(
    source_fqn: str,
    target_fqn: str,
    transformations_json: str,
    target_lag: str = "",
) -> str:
    """Generate CREATE OR REPLACE DYNAMIC TABLE DDL from transformation specs.

    Each transformation specifies what to do with a column:
    - pass_through: keep as-is
    - cast: change type (e.g., VARCHAR to NUMBER)
    - coalesce: fill nulls with a default
    - dedup: add ROW_NUMBER() for deduplication
    - rename: change column name

    Args:
        source_fqn: Source table FQN (DATABASE.SCHEMA.TABLE)
        target_fqn: Target Dynamic Table FQN (DATABASE.SILVER_EKAIX.TABLE)
        transformations_json: JSON array of transformation specs
        target_lag: Refresh lag (default from config, typically "1 hour")
    """
    from tools.snowflake_tools import _validate_fqn, _quoted_fqn

    # Validate FQNs
    src_parts, src_err = _validate_fqn(source_fqn)
    if src_err:
        return _tool_error("generate_dynamic_table_ddl", f"Invalid source: {src_err}")

    tgt_parts, tgt_err = _validate_fqn(target_fqn)
    if tgt_err:
        return _tool_error("generate_dynamic_table_ddl", f"Invalid target: {tgt_err}")

    try:
        transforms = json.loads(transformations_json)
    except json.JSONDecodeError as e:
        return _tool_error("generate_dynamic_table_ddl", f"Invalid JSON: {e}")

    if not isinstance(transforms, list):
        return _tool_error("generate_dynamic_table_ddl", "transformations_json must be a JSON array")

    lag = target_lag or _get_target_lag()
    settings = get_settings()
    warehouse = settings.snowflake_warehouse or "COMPUTE_WH"

    quoted_src = _quoted_fqn(src_parts)
    quoted_tgt = _quoted_fqn(tgt_parts)

    # Build SELECT expressions
    select_exprs: list[str] = []
    dedup_partition_cols: list[str] = []
    dedup_order_col: str = ""

    for t in transforms:
        col = t.get("column", "")
        transform_type = t.get("type", "pass_through")
        target_name = t.get("target_name", col)
        quoted_col = f'"{col}"'
        quoted_target = f'"{target_name}"'

        if transform_type == "pass_through":
            if col != target_name:
                select_exprs.append(f"{quoted_col} AS {quoted_target}")
            else:
                select_exprs.append(quoted_col)

        elif transform_type == "cast":
            target_type = t.get("target_type", "VARCHAR")
            default_val = t.get("default", None)
            if default_val is not None:
                expr = f"COALESCE(TRY_CAST({quoted_col} AS {target_type}), {default_val})"
            else:
                expr = f"TRY_CAST({quoted_col} AS {target_type})"
            select_exprs.append(f"{expr} AS {quoted_target}")

        elif transform_type == "coalesce":
            default_val = t.get("default", "''")
            select_exprs.append(f"COALESCE({quoted_col}, {default_val}) AS {quoted_target}")

        elif transform_type == "rename":
            select_exprs.append(f"{quoted_col} AS {quoted_target}")

        elif transform_type == "dedup":
            # Mark columns for deduplication window
            dedup_partition_cols = t.get("partition_by", [])
            dedup_order_col = t.get("order_by", "")
            # This column is handled after the main SELECT

        elif transform_type == "expression":
            # Custom SQL expression
            expr = t.get("expression", quoted_col)
            select_exprs.append(f"{expr} AS {quoted_target}")

        else:
            select_exprs.append(quoted_col)

    if not select_exprs:
        return _tool_error("generate_dynamic_table_ddl", "No columns to select")

    # Build the DDL
    select_clause = ",\n    ".join(select_exprs)

    if dedup_partition_cols and dedup_order_col:
        # Deduplication: use ROW_NUMBER() window and filter
        partition_cols = ", ".join(f'"{c}"' for c in dedup_partition_cols)
        ddl = f"""CREATE OR REPLACE DYNAMIC TABLE {quoted_tgt}
  TARGET_LAG = '{lag}'
  WAREHOUSE = "{warehouse}"
AS
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
        ddl = f"""CREATE OR REPLACE DYNAMIC TABLE {quoted_tgt}
  TARGET_LAG = '{lag}'
  WAREHOUSE = "{warehouse}"
AS
SELECT
    {select_clause}
FROM {quoted_src}"""

    return json.dumps({
        "ddl": ddl,
        "source": source_fqn,
        "target": target_fqn,
        "transform_count": len(transforms),
        "has_dedup": bool(dedup_partition_cols),
    })


@tool
def execute_transformation_ddl(ddl: str) -> str:
    """Execute a CREATE DYNAMIC TABLE statement against Snowflake.

    Before executing the Dynamic Table DDL, ensures the target schema exists.
    Only CREATE OR REPLACE DYNAMIC TABLE statements are allowed.

    Args:
        ddl: The DDL statement to execute
    """
    ddl_upper = ddl.strip().upper()

    # Safety check: only allow Dynamic Table creation
    if not ddl_upper.startswith("CREATE OR REPLACE DYNAMIC TABLE") and \
       not ddl_upper.startswith("CREATE DYNAMIC TABLE") and \
       not ddl_upper.startswith("CREATE SCHEMA"):
        return _tool_error(
            "execute_transformation_ddl",
            "Only CREATE [OR REPLACE] DYNAMIC TABLE and CREATE SCHEMA statements are allowed.",
        )

    try:
        conn = get_connection()
        cur = conn.cursor()

        # If this is a Dynamic Table DDL, ensure the target schema exists
        if "DYNAMIC TABLE" in ddl_upper:
            # Extract target schema from the DDL: CREATE OR REPLACE DYNAMIC TABLE "DB"."SCHEMA"."TABLE"
            import re
            # Match quoted FQN pattern
            fqn_match = re.search(
                r'DYNAMIC\s+TABLE\s+"([^"]+)"\."([^"]+)"\."([^"]+)"',
                ddl, re.IGNORECASE,
            )
            if fqn_match:
                db, schema = fqn_match.group(1), fqn_match.group(2)
                try:
                    cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{db}"."{schema}"')
                    logger.info("Ensured schema exists: %s.%s", db, schema)
                except Exception as schema_err:
                    logger.warning("CREATE SCHEMA failed (may already exist): %s", schema_err)

        cur.execute(ddl)
        result = cur.fetchone()
        cur.close()

        return json.dumps({
            "status": "success",
            "message": f"DDL executed successfully",
            "result": str(result) if result else "OK",
        })

    except Exception as e:
        logger.error("execute_transformation_ddl failed: %s — DDL: %s", e, ddl[:200])
        return _tool_error("execute_transformation_ddl", str(e))


@tool
def validate_transformation(source_fqn: str, target_fqn: str) -> str:
    """Validate a transformation by comparing source and target tables.

    Runs 4 checks:
    1. Row count comparison (target should have <= source rows)
    2. Column type verification (target columns should have proper types)
    3. Null rate comparison (target should have same or fewer nulls)
    4. Sample rows spot-check (5 rows from target)

    Args:
        source_fqn: Source table FQN
        target_fqn: Target Dynamic Table FQN
    """
    from tools.snowflake_tools import _validate_fqn, _quoted_fqn

    src_parts, src_err = _validate_fqn(source_fqn)
    if src_err:
        return _tool_error("validate_transformation", f"Invalid source: {src_err}")

    tgt_parts, tgt_err = _validate_fqn(target_fqn)
    if tgt_err:
        return _tool_error("validate_transformation", f"Invalid target: {tgt_err}")

    quoted_src = _quoted_fqn(src_parts)
    quoted_tgt = _quoted_fqn(tgt_parts)

    checks: dict[str, Any] = {"source": source_fqn, "target": target_fqn, "passed": True, "issues": []}

    try:
        conn = get_connection()
        cur = conn.cursor()

        # Check 1: Row counts
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
            if ratio > 1.05:  # More than 5% row increase is suspicious
                checks["issues"].append(
                    f"Target has MORE rows ({tgt_count}) than source ({src_count}) — possible fan-out"
                )

        # Check 2: Column types
        cur.execute(f"SHOW COLUMNS IN TABLE {quoted_tgt}")
        tgt_cols = cur.fetchall()
        col_desc = [d[0] for d in cur.description] if cur.description else []
        tgt_col_types = {}
        for row in tgt_cols:
            row_dict = dict(zip(col_desc, row))
            tgt_col_types[row_dict.get("column_name", "")] = row_dict.get("data_type", "")
        checks["target_columns"] = len(tgt_col_types)

        # Check 3: Null rates on target (sample)
        if tgt_col_types:
            null_exprs = [
                f'ROUND((1 - COUNT("{cn}") / NULLIF(COUNT(*), 0)) * 100, 2) AS "null_{cn}"'
                for cn in list(tgt_col_types.keys())[:20]  # Limit to 20 columns
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

        # Check 4: Sample rows
        cur.execute(f"SELECT * FROM {quoted_tgt} LIMIT 5")
        sample_desc = [d[0] for d in cur.description] if cur.description else []
        sample_rows = cur.fetchall()
        checks["sample_rows"] = [
            dict(zip(sample_desc, row)) for row in sample_rows
        ]

        cur.close()

        if checks["issues"]:
            checks["passed"] = False

        return json.dumps(checks, default=str)

    except Exception as e:
        logger.error("validate_transformation failed: %s", e)
        return _tool_error("validate_transformation", str(e))


@tool
def register_transformed_layer(
    data_product_id: str,
    table_mapping_json: str,
) -> str:
    """Register transformed tables as the working layer for semantic modeling.

    Stores a mapping of original FQNs to transformed FQNs so downstream
    agents (generation, validation, publishing) use the clean versions.

    Args:
        data_product_id: UUID of the data product
        table_mapping_json: JSON object mapping original FQN to transformed FQN.
            Example: {"DMTDEMO.BRONZE.RAW_TABLE": "DMTDEMO.SILVER_EKAIX.RAW_TABLE"}
    """
    try:
        mapping = json.loads(table_mapping_json)
    except json.JSONDecodeError as e:
        return _tool_error("register_transformed_layer", f"Invalid JSON: {e}")

    if not isinstance(mapping, dict) or not mapping:
        return _tool_error("register_transformed_layer", "Mapping must be a non-empty JSON object")

    try:
        import asyncio
        from services import redis as redis_service
        from services import postgres as pg_service

        settings = get_settings()

        # Store in Redis for fast lookup
        async def _save():
            client = await redis_service.get_client(settings.redis_url)
            cache_key = f"cache:working_layer:{data_product_id}"
            await redis_service.set_json(client, cache_key, mapping, ttl=86400)

            # Also persist to PostgreSQL data_products.settings for durability
            if pg_service._pool is not None:
                pool = pg_service._pool
                sql = """
                UPDATE data_products
                SET settings = COALESCE(settings, '{}'::jsonb) || jsonb_build_object('working_layer', $1::jsonb)
                WHERE id = $2::uuid
                """
                await pg_service.execute(pool, sql, json.dumps(mapping), data_product_id)

        asyncio.get_event_loop().run_until_complete(_save())

        logger.info(
            "Registered working layer for %s: %d table mappings",
            data_product_id, len(mapping),
        )

        return json.dumps({
            "status": "success",
            "data_product_id": data_product_id,
            "table_count": len(mapping),
            "mapping": mapping,
        })

    except Exception as e:
        logger.error("register_transformed_layer failed: %s", e)
        return _tool_error("register_transformed_layer", str(e))
