"""Discovery subagent — schema profiling, PK/FK detection, and ERD construction.

Responsibilities:
    - Profile schemas from Snowflake INFORMATION_SCHEMA (via RCR)
    - Detect primary keys (>98% uniqueness threshold)
    - Infer foreign key relationships (bias toward false positives)
    - Build the ERD graph in Neo4j (Database -> Schema -> Table -> Column)
    - Run data quality checks and compute health score
    - Generate the Data Quality Report artifact
"""

from __future__ import annotations

import logging
from typing import Any

from agents.prompts import DISCOVERY_PROMPT
from config import get_settings

logger = logging.getLogger(__name__)


def _get_uniqueness_threshold() -> float:
    return get_settings().pk_uniqueness_threshold


def _get_deduction_duplicate_pk() -> int:
    return get_settings().deduction_duplicate_pk


def _get_deduction_orphaned_fk() -> int:
    return get_settings().deduction_orphaned_fk


def _get_deduction_numeric_varchar() -> int:
    return get_settings().deduction_numeric_varchar


def _get_deduction_missing_description() -> int:
    return get_settings().deduction_missing_description


def compute_health_score(check_results: dict[str, list[dict[str, Any]]]) -> int:
    """Compute overall data quality health score from check results.

    Starting score is 100. Deductions are applied per issue found.
    Data completeness is the primary factor — empty tables destroy the score.
    Floor is 0.

    Args:
        check_results: Dict with check type keys and lists of issues.
            - "completeness_pcts": list of floats (avg non-null % per table)
            - "duplicate_pks": list of issue dicts
            - "orphaned_fks": list of issue dicts
            - "numeric_varchars": list of issue dicts
            - "missing_descriptions": list of issue dicts

    Returns:
        Health score between 0 and 100
    """
    score = 100

    # Data completeness — most important factor
    completeness_pcts = check_results.get("completeness_pcts", [])
    if completeness_pcts:
        avg_completeness = sum(completeness_pcts) / len(completeness_pcts)
        # Deduct 1 point per % below 90% completeness
        if avg_completeness < 90:
            score -= int(90 - avg_completeness)
        # Hard caps: empty data cannot score well
        if avg_completeness < 10:
            score = min(score, 15)
        elif avg_completeness < 50:
            score = min(score, 35)

    duplicate_pks = check_results.get("duplicate_pks", [])
    score -= len(duplicate_pks) * _get_deduction_duplicate_pk()

    orphaned_fks = check_results.get("orphaned_fks", [])
    score -= len(orphaned_fks) * _get_deduction_orphaned_fk()

    numeric_varchars = check_results.get("numeric_varchars", [])
    score -= len(numeric_varchars) * _get_deduction_numeric_varchar()

    missing_descriptions = check_results.get("missing_descriptions", [])
    score -= len(missing_descriptions) * _get_deduction_missing_description()

    return max(0, score)


def detect_primary_key(column_profile: dict[str, Any], row_count: int) -> bool:
    """Determine if a column is likely a primary key based on uniqueness.

    Args:
        column_profile: Profiling result for a single column
        row_count: Total row count of the table

    Returns:
        True if the column's uniqueness ratio exceeds the threshold
    """
    if row_count == 0:
        return False

    exact_distinct = column_profile.get("exact_distinct")
    if exact_distinct is None:
        return False

    uniqueness_ratio = exact_distinct / row_count
    return uniqueness_ratio >= _get_uniqueness_threshold()


def classify_table(table_name: str, column_names: list[str], row_count: int) -> str:
    """Classify a table as FACT or DIMENSION based on naming and structure.

    Heuristics:
    - Tables prefixed with 'fact_' or 'fct_' → FACT
    - Tables prefixed with 'dim_' or 'dimension_' → DIMENSION
    - Tables with many foreign key columns (>3) → likely FACT
    - Smaller tables with descriptive columns → likely DIMENSION

    Args:
        table_name: The table name (not FQN)
        column_names: List of column names
        row_count: Total row count

    Returns:
        'FACT' or 'DIMENSION'
    """
    name_lower = table_name.lower()

    if name_lower.startswith(("fact_", "fct_")):
        return "FACT"
    if name_lower.startswith(("dim_", "dimension_", "d_")):
        return "DIMENSION"

    # Count columns that look like foreign keys (_id suffix)
    fk_columns = [c for c in column_names if c.lower().endswith("_id")]
    if len(fk_columns) > 3:
        return "FACT"

    return "DIMENSION"


def _find_pk_column(table: dict[str, Any]) -> str:
    """Find the PK column name in a table, or return empty string."""
    for col in table.get("columns", []):
        if col.get("is_pk"):
            return col["name"]
    return ""


def infer_foreign_keys(
    tables: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Infer foreign key relationships between tables by matching column names.

    Biases toward false positives — better to suggest too many relationships
    than to miss real ones.

    Args:
        tables: List of table metadata dicts with 'name', 'columns' keys.
                Each column dict may have 'name' and optional 'is_pk' flag.

    Returns:
        List of inferred FK relationships with confidence scores
    """
    relationships: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()  # Deduplicate

    for table in tables:
        for col in table.get("columns", []):
            col_name = col.get("name", "").lower()
            if not col_name:
                continue

            # Look for _id pattern: sensor_id in fact -> SENSOR_ID in dim_sensor
            if col_name.endswith("_id"):
                entity_name = col_name[:-3]  # Remove _id suffix

                for other_table in tables:
                    if other_table["name"] == table["name"]:
                        continue
                    other_name_lower = other_table["name"].lower()

                    if entity_name in other_name_lower:
                        other_cols = {c["name"].lower(): c["name"] for c in other_table.get("columns", [])}

                        # Resolve target column: prefer exact same name, then
                        # PK column, then entity_id, then "id" — never hardcode
                        target_col = ""
                        confidence = 0.7
                        if col_name in other_cols:
                            # Exact column name match (e.g., SENSOR_ID → SENSOR_ID)
                            target_col = other_cols[col_name]
                            confidence = 0.95
                        elif f"{entity_name}_id" in other_cols:
                            target_col = other_cols[f"{entity_name}_id"]
                            confidence = 0.95
                        elif "id" in other_cols:
                            target_col = other_cols["id"]
                            confidence = 0.9
                        else:
                            # Fall back to the table's PK column
                            pk_col = _find_pk_column(other_table)
                            if pk_col:
                                target_col = pk_col
                                confidence = 0.85

                        if not target_col:
                            continue

                        key = (table["name"], col["name"], other_table["name"], target_col)
                        if key in seen:
                            continue
                        seen.add(key)

                        relationships.append({
                            "from_table": table["name"],
                            "from_column": col["name"],
                            "to_table": other_table["name"],
                            "to_column": target_col,
                            "confidence": confidence,
                            "cardinality": "many_to_one",
                        })

    return relationships


# Subagent configuration — only follow-up tools (pipeline handles initial discovery)
DISCOVERY_CONFIG = {
    "name": "discovery",
    "system_prompt": DISCOVERY_PROMPT,
    "tools": [
        "execute_rcr_query",
        "query_erd_graph",
    ],
}
