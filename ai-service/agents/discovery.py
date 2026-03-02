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

import json
import logging
import re
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


def compute_quality_band(
    check_results: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Classify data quality into a band: good, attention, or poor.

    Band criteria (evaluated top-down — first match wins):

    **Poor Quality** — ANY of:
        - Duplicate PKs exist
        - Average identifier completeness < 70%
        - 3+ orphaned FKs

    **Needs Attention** — ANY of:
        - Average identifier completeness between 70–90%
        - 1–2 orphaned FKs
        - 3+ numeric-stored-as-varchar columns

    **Good Quality** — everything else (no structural issues)

    Missing table descriptions are informational only — they never
    affect the band. Snowflake tables routinely lack comments.

    Returns:
        {"band": "good"|"attention"|"poor",
         "label": "Good Quality"|"Needs Attention"|"Poor Quality",
         "score": int}   # 100/60/20 for DB sorting/filtering
    """
    duplicate_pks = check_results.get("duplicate_pks", [])
    orphaned_fks = check_results.get("orphaned_fks", [])
    numeric_varchars = check_results.get("numeric_varchars", [])
    completeness_pcts = check_results.get("completeness_pcts", [])

    avg_completeness = (
        sum(completeness_pcts) / len(completeness_pcts)
        if completeness_pcts
        else 100.0
    )

    # --- Poor Quality ---
    if (
        len(duplicate_pks) > 0
        or avg_completeness < 70
        or len(orphaned_fks) >= 3
    ):
        return {"band": "poor", "label": "Poor Quality", "score": 20}

    # --- Needs Attention ---
    if (
        avg_completeness < 90
        or 1 <= len(orphaned_fks) <= 2
        or len(numeric_varchars) >= 3
    ):
        return {"band": "attention", "label": "Needs Attention", "score": 60}

    # --- Good Quality ---
    return {"band": "good", "label": "Good Quality", "score": 100}


def compute_health_score(check_results: dict[str, list[dict[str, Any]]]) -> int:
    """Legacy wrapper — returns numeric score from band classification."""
    return compute_quality_band(check_results)["score"]


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


def _compute_naming_score(column_names: list[str]) -> float:
    """Score column naming conventions (0.0 = poor, 1.0 = excellent).

    Positive: UPPER_SNAKE_CASE (Snowflake convention), lower_snake_case.
    Negative: camelCase, generic prefixes (col_, raw_, sys_, src_, tmp_).
    """
    if not column_names:
        return 0.5  # neutral

    snake_re = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
    upper_re = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$")
    camel_re = re.compile(r"^[a-z]+[A-Z]")
    bad_prefixes = ("col_", "raw_", "sys_", "src_", "tmp_", "x_", "xx_")

    total = len(column_names)
    score_sum = 0.0

    for name in column_names:
        col_score = 0.5  # baseline
        if upper_re.match(name):
            col_score = 1.0  # Snowflake standard
        elif snake_re.match(name):
            col_score = 0.9  # Good convention
        elif camel_re.match(name):
            col_score = 0.3  # Unusual for data

        if name.lower().startswith(bad_prefixes):
            col_score -= 0.3

        score_sum += max(0.0, min(1.0, col_score))

    return round(score_sum / total, 2)


def classify_data_maturity(
    columns: list[dict[str, Any]],
    duplicate_rate: float = 0.0,
) -> dict[str, Any]:
    """Classify data maturity for a single table as bronze/silver/gold.

    Used by the discovery pipeline and transformation agent tools.

    Args:
        columns: Profile column dicts with data_type, null_pct, is_likely_pk,
                 and column (or name) fields.
        duplicate_rate: Pre-computed duplicate row rate (0.0–1.0).

    Returns:
        Dict with maturity level, composite score, and individual signals.
    """
    total_cols = len(columns)
    if total_cols == 0:
        return {"maturity": "bronze", "score": 0.0, "signals": {}}

    varchar_types = {"TEXT", "VARCHAR", "STRING", "VARIANT", "OBJECT", "ARRAY"}
    nested_types = {"VARIANT", "OBJECT", "ARRAY"}

    varchar_count = sum(
        1 for c in columns
        if c.get("data_type", "").upper() in varchar_types
    )
    varchar_ratio = varchar_count / total_cols

    null_pcts = [c.get("null_pct", 0) for c in columns]
    avg_null_pct = sum(null_pcts) / len(null_pcts) if null_pcts else 0

    col_names = [c.get("column", c.get("name", "")) for c in columns]
    naming_score = _compute_naming_score(col_names)

    pk_candidates = [c for c in columns if c.get("is_likely_pk", False)]
    pk_confidence = 1.0 if pk_candidates else 0.0

    nested_count = sum(
        1 for c in columns
        if c.get("data_type", "").upper() in nested_types
    )

    # Composite score (0–100), weighted by importance
    score = (
        (1 - varchar_ratio) * 25
        + (1 - min(avg_null_pct / 100, 1.0)) * 20
        + (1 - min(duplicate_rate, 1.0)) * 15
        + naming_score * 15
        + pk_confidence * 15
        + (1 - min(nested_count / 5, 1.0)) * 10
    )

    if score >= 80:
        maturity = "gold"
    elif score >= 50:
        maturity = "silver"
    else:
        maturity = "bronze"

    return {
        "maturity": maturity,
        "score": round(score, 1),
        "signals": {
            "varchar_ratio": round(varchar_ratio, 2),
            "avg_null_pct": round(avg_null_pct, 1),
            "duplicate_rate": round(duplicate_rate, 3),
            "naming_score": round(naming_score, 2),
            "pk_confidence": pk_confidence,
            "nested_col_count": nested_count,
        },
    }


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


def _table_name_only(fqn: str) -> str:
    """Extract the bare table name from a potentially fully-qualified name."""
    return fqn.split(".")[-1].lower()


def _singular_matches_table(entity: str, table_name: str) -> bool:
    """Check if a singular entity name matches a table name (singular/plural).

    Handles common patterns:
      - PATIENT → PATIENTS, PAYER → PAYERS  (+ 's')
      - ADDRESS → ADDRESSES                 (+ 'es')
      - ENTITY → ENTITIES                   ('y' → 'ies')
      - ENCOUNTER → ENCOUNTERS              (+ 's')
      - Exact match                          (entity == table)
    """
    tbl = table_name.lower()
    ent = entity.lower()
    if ent == tbl:
        return True
    if f"{ent}s" == tbl:
        return True
    if f"{ent}es" == tbl:
        return True
    if ent.endswith("y") and f"{ent[:-1]}ies" == tbl:
        return True
    # Reverse: table name de-pluralised matches entity
    if tbl.endswith("ies") and f"{tbl[:-3]}y" == ent:
        return True
    if tbl.endswith("es") and tbl[:-2] == ent:
        return True
    if tbl.endswith("s") and tbl[:-1] == ent:
        return True
    return False


def infer_foreign_keys(
    tables: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Infer foreign key relationships between tables by matching column names.

    Biases toward false positives — better to suggest too many relationships
    than to miss real ones.

    Supported patterns:
      - *_id suffix:  sensor_id  → sensors / dim_sensor  (classic FK naming)
      - *_code suffix: payer_code → payers               (code-style FK naming)
      - *_key suffix:  org_key   → organizations          (key-style FK naming)
      - Bare entity:   PATIENT   → PATIENTS               (singular→plural)

    Args:
        tables: List of table metadata dicts with 'name', 'columns' keys.
                Each column dict may have 'name' and optional 'is_pk' flag.

    Returns:
        List of inferred FK relationships with confidence scores
    """
    relationships: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()  # Deduplicate

    # Pre-build a set of table names (bare, lowered) for fast lookup
    table_bare_names: dict[str, list[dict[str, Any]]] = {}
    for t in tables:
        bare = _table_name_only(t["name"])
        table_bare_names.setdefault(bare, []).append(t)

    for table in tables:
        for col in table.get("columns", []):
            col_name = col.get("name", "").lower()
            if not col_name:
                continue

            # Skip columns that are PKs in their own table
            if col.get("is_pk"):
                continue

            # Determine the entity name and confidence tier based on suffix
            entity_name: str | None = None
            is_explicit_fk = False

            if col_name.endswith("_id"):
                entity_name = col_name[:-3]
                is_explicit_fk = True
            elif col_name.endswith("_code"):
                entity_name = col_name[:-5]
                is_explicit_fk = True
            elif col_name.endswith("_key"):
                entity_name = col_name[:-4]
                is_explicit_fk = True
            else:
                # Bare entity name candidate (e.g., PATIENT, ENCOUNTER)
                # Skip very short names and common non-FK columns
                skip_names = {
                    "id", "name", "type", "status", "date", "start", "stop",
                    "code", "value", "description", "source", "address", "city",
                    "state", "zip", "county", "lat", "lon", "gender", "race",
                    "ethnicity", "birthdate", "deathdate", "prefix", "suffix",
                    "first", "last", "maiden", "phone", "revenue", "utilization",
                }
                if col_name in skip_names or len(col_name) < 3:
                    continue
                entity_name = col_name
                is_explicit_fk = False

            if not entity_name:
                continue

            for other_table in tables:
                if other_table["name"] == table["name"]:
                    continue
                other_bare = _table_name_only(other_table["name"])

                # Match: entity name against table name
                is_match = False
                if is_explicit_fk:
                    # For _id/_code/_key: substring match (original behavior)
                    is_match = entity_name in other_table["name"].lower()
                else:
                    # For bare entity: singular→plural match only (stricter)
                    is_match = _singular_matches_table(entity_name, other_bare)

                if not is_match:
                    continue

                other_cols = {
                    c["name"].lower(): c["name"]
                    for c in other_table.get("columns", [])
                }

                # Resolve target column
                target_col = ""
                confidence = 0.7
                if col_name in other_cols:
                    target_col = other_cols[col_name]
                    confidence = 0.95
                elif f"{entity_name}_id" in other_cols:
                    target_col = other_cols[f"{entity_name}_id"]
                    confidence = 0.95
                elif "id" in other_cols:
                    target_col = other_cols["id"]
                    confidence = 0.9
                else:
                    pk_col = _find_pk_column(other_table)
                    if pk_col:
                        target_col = pk_col
                        confidence = 0.85

                if not target_col:
                    continue

                # Reduce confidence for bare entity names (more speculative)
                if not is_explicit_fk:
                    confidence *= 0.85

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


def _parse_relationship_overrides(
    doc_text: str,
) -> tuple[dict[tuple[str, str], dict[str, Any]], set[tuple[str, str]]]:
    """Parse data description text for confirmed/rejected relationships.

    Looks for patterns in section [6] ERD Generation Recommendations:
      - [6.1] Confirmed/Focus: "TABLE_A connects to TABLE_B via COL"
      - [6.3] Rejected/Known Limitations: "TABLE_A to TABLE_B: rejected"

    Returns (confirmed_dict, rejected_set). Best-effort parsing — returns
    empty collections on failure (base heuristics are used unchanged).
    """
    confirmed: dict[tuple[str, str], dict[str, Any]] = {}
    rejected: set[tuple[str, str]] = set()

    # Pattern: "TABLE_A connects to TABLE_B via COLUMN" or
    #          "TABLE_A to TABLE_B: relationship_type"
    connect_pattern = re.compile(
        r"(\S+)\s+(?:connects? to|→|->)\s+(\S+)\s+via\s+(\S+)",
        re.IGNORECASE,
    )
    reject_pattern = re.compile(
        r"(\S+)\s+(?:to|→|->)\s+(\S+).*(?:reject|remove|invalid|incorrect)",
        re.IGNORECASE,
    )

    for match in connect_pattern.finditer(doc_text):
        from_tbl = match.group(1).strip("'\"")
        to_tbl = match.group(2).strip("'\"")
        via_col = match.group(3).strip("'\"")
        key = (from_tbl, to_tbl)
        confirmed[key] = {
            "from_table": from_tbl,
            "from_column": via_col,
            "to_table": to_tbl,
            "to_column": "",  # Will be resolved by FK inference
            "cardinality": "many_to_one",
        }

    for match in reject_pattern.finditer(doc_text):
        from_tbl = match.group(1).strip("'\"")
        to_tbl = match.group(2).strip("'\"")
        rejected.add((from_tbl, to_tbl))

    return confirmed, rejected


def infer_foreign_keys_enhanced(
    tables: list[dict[str, Any]],
    data_description: dict[str, Any] | str,
) -> list[dict[str, Any]]:
    """FK inference enhanced with data description context.

    1. Run base heuristic inference
    2. Parse data description for relationship overrides
    3. Boost confirmed relationships to confidence 1.0
    4. Add user-stated relationships that heuristics missed
    5. Remove rejected relationships
    """
    # 1. Base heuristic inference
    relationships = infer_foreign_keys(tables)

    # 2. Parse data description
    if isinstance(data_description, dict):
        doc_text = data_description.get("document", json.dumps(data_description))
    elif isinstance(data_description, str):
        try:
            parsed = json.loads(data_description)
            doc_text = parsed.get("document", data_description)
        except (json.JSONDecodeError, TypeError):
            doc_text = data_description
    else:
        doc_text = str(data_description)

    confirmed, rejected = _parse_relationship_overrides(doc_text)
    if not confirmed and not rejected:
        logger.info("No relationship overrides found in data description — using base heuristics")
        return relationships

    # 3. Boost confirmed relationships
    existing_keys: set[tuple[str, str]] = set()
    for rel in relationships:
        key = (rel["from_table"], rel["to_table"])
        existing_keys.add(key)
        if key in confirmed:
            rel["confidence"] = 1.0
            rel["source"] = "user_confirmed"
            logger.info("Boosted relationship %s → %s to confidence 1.0", key[0], key[1])

    # 4. Add user-stated relationships that heuristics missed
    for key, rel_data in confirmed.items():
        if key not in existing_keys:
            relationships.append({**rel_data, "confidence": 1.0, "source": "user_stated"})
            logger.info("Added user-stated relationship: %s → %s", key[0], key[1])

    # 5. Remove rejected relationships
    if rejected:
        before_count = len(relationships)
        relationships = [r for r in relationships
                         if (r["from_table"], r["to_table"]) not in rejected]
        removed = before_count - len(relationships)
        if removed:
            logger.info("Removed %d rejected relationships", removed)

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
