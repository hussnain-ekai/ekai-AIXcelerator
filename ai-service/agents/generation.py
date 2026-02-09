"""Generation subagent — semantic view YAML generation from BRD.

Responsibilities:
    - Load the BRD from the requirements phase
    - Load the ERD to verify table/column references
    - Generate Snowflake semantic view YAML using template-based assembly
    - Ensure all table references use fully qualified names
    - Verify all column references exist in the ERD graph
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import yaml

from agents.prompts import GENERATION_PROMPT

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Expression Templates — the LLM selects a template + column bindings;
# code assembles the expression. This prevents hallucinated SQL.
# ---------------------------------------------------------------------------

FACT_TEMPLATES: dict[str, str] = {
    "column_ref": "{column}",
    "calculated": "{col1} * {col2}",
    "case_binary": "CASE WHEN {col} {op} {val} THEN 1 ELSE 0 END",
    "date_trunc": "DATE_TRUNC('{granularity}', {col})",
    "coalesce": "COALESCE({col}, {default})",
    "cast": "CAST({col} AS {type})",
    "concat": "{col1} || ' ' || {col2}",
    "expr": "{expr}",  # Pass-through for raw expressions
}

METRIC_TEMPLATES: dict[str, str] = {
    "sum": "SUM({fact})",
    "count": "COUNT({fact})",
    "count_distinct": "COUNT(DISTINCT {fact})",
    "avg": "AVG({fact})",
    "min": "MIN({fact})",
    "max": "MAX({fact})",
    "sum_product": "SUM({fact1} * {fact2})",
    "ratio": "SUM({fact1}) / NULLIF(SUM({fact2}), 0)",
    "expr": "{expr}",  # Pass-through for raw aggregate expressions
}


def _resolve_fact_expr(fact_name: str, facts_map: dict[str, str]) -> str:
    """Resolve a fact name to its expression for use in metric templates."""
    return facts_map.get(fact_name, fact_name)


def _fill_template(template_name: str, templates: dict[str, str], columns: dict[str, str],
                    facts_map: dict[str, str] | None = None) -> str | None:
    """Fill a template with column bindings. Returns None on failure."""
    template = templates.get(template_name)
    if not template:
        logger.warning("Unknown template: %s", template_name)
        return None

    try:
        bindings = dict(columns)
        # For metric templates, resolve fact references to their expressions
        if facts_map:
            for key in ("fact", "fact1", "fact2"):
                if key in bindings:
                    resolved = _resolve_fact_expr(bindings[key], facts_map)
                    bindings[key] = resolved
        return template.format(**bindings)
    except KeyError as e:
        logger.warning("Template %s missing binding %s", template_name, e)
        return None


def _auto_recover_expr(template_name: str, columns: dict[str, str],
                       templates: dict[str, str],
                       facts_map: dict[str, str] | None = None) -> str | None:
    """Try to recover an expression when the chosen template fails.

    Strategy:
    1. If 'expr' key exists, use it directly (LLM provided raw expression)
    2. Try to auto-detect the right template from column keys
    3. Try all templates in order to find one that fits
    """
    # 1. Direct expression
    if "expr" in columns:
        return columns["expr"]

    # 2. Auto-detect from column keys
    col_keys = set(columns.keys())

    # case_binary needs: col, op, val
    if {"col", "op", "val"} <= col_keys:
        result = _fill_template("case_binary", templates, columns, facts_map)
        if result:
            return result

    # date_trunc needs: granularity, col
    if {"granularity", "col"} <= col_keys:
        result = _fill_template("date_trunc", templates, columns, facts_map)
        if result:
            return result

    # coalesce needs: col, default
    if {"col", "default"} <= col_keys:
        result = _fill_template("coalesce", templates, columns, facts_map)
        if result:
            return result

    # cast needs: col, type
    if {"col", "type"} <= col_keys:
        result = _fill_template("cast", templates, columns, facts_map)
        if result:
            return result

    # column_ref needs: column
    if "column" in col_keys:
        result = _fill_template("column_ref", templates, columns, facts_map)
        if result:
            return result

    # calculated needs: col1, col2
    if {"col1", "col2"} <= col_keys:
        result = _fill_template("calculated", templates, columns, facts_map)
        if result:
            return result

    # concat needs: col1, col2
    if {"col1", "col2"} <= col_keys:
        result = _fill_template("concat", templates, columns, facts_map)
        if result:
            return result

    # 3. Last resort: extract any column value
    for key in ("column", "col", "col1", "col2"):
        if key in columns and columns[key]:
            return columns[key]

    return None


def validate_column_exists(column: str, table_alias: str,
                           table_metadata: dict[str, set[str]]) -> bool:
    """Check whether a column exists in the given table's metadata."""
    cols = table_metadata.get(table_alias, set())
    return column.upper() in {c.upper() for c in cols}


# Keys in the columns dict that reference actual table columns (vs. literal values)
_COLUMN_KEYS = {"column", "col", "col1", "col2"}
# Keys that are literal values, not column references
_LITERAL_KEYS = {"op", "val", "default", "type", "granularity"}


def _validate_all_column_refs(columns: dict[str, str], table_alias: str,
                               table_metadata: dict[str, set[str]]) -> list[str]:
    """Validate all column references in a columns dict. Returns list of invalid columns."""
    invalid = []
    for key, value in columns.items():
        if key in _LITERAL_KEYS:
            continue
        if key in _COLUMN_KEYS and value:
            if not validate_column_exists(value, table_alias, table_metadata):
                invalid.append(value)
    return invalid


def _lint_and_fix_structure(structure: dict[str, Any],
                            table_metadata: dict[str, set[str]] | None = None) -> dict[str, Any]:
    """Auto-fix common structural issues in the LLM's JSON output.

    Runs before YAML assembly. Fixes:
    - Date/timestamp dimensions misclassified as regular dimensions
    - Missing data_type (inferred from column name or expr patterns)
    - Missing description (generated from name)
    - Duplicate names within a section
    - Empty/invalid entries
    - Root-level "comment" → "description"
    """
    import copy
    s = copy.deepcopy(structure)

    # 1. Auto-move date/timestamp dimensions to time_dimensions
    _DATE_TYPES = {"DATE", "TIMESTAMP", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ"}
    _DATE_SUFFIXES = {"_DATE", "_AT", "_TIME", "_TIMESTAMP", "_DT", "_TS"}
    time_dims = s.setdefault("time_dimensions", [])
    remaining_dims = []
    for dim in s.get("dimensions", []):
        dt = (dim.get("data_type") or "").upper()
        col_name = (dim.get("columns", {}).get("column", "") or dim.get("name", "")).upper()
        is_date = dt in _DATE_TYPES or any(col_name.endswith(sfx) for sfx in _DATE_SUFFIXES)
        if is_date:
            logger.info("Lint: auto-moved dimension '%s' to time_dimensions (data_type=%s)", dim["name"], dt)
            time_dims.append(dim)
        else:
            remaining_dims.append(dim)
    s["dimensions"] = remaining_dims

    # 2. Infer missing data_type from column name patterns
    _TYPE_HINTS: dict[str, list[str]] = {
        "NUMBER": ["_ID", "_COUNT", "_QTY", "_QUANTITY", "_AMOUNT", "_PRICE", "_COST", "_SCORE", "_RATE", "_PCT"],
        "VARCHAR": ["_NAME", "_DESC", "_DESCRIPTION", "_TYPE", "_STATUS", "_CODE", "_CATEGORY", "_LABEL", "_TEXT"],
        "DATE": ["_DATE", "_DT"],
        "TIMESTAMP_NTZ": ["_AT", "_TIME", "_TIMESTAMP", "_TS"],
        "BOOLEAN": ["IS_", "HAS_", "FLAG_"],
    }
    for section_key in ("facts", "dimensions", "time_dimensions"):
        for item in s.get(section_key, []):
            if not item.get("data_type"):
                col = (item.get("columns", {}).get("column", "") or item.get("name", "")).upper()
                inferred = "VARCHAR"  # default
                for dtype, suffixes in _TYPE_HINTS.items():
                    if any(col.endswith(sfx) if not sfx.endswith("_") else col.startswith(sfx) for sfx in suffixes):
                        inferred = dtype
                        break
                item["data_type"] = inferred
                logger.info("Lint: inferred data_type '%s' for %s '%s'", inferred, section_key, item["name"])

    # 3. Fill missing descriptions from name
    for section_key in ("facts", "dimensions", "time_dimensions", "metrics", "filters"):
        for item in s.get(section_key, []):
            if not item.get("description"):
                item["description"] = item["name"].replace("_", " ").title()
                logger.info("Lint: auto-generated description for %s '%s'", section_key, item["name"])

    # 4. Fill missing table descriptions
    for tbl in s.get("tables", []):
        if not tbl.get("description"):
            tbl["description"] = f"{tbl.get('table', tbl.get('alias', 'Table'))} data"

    # 5. Deduplicate names within each section
    for section_key in ("facts", "dimensions", "time_dimensions", "metrics", "filters"):
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for item in s.get(section_key, []):
            name = item.get("name", "")
            if name in seen:
                new_name = f"{name}_{len(seen)}"
                logger.warning("Lint: renamed duplicate '%s' to '%s' in %s", name, new_name, section_key)
                item["name"] = new_name
            seen.add(item["name"])
            deduped.append(item)
        s[section_key] = deduped

    # 6. Remove entries with empty names
    for section_key in ("facts", "dimensions", "time_dimensions", "metrics"):
        s[section_key] = [item for item in s.get(section_key, []) if item.get("name")]

    # 7. Ensure root-level uses "description" not "comment"
    if "comment" in s and "description" not in s:
        s["description"] = s.pop("comment")

    # 8. Remove verified_queries with invalid SEMANTIC_VIEW() SQL
    vqs = s.get("verified_queries", [])
    if vqs:
        cleaned = [vq for vq in vqs if "SEMANTIC_VIEW(" not in (vq.get("sql", "") or "").upper()]
        if len(cleaned) < len(vqs):
            logger.info("Lint: removed %d verified_queries with invalid SEMANTIC_VIEW() SQL", len(vqs) - len(cleaned))
        s["verified_queries"] = cleaned if cleaned else []
        if not s["verified_queries"]:
            del s["verified_queries"]

    # 9. Ensure to_table in relationships has primary_key in tables
    to_tables = {r.get("to_table", r.get("right_table", "")) for r in s.get("relationships", [])}
    for tbl in s.get("tables", []):
        alias = tbl.get("alias", tbl.get("name", ""))
        if alias in to_tables and not tbl.get("primary_key"):
            # Try to infer PK from columns_used or common ID columns
            cols = tbl.get("columns_used", [])
            col_names = [c["name"] if isinstance(c, dict) else c for c in cols]
            # Look for *_ID column matching the table name
            pk_candidates = [c for c in col_names if c.upper().endswith("_ID")]
            if pk_candidates:
                tbl["primary_key"] = [pk_candidates[0]]
                logger.info("Lint: auto-assigned primary_key '%s' to table '%s'", pk_candidates[0], alias)

    return s


def assemble_semantic_view_yaml(structure: dict[str, Any],
                                 table_metadata: dict[str, set[str]] | None = None) -> str:
    """Assemble a Snowflake Semantic View YAML from the LLM's structured JSON.

    Produces table-scoped YAML: facts, dimensions, and metrics are nested
    inside each table definition (not at root level). Root-level metrics are
    reserved for derived (cross-table) metrics only.

    Relationships use left_table/right_table/relationship_columns format.

    Args:
        structure: The JSON structure produced by the generation agent.
        table_metadata: Optional mapping of table_alias -> set of column names
                        for validation. If None, skips validation.

    Returns:
        YAML string conforming to Snowflake's semantic view YAML specification.
    """
    # Auto-fix common structural issues before assembly
    structure = _lint_and_fix_structure(structure, table_metadata)

    doc: dict[str, Any] = {}

    # Name and description (spec uses "description", not "comment")
    doc["name"] = structure.get("name", "semantic_model")
    desc = structure.get("description", structure.get("comment", ""))
    if desc:
        doc["description"] = desc

    # Index items by table alias for table-scoped assembly
    facts_by_table: dict[str, list[dict[str, Any]]] = {}
    dims_by_table: dict[str, list[dict[str, Any]]] = {}
    metrics_by_table: dict[str, list[dict[str, Any]]] = {}
    derived_metrics: list[dict[str, Any]] = []

    # Build facts map: fact_name -> fact expression (for metric resolution)
    facts_expr_map: dict[str, str] = {}

    # Process facts
    for fact in structure.get("facts", []):
        template_name = fact.get("template", "column_ref")
        columns = fact.get("columns", {})
        tbl_alias = fact.get("table", "")

        if table_metadata and tbl_alias:
            invalid = _validate_all_column_refs(columns, tbl_alias, table_metadata)
            if invalid:
                logger.warning("Skipping fact %s: columns %s not found in table %s",
                               fact["name"], invalid, tbl_alias)
                continue

        expr = _fill_template(template_name, FACT_TEMPLATES, columns)
        if expr is None:
            expr = _auto_recover_expr(template_name, columns, FACT_TEMPLATES)
            if expr:
                logger.info("Auto-recovered expression for fact %s: %s", fact["name"], expr)
            else:
                logger.warning("Cannot resolve expression for fact %s, skipping", fact["name"])
                continue

        f: dict[str, Any] = {"name": fact["name"], "expr": expr}
        if fact.get("synonyms"):
            f["synonyms"] = fact["synonyms"]
        if fact.get("description"):
            f["description"] = fact["description"]
        if fact.get("data_type"):
            f["data_type"] = fact["data_type"]

        facts_by_table.setdefault(tbl_alias, []).append(f)
        facts_expr_map[fact["name"]] = expr

    # Process dimensions
    for dim in structure.get("dimensions", []):
        template_name = dim.get("template", "column_ref")
        columns = dim.get("columns", {})
        tbl_alias = dim.get("table", "")

        if table_metadata and tbl_alias:
            invalid = _validate_all_column_refs(columns, tbl_alias, table_metadata)
            if invalid:
                logger.warning("Skipping dimension %s: columns %s not found in table %s",
                               dim["name"], invalid, tbl_alias)
                continue

        expr = _fill_template(template_name, FACT_TEMPLATES, columns)
        if expr is None:
            expr = _auto_recover_expr(template_name, columns, FACT_TEMPLATES)
            if expr:
                logger.info("Auto-recovered expression for dimension %s: %s", dim["name"], expr)
            else:
                logger.warning("Cannot resolve expression for dimension %s, skipping", dim["name"])
                continue

        d: dict[str, Any] = {"name": dim["name"], "expr": expr}
        if dim.get("synonyms"):
            d["synonyms"] = dim["synonyms"]
        if dim.get("description"):
            d["description"] = dim["description"]
        if dim.get("data_type"):
            d["data_type"] = dim["data_type"]

        dims_by_table.setdefault(tbl_alias, []).append(d)

    # Process time_dimensions
    time_dims_by_table: dict[str, list[dict[str, Any]]] = {}
    for tdim in structure.get("time_dimensions", []):
        template_name = tdim.get("template", "column_ref")
        columns = tdim.get("columns", {})
        tbl_alias = tdim.get("table", "")

        if table_metadata and tbl_alias:
            invalid = _validate_all_column_refs(columns, tbl_alias, table_metadata)
            if invalid:
                logger.warning("Skipping time_dimension %s: columns %s not found in table %s",
                               tdim["name"], invalid, tbl_alias)
                continue

        expr = _fill_template(template_name, FACT_TEMPLATES, columns)
        if expr is None:
            expr = _auto_recover_expr(template_name, columns, FACT_TEMPLATES)
            if expr:
                logger.info("Auto-recovered expression for time_dimension %s: %s", tdim["name"], expr)
            else:
                logger.warning("Cannot resolve expression for time_dimension %s, skipping", tdim["name"])
                continue

        td: dict[str, Any] = {"name": tdim["name"], "expr": expr}
        if tdim.get("synonyms"):
            td["synonyms"] = tdim["synonyms"]
        if tdim.get("description"):
            td["description"] = tdim["description"]
        if tdim.get("data_type"):
            td["data_type"] = tdim["data_type"]

        time_dims_by_table.setdefault(tbl_alias, []).append(td)

    # Process filters
    filters_by_table: dict[str, list[dict[str, Any]]] = {}
    for flt in structure.get("filters", []):
        tbl_alias = flt.get("table", "")
        f_entry: dict[str, Any] = {"name": flt["name"], "expr": flt.get("expr", "")}
        if flt.get("synonyms"):
            f_entry["synonyms"] = flt["synonyms"]
        if flt.get("description"):
            f_entry["description"] = flt["description"]
        filters_by_table.setdefault(tbl_alias, []).append(f_entry)

    # Process metrics — table-scoped metrics use column names directly
    for metric in structure.get("metrics", []):
        template_name = metric.get("template", "sum")
        columns = metric.get("columns", {})
        tbl_alias = metric.get("table", "")
        is_derived = metric.get("derived", False)

        # Resolve fact references to their underlying expressions
        # (table-scoped metrics must use column expressions, not fact names)
        resolved_columns = dict(columns)
        for key in ("fact", "fact1", "fact2"):
            if key in resolved_columns:
                fact_name = resolved_columns[key]
                resolved_columns[key] = facts_expr_map.get(fact_name, fact_name)

        expr = _fill_template(template_name, METRIC_TEMPLATES, resolved_columns)
        if expr is None:
            # Try auto-recovery for metrics
            expr = _auto_recover_expr(template_name, resolved_columns, METRIC_TEMPLATES, facts_expr_map)
            if expr is None:
                fact_ref = columns.get("fact", columns.get("fact1", ""))
                if fact_ref:
                    resolved = facts_expr_map.get(fact_ref, fact_ref)
                    expr = f"SUM({resolved})"
                else:
                    logger.warning("Cannot resolve expression for metric %s, skipping", metric["name"])
                    continue
            else:
                logger.info("Auto-recovered expression for metric %s: %s", metric["name"], expr)

        m: dict[str, Any] = {"name": metric["name"], "expr": expr}
        if metric.get("synonyms"):
            m["synonyms"] = metric["synonyms"]
        if metric.get("description"):
            m["description"] = metric["description"]

        if is_derived or not tbl_alias:
            derived_metrics.append(m)
        else:
            metrics_by_table.setdefault(tbl_alias, []).append(m)

    # Build tables with nested facts/dims/metrics
    tables_out: list[dict[str, Any]] = []
    for tbl in structure.get("tables", []):
        alias = tbl.get("alias", tbl.get("name", ""))
        t: dict[str, Any] = {"name": alias}
        t["base_table"] = {
            "database": tbl["database"],
            "schema": tbl["schema"],
            "table": tbl["table"],
        }
        if tbl.get("description"):
            t["description"] = tbl["description"]
        # Add primary_key if specified (required for relationships)
        pk = tbl.get("primary_key")
        if pk:
            if isinstance(pk, list):
                t["primary_key"] = {"columns": pk}
            elif isinstance(pk, dict) and "columns" in pk:
                t["primary_key"] = pk
            elif isinstance(pk, str):
                t["primary_key"] = {"columns": [pk]}
        # Nest facts, dimensions, time_dimensions, metrics, filters inside the table
        if alias in facts_by_table:
            t["facts"] = facts_by_table[alias]
        if alias in dims_by_table:
            t["dimensions"] = dims_by_table[alias]
        if alias in time_dims_by_table:
            t["time_dimensions"] = time_dims_by_table[alias]
        if alias in metrics_by_table:
            t["metrics"] = metrics_by_table[alias]
        if alias in filters_by_table:
            t["filters"] = filters_by_table[alias]
        tables_out.append(t)
    doc["tables"] = tables_out

    # Relationships — use left_table/right_table/relationship_columns format
    rels_out: list[dict[str, Any]] = []
    for rel in structure.get("relationships", []):
        from_table = rel.get("from_table", rel.get("left_table", ""))
        to_table = rel.get("to_table", rel.get("right_table", ""))
        from_cols = rel.get("from_columns", [])
        to_cols = rel.get("to_columns", [])

        r: dict[str, Any] = {"name": rel["name"]}
        r["left_table"] = from_table
        r["right_table"] = to_table
        r["relationship_columns"] = [
            {"left_column": fc, "right_column": tc}
            for fc, tc in zip(from_cols, to_cols)
        ]
        rels_out.append(r)
    if rels_out:
        doc["relationships"] = rels_out

    # Derived (cross-table) metrics at root level
    if derived_metrics:
        doc["metrics"] = derived_metrics

    # Verified queries (optional)
    vqr = structure.get("verified_queries", [])
    if vqr:
        doc["verified_queries"] = vqr

    # NOTE: ai_sql_generation is NOT a valid Snowflake semantic view YAML field.
    # Custom instructions are set via the Cortex Agent creation SQL, not in the YAML.
    # Any ai_sql_generation content from the LLM is intentionally excluded here.

    # Serialize to YAML
    return yaml.dump(doc, default_flow_style=False, sort_keys=False, allow_unicode=True)


def extract_json_from_text(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from LLM text output.

    Tries to parse the entire text as JSON first, then looks for JSON blocks.
    """
    # Try direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # Look for JSON block (with or without markdown fences)
    patterns = [
        r'```json\s*\n(.*?)\n\s*```',
        r'```\s*\n(.*?)\n\s*```',
        r'(\{[\s\S]*\})',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except (json.JSONDecodeError, TypeError):
                continue

    return None


# Subagent configuration
GENERATION_CONFIG = {
    "name": "generation",
    "system_prompt": GENERATION_PROMPT,
    "tools": [
        "get_latest_brd",
        "query_erd_graph",
        "save_semantic_view",
        "upload_artifact",
        "execute_rcr_query",
    ],
}
