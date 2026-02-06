"""
ekaiX Snowflake Stored Procedure: discover_schema

Discovers schema metadata from Snowflake INFORMATION_SCHEMA using Restricted Caller's Rights.
Only returns objects the caller's role has access to.

Usage:
    CALL ekaix.procedures.discover_schema('MY_DATABASE', ARRAY_CONSTRUCT('PUBLIC', 'ANALYTICS'));

Returns: VARIANT with tables, columns, constraints metadata.
"""

import json
from typing import Any


def discover_schema(session: Any, database_name: str, schema_names: list[str]) -> str:
    """
    Discover schema metadata from Snowflake INFORMATION_SCHEMA.

    This procedure executes with RESTRICTED CALLER's rights — the caller only sees
    objects their Snowflake role permits.

    Args:
        session: Snowpark Session (injected by Snowflake)
        database_name: Name of the database to discover
        schema_names: List of schema names to profile

    Returns:
        JSON string with discovered metadata
    """
    result: dict[str, Any] = {
        "database": database_name,
        "schemas": [],
        "tables": [],
        "columns": [],
        "constraints": [],
    }

    for schema_name in schema_names:
        fqn = f"{database_name}.{schema_name}"

        # Discover tables
        tables_df = session.sql(f"""
            SELECT
                TABLE_CATALOG,
                TABLE_SCHEMA,
                TABLE_NAME,
                TABLE_TYPE,
                ROW_COUNT,
                BYTES,
                COMMENT
            FROM {database_name}.INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = '{schema_name}'
              AND TABLE_TYPE IN ('BASE TABLE', 'VIEW')
            ORDER BY TABLE_NAME
        """).collect()

        schema_tables: list[dict[str, Any]] = []
        for row in tables_df:
            table_info = {
                "catalog": row["TABLE_CATALOG"],
                "schema": row["TABLE_SCHEMA"],
                "name": row["TABLE_NAME"],
                "type": row["TABLE_TYPE"],
                "row_count": row["ROW_COUNT"],
                "bytes": row["BYTES"],
                "comment": row["COMMENT"],
                "fqn": f"{row['TABLE_CATALOG']}.{row['TABLE_SCHEMA']}.{row['TABLE_NAME']}",
            }
            schema_tables.append(table_info)
            result["tables"].append(table_info)

        result["schemas"].append({
            "name": schema_name,
            "fqn": fqn,
            "table_count": len(schema_tables),
        })

        # Discover columns for all tables in this schema
        columns_df = session.sql(f"""
            SELECT
                TABLE_CATALOG,
                TABLE_SCHEMA,
                TABLE_NAME,
                COLUMN_NAME,
                ORDINAL_POSITION,
                DATA_TYPE,
                IS_NULLABLE,
                CHARACTER_MAXIMUM_LENGTH,
                NUMERIC_PRECISION,
                NUMERIC_SCALE,
                COLUMN_DEFAULT,
                COMMENT
            FROM {database_name}.INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = '{schema_name}'
            ORDER BY TABLE_NAME, ORDINAL_POSITION
        """).collect()

        for row in columns_df:
            result["columns"].append({
                "table_fqn": f"{row['TABLE_CATALOG']}.{row['TABLE_SCHEMA']}.{row['TABLE_NAME']}",
                "name": row["COLUMN_NAME"],
                "ordinal_position": row["ORDINAL_POSITION"],
                "data_type": row["DATA_TYPE"],
                "is_nullable": row["IS_NULLABLE"],
                "max_length": row["CHARACTER_MAXIMUM_LENGTH"],
                "numeric_precision": row["NUMERIC_PRECISION"],
                "numeric_scale": row["NUMERIC_SCALE"],
                "default_value": row["COLUMN_DEFAULT"],
                "comment": row["COMMENT"],
            })

        # Discover constraints (PKs, UKs, FKs)
        constraints_df = session.sql(f"""
            SELECT
                tc.TABLE_CATALOG,
                tc.TABLE_SCHEMA,
                tc.TABLE_NAME,
                tc.CONSTRAINT_NAME,
                tc.CONSTRAINT_TYPE
            FROM {database_name}.INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
            WHERE tc.TABLE_SCHEMA = '{schema_name}'
              AND tc.CONSTRAINT_TYPE IN ('PRIMARY KEY', 'UNIQUE', 'FOREIGN KEY')
            ORDER BY tc.TABLE_NAME, tc.CONSTRAINT_TYPE
        """).collect()

        for row in constraints_df:
            result["constraints"].append({
                "table_fqn": f"{row['TABLE_CATALOG']}.{row['TABLE_SCHEMA']}.{row['TABLE_NAME']}",
                "constraint_name": row["CONSTRAINT_NAME"],
                "constraint_type": row["CONSTRAINT_TYPE"],
            })

    return json.dumps(result)


# -- Snowflake CREATE PROCEDURE DDL --
# CREATE OR REPLACE PROCEDURE ekaix.procedures.discover_schema(
#     database_name VARCHAR,
#     schema_names ARRAY
# )
# RETURNS VARIANT
# LANGUAGE PYTHON
# RUNTIME_VERSION = '3.11'
# PACKAGES = ('snowflake-snowpark-python')
# HANDLER = 'discover_schema'
# EXECUTE AS RESTRICTED CALLER;
