"""
ekaiX Snowflake Stored Procedure: profile_table

Profiles a single table or view using sampling: null counts, approximate
cardinality, PK detection. Uses TABLESAMPLE BERNOULLI (1M rows) for base
tables, subquery LIMIT for views. All computation runs inside the warehouse.

Usage:
    CALL ekaix.procedures.profile_table('MY_DB.MY_SCHEMA.MY_TABLE');

Returns: VARIANT with profiling results.
Safe for billion-row tables.
"""

import json
from typing import Any

SAMPLE_SIZE: int = 1_000_000


def profile_table(session: Any, table_fqn: str) -> str:
    """
    Profile a Snowflake table or view with sampling-based analysis.

    Uses TABLESAMPLE BERNOULLI for large tables (>1M rows) to avoid full
    scans. Computes null rates and approximate uniqueness for PK detection.
    Executes with RESTRICTED CALLER's rights.

    Args:
        session: Snowpark Session (injected by Snowflake)
        table_fqn: Fully qualified table name (DB.SCHEMA.TABLE)

    Returns:
        JSON string with profiling results
    """
    parts = table_fqn.split(".")
    if len(parts) != 3:
        return json.dumps({"error": f"Invalid FQN: {table_fqn}. Expected DB.SCHEMA.TABLE"})

    # Step 1: Get row count from metadata (free, instant for base tables)
    meta_df = session.sql(f"""
        SELECT ROW_COUNT, TABLE_TYPE
        FROM {parts[0]}.INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = '{parts[1]}' AND TABLE_NAME = '{parts[2]}'
    """).collect()

    is_view = not meta_df or meta_df[0]["TABLE_TYPE"] in ("VIEW", "MATERIALIZED VIEW")
    metadata_row_count = meta_df[0]["ROW_COUNT"] if meta_df and meta_df[0].get("ROW_COUNT") else None

    # Step 2: Determine sampling strategy
    # TABLESAMPLE BERNOULLI only works on base tables, NOT views.
    # For views, use a subquery with LIMIT instead.
    sampled = False
    if is_view or metadata_row_count is None:
        from_clause = f"(SELECT * FROM {table_fqn} LIMIT {SAMPLE_SIZE}) AS _sample"
        sampled = True
        total_rows = None
    elif metadata_row_count == 0:
        return json.dumps({"table_fqn": table_fqn, "row_count": 0, "columns": [], "sampled": False})
    elif metadata_row_count <= SAMPLE_SIZE:
        from_clause = table_fqn
        total_rows = metadata_row_count
    else:
        from_clause = f"{table_fqn} TABLESAMPLE BERNOULLI ({SAMPLE_SIZE} ROWS)"
        sampled = True
        total_rows = metadata_row_count

    # Step 3: Get column metadata
    columns_df = session.sql(f"""
        SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE
        FROM {parts[0]}.INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = '{parts[1]}' AND TABLE_NAME = '{parts[2]}'
        ORDER BY ORDINAL_POSITION
    """).collect()

    if not columns_df:
        return json.dumps({
            "table_fqn": table_fqn,
            "row_count": total_rows or 0,
            "columns": [],
            "sampled": sampled,
        })

    # Step 4: Batch profile ALL columns in a single aggregate query
    col_expressions = []
    for col_row in columns_df:
        cn = col_row["COLUMN_NAME"]
        col_expressions.append(
            f'COUNT("{cn}") AS "nn_{cn}", '
            f'APPROX_COUNT_DISTINCT("{cn}") AS "dc_{cn}"'
        )

    batch_sql = f'SELECT COUNT(*) AS "_sample_n", {", ".join(col_expressions)} FROM {from_clause}'
    batch_df = session.sql(batch_sql).collect()
    batch_row = batch_df[0] if batch_df else {}

    sample_n = batch_row.get("_sample_n", 0) or 0

    if total_rows is None:
        total_rows = sample_n

    # Step 5: Compute per-column stats from batch results
    result_columns = []
    for col_row in columns_df:
        col_name = col_row["COLUMN_NAME"]
        non_null = batch_row.get(f"nn_{col_name}", 0) or 0
        distinct = batch_row.get(f"dc_{col_name}", 0) or 0
        null_pct = round((1 - non_null / sample_n) * 100, 2) if sample_n > 0 else 0
        uniqueness_pct = round((distinct / non_null) * 100, 2) if non_null > 0 else 0

        result_columns.append({
            "name": col_name,
            "data_type": col_row["DATA_TYPE"],
            "is_nullable": col_row["IS_NULLABLE"],
            "null_pct": null_pct,
            "uniqueness_pct": uniqueness_pct,
            "distinct_count": distinct,
            "total_rows": total_rows,
            "is_likely_pk": uniqueness_pct > 98 and null_pct == 0,
            "sampled": sampled,
        })

    return json.dumps({
        "table_fqn": table_fqn,
        "row_count": total_rows,
        "column_count": len(result_columns),
        "columns": result_columns,
        "sampled": sampled,
        "sample_size": sample_n if sampled else total_rows,
    })


# -- Snowflake CREATE PROCEDURE DDL --
# CREATE OR REPLACE PROCEDURE ekaix.procedures.profile_table(
#     table_fqn VARCHAR
# )
# RETURNS VARIANT
# LANGUAGE PYTHON
# RUNTIME_VERSION = '3.11'
# PACKAGES = ('snowflake-snowpark-python')
# HANDLER = 'profile_table'
# EXECUTE AS RESTRICTED CALLER;
