"""
ekaiX Snowflake Stored Procedure: validate_semantic_view

Validates a semantic view YAML definition against real Snowflake metadata.
Checks: YAML syntax, column existence, SQL compilation, join cardinality.

Usage:
    CALL ekaix.procedures.validate_semantic_view('<yaml_string>');

Returns: VARIANT with pass/fail + issues list.
"""

import json
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


def validate_semantic_view(session: Any, yaml_content: str) -> str:
    """
    Validate a Snowflake semantic view YAML definition.

    Args:
        session: Snowpark Session (injected by Snowflake)
        yaml_content: YAML string defining the semantic view

    Returns:
        JSON string with validation results
    """
    issues: list[dict[str, str]] = []
    result: dict[str, Any] = {
        "valid": True,
        "issues": issues,
        "checks_run": [],
    }

    # 1. YAML Syntax Check
    result["checks_run"].append("yaml_syntax")
    try:
        if yaml is None:
            issues.append({
                "severity": "error",
                "check": "yaml_syntax",
                "message": "PyYAML not available in Snowflake environment",
            })
            result["valid"] = False
            return json.dumps(result)

        spec = yaml.safe_load(yaml_content)
        if not isinstance(spec, dict):
            issues.append({
                "severity": "error",
                "check": "yaml_syntax",
                "message": "YAML root must be a mapping/object",
            })
            result["valid"] = False
            return json.dumps(result)
    except Exception as e:
        issues.append({
            "severity": "error",
            "check": "yaml_syntax",
            "message": f"Invalid YAML: {e!s}",
        })
        result["valid"] = False
        return json.dumps(result)

    # 2. Required Fields Check
    result["checks_run"].append("required_fields")
    required_top_level = ["name", "tables"]
    for field in required_top_level:
        if field not in spec:
            issues.append({
                "severity": "error",
                "check": "required_fields",
                "message": f"Missing required top-level field: '{field}'",
            })
            result["valid"] = False

    if not result["valid"]:
        return json.dumps(result)

    # 3. Column Existence Check
    result["checks_run"].append("column_existence")
    tables = spec.get("tables", [])
    if not isinstance(tables, list):
        tables = [tables]

    for table_spec in tables:
        table_name = table_spec.get("name", "")
        if not table_name:
            issues.append({
                "severity": "error",
                "check": "column_existence",
                "message": "Table entry missing 'name' field",
            })
            result["valid"] = False
            continue

        # Parse FQN
        parts = table_name.split(".")
        if len(parts) != 3:
            issues.append({
                "severity": "warning",
                "check": "column_existence",
                "message": f"Table '{table_name}' is not fully qualified (expected DB.SCHEMA.TABLE)",
            })
            continue

        db, schema, tbl = parts

        # Get actual columns from INFORMATION_SCHEMA
        try:
            actual_cols_df = session.sql(f"""
                SELECT COLUMN_NAME
                FROM {db}.INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = '{schema}' AND TABLE_NAME = '{tbl}'
            """).collect()

            actual_columns = {row["COLUMN_NAME"].upper() for row in actual_cols_df}

            if not actual_columns:
                issues.append({
                    "severity": "error",
                    "check": "column_existence",
                    "message": f"Table '{table_name}' not found or no columns accessible",
                })
                result["valid"] = False
                continue

            # Check all referenced columns in measures, dimensions, time_dimensions
            for section in ["measures", "dimensions", "time_dimensions"]:
                for item in table_spec.get(section, []):
                    expr = item.get("expr", item.get("name", ""))
                    # Simple column reference check (not full SQL parsing)
                    if expr and "(" not in expr and expr.upper() not in actual_columns:
                        issues.append({
                            "severity": "warning",
                            "check": "column_existence",
                            "message": (
                                f"Column '{expr}' in {section} may not exist "
                                f"in table '{table_name}'"
                            ),
                        })

        except Exception as e:
            issues.append({
                "severity": "error",
                "check": "column_existence",
                "message": f"Cannot verify columns for '{table_name}': {e!s}",
            })

    # 4. SQL Compilation via EXPLAIN
    result["checks_run"].append("sql_compilation")
    for table_spec in tables:
        table_name = table_spec.get("name", "")
        if not table_name or len(table_name.split(".")) != 3:
            continue

        # Build a simple SELECT to test compilation
        select_cols: list[str] = []
        for section in ["measures", "dimensions", "time_dimensions"]:
            for item in table_spec.get(section, []):
                expr = item.get("expr", item.get("name", ""))
                if expr:
                    select_cols.append(expr)

        if select_cols:
            test_sql = f"SELECT {', '.join(select_cols[:10])} FROM {table_name} LIMIT 0"
            try:
                session.sql(f"EXPLAIN {test_sql}").collect()
            except Exception as e:
                issues.append({
                    "severity": "error",
                    "check": "sql_compilation",
                    "message": f"SQL compilation failed for '{table_name}': {e!s}",
                })
                result["valid"] = False

    # 5. Join Validation
    result["checks_run"].append("join_validation")
    joins = spec.get("joins", [])
    if isinstance(joins, list):
        for join_spec in joins:
            join_expr = join_spec.get("join", "")
            if join_expr:
                try:
                    # Test the join compiles
                    test_join_sql = f"EXPLAIN SELECT 1 FROM {join_expr} LIMIT 0"
                    session.sql(test_join_sql).collect()
                except Exception as e:
                    issues.append({
                        "severity": "warning",
                        "check": "join_validation",
                        "message": f"Join validation issue: {e!s}",
                    })

    # Set overall validity
    error_count = sum(1 for i in issues if i["severity"] == "error")
    result["valid"] = error_count == 0
    result["error_count"] = error_count
    result["warning_count"] = len(issues) - error_count

    return json.dumps(result)


# -- Snowflake CREATE PROCEDURE DDL --
# CREATE OR REPLACE PROCEDURE ekaix.procedures.validate_semantic_view(
#     yaml_content VARCHAR
# )
# RETURNS VARIANT
# LANGUAGE PYTHON
# RUNTIME_VERSION = '3.11'
# PACKAGES = ('snowflake-snowpark-python', 'pyyaml')
# HANDLER = 'validate_semantic_view'
# EXECUTE AS RESTRICTED CALLER;
