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


# ---------------------------------------------------------------------------
# Column case sensitivity helpers — Snowflake auto-uppercases unquoted
# identifiers, so columns stored in lowercase (via double-quoted CREATE)
# must be referenced with double quotes in SQL expressions.
# ---------------------------------------------------------------------------


def needs_quoting(col_name: str) -> bool:
    """True if a Snowflake identifier needs double-quoting (stored as non-uppercase)."""
    return col_name != col_name.upper()


def safe_col(col_name: str) -> str:
    """Wrap a column name in SQL double quotes if it needs quoting for Snowflake."""
    if needs_quoting(col_name):
        return f'"{col_name}"'
    return col_name


def _resolve_column_case(col_name: str, table_alias: str,
                          table_metadata: dict[str, set[str]]) -> str:
    """Resolve a column name to its actual stored case via case-insensitive lookup."""
    cols = table_metadata.get(table_alias, set())
    for actual in cols:
        if actual.upper() == col_name.upper():
            return actual
    return col_name  # Not found — return as-is


def _quote_column_refs(columns: dict[str, str], table_alias: str,
                        table_metadata: dict[str, set[str]]) -> dict[str, str]:
    """Resolve actual column case and apply SQL quoting for all column-key values."""
    result = dict(columns)
    for key in _COLUMN_KEYS:
        if key in result and result[key]:
            resolved = _resolve_column_case(result[key], table_alias, table_metadata)
            result[key] = safe_col(resolved)
    return result


def _quote_columns_in_filter_expr(expr: str, table_alias: str,
                                    table_metadata: dict[str, set[str]]) -> str:
    """Find and quote column names within a raw SQL filter expression.

    Uses look-around assertions to skip columns already wrapped in double quotes.
    """
    cols = table_metadata.get(table_alias, set())
    for col in cols:
        if needs_quoting(col):
            # (?<!") prevents matching inside already-quoted identifiers
            pattern = re.compile(r'(?<!")' + r'\b' + re.escape(col) + r'\b' + r'(?!")', re.IGNORECASE)
            expr = pattern.sub(f'"{col}"', expr)
    return expr


async def build_table_metadata(data_product_id: str,
                                structure: dict[str, Any]) -> dict[str, set[str]]:
    """Build alias -> set(actual_column_names) from discovery pipeline cache.

    Maps the LLM's table aliases to actual column sets via FQN matching.
    Falls back to an empty dict if cache is unavailable (graceful degradation).
    """
    fqn_columns = await _build_fqn_column_map(data_product_id)
    if not fqn_columns:
        return {}

    # Map structure aliases to column sets via FQN
    meta: dict[str, set[str]] = {}
    for tbl in structure.get("tables", []):
        alias = tbl.get("alias", tbl.get("name", ""))
        fqn = f"{tbl.get('database', '')}.{tbl.get('schema', '')}.{tbl.get('table', '')}".upper()
        cols = fqn_columns.get(fqn, set())
        meta[alias] = cols

    return meta


async def _build_fqn_column_map(data_product_id: str) -> dict[str, set[str]]:
    """Fetch Redis discovery cache and return FQN (uppercased) -> set(actual column names)."""
    from config import get_settings
    from services import redis as redis_service

    try:
        settings = get_settings()
        client = await redis_service.get_client(settings.redis_url)
        cache_key = f"discovery:pipeline:{data_product_id}"
        cached = await redis_service.get_json(client, cache_key)
    except Exception as exc:
        logger.warning("_build_fqn_column_map: Redis unavailable: %s", exc)
        return {}

    if not cached:
        return {}

    fqn_columns: dict[str, set[str]] = {}
    for table in cached.get("metadata", []):
        fqn = table.get("fqn", "").upper()
        columns = {col["name"] for col in table.get("columns", [])}
        fqn_columns[fqn] = columns
    return fqn_columns


def _extract_sample_values_from_profiles(profiles: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    """Extract sample_values map from a list of profile dicts.

    Returns: {FQN_UPPER: {COLUMN_NAME: {"sample_values": [...], "distinct_count": int}}}
    """
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for profile in profiles:
        fqn = profile.get("table", "").upper()
        if not fqn:
            continue
        col_map: dict[str, dict[str, Any]] = {}
        for col in profile.get("columns", []):
            sv = col.get("sample_values")
            if sv:
                col_map[col["column"]] = {
                    "sample_values": sv,
                    "distinct_count": col.get("distinct_count", 0),
                }
        if col_map:
            result[fqn] = col_map
    return result


async def _build_fqn_sample_values(data_product_id: str) -> dict[str, dict[str, dict[str, Any]]]:
    """Fetch sample_values + distinct_count per column from discovery data.

    Returns: {FQN_UPPER: {COLUMN_NAME: {"sample_values": [...], "distinct_count": int}}}

    Tries Redis cache first (fast). If cache expired, falls back to the
    quality_report artifact in MinIO (permanent storage).
    """
    from config import get_settings
    from services import redis as redis_service

    # 1. Try Redis cache (fast path)
    try:
        settings = get_settings()
        client = await redis_service.get_client(settings.redis_url)
        cache_key = f"discovery:pipeline:{data_product_id}"
        cached = await redis_service.get_json(client, cache_key)
        if cached and cached.get("profiles"):
            return _extract_sample_values_from_profiles(cached["profiles"])
    except Exception as exc:
        logger.warning("_build_fqn_sample_values: Redis unavailable: %s", exc)

    # 2. Fallback: read quality_report artifact from MinIO (survives cache expiry)
    try:
        from services import minio as minio_service
        from services import postgres as pg_service

        if pg_service._pool is None or minio_service._client is None:
            return {}

        settings = get_settings()
        rows = await pg_service.query(
            pg_service._pool,
            "SELECT minio_path FROM artifacts "
            "WHERE data_product_id = $1::uuid AND artifact_type = 'quality_report' "
            "ORDER BY created_at DESC LIMIT 1",
            data_product_id,
        )
        if not rows:
            return {}

        minio_path = rows[0]["minio_path"]
        raw = minio_service.download_file(
            minio_service._client, settings.minio_artifacts_bucket, minio_path,
        )
        qr_data = json.loads(raw)
        profiles = qr_data.get("profiles", [])
        if profiles:
            logger.info("_build_fqn_sample_values: loaded profiles from MinIO artifact (Redis cache expired)")
            return _extract_sample_values_from_profiles(profiles)
    except Exception as exc:
        logger.warning("_build_fqn_sample_values: MinIO fallback failed: %s", exc)

    return {}


async def build_table_metadata_from_yaml(
    data_product_id: str, yaml_doc: dict[str, Any]
) -> dict[str, set[str]]:
    """Build alias -> set(actual_column_names) from a parsed YAML doc.

    Unlike build_table_metadata (which works with JSON structure from the LLM),
    this works with the final YAML doc where tables have name + base_table fields.
    """
    fqn_columns = await _build_fqn_column_map(data_product_id)
    if not fqn_columns:
        return {}

    meta: dict[str, set[str]] = {}
    for tbl in yaml_doc.get("tables", []):
        alias = tbl.get("name", "")
        bt = tbl.get("base_table", {})
        fqn = f"{bt.get('database', '')}.{bt.get('schema', '')}.{bt.get('table', '')}".upper()
        meta[alias] = fqn_columns.get(fqn, set())
    return meta


def _quote_columns_in_expr(expr: str, table_alias: str,
                            table_metadata: dict[str, set[str]]) -> str:
    """Quote all column references in a SQL expression.

    Handles simple column refs (CAPACITY_MW), aggregate funcs (SUM(CAPACITY_MW)),
    CASE expressions, etc. by doing word-boundary replacement for each known column.
    Skips columns that are already quoted (wrapped in double quotes).
    """
    if not expr or not table_metadata:
        return expr
    cols = table_metadata.get(table_alias, set())
    if not cols:
        return expr

    for col in cols:
        if needs_quoting(col):
            # Match column name with word boundaries, not already inside double quotes
            pattern = re.compile(r'(?<!")' + r'\b' + re.escape(col) + r'\b' + r'(?!")', re.IGNORECASE)
            expr = pattern.sub(f'"{col}"', expr)

    return expr


async def quote_columns_in_yaml_str(yaml_str: str, data_product_id: str) -> str:
    """Parse YAML, resolve column case, apply SQL quoting, re-serialize.

    This is the YAML-level fallback for when the generation agent outputs
    pre-assembled YAML instead of JSON (bypassing the template assembler).
    """
    try:
        doc = yaml.safe_load(yaml_str)
    except yaml.YAMLError:
        return yaml_str  # Can't parse, return as-is

    if not isinstance(doc, dict) or "tables" not in doc:
        return yaml_str

    # Build metadata from the YAML's own table definitions
    meta = await build_table_metadata_from_yaml(data_product_id, doc)
    if not meta:
        logger.warning("quote_columns_in_yaml_str: no metadata available, returning YAML as-is")
        return yaml_str

    # Check if any quoting is needed at all
    any_lowercase = False
    for cols in meta.values():
        if any(needs_quoting(c) for c in cols):
            any_lowercase = True
            break
    if not any_lowercase:
        return yaml_str  # All columns are uppercase, no quoting needed

    changed = False

    # Quote expr fields in facts/dimensions/time_dimensions/metrics/filters
    for tbl in doc.get("tables", []):
        alias = tbl.get("name", "")
        for section in ("facts", "dimensions", "time_dimensions", "metrics", "filters"):
            for item in tbl.get(section, []):
                old_expr = item.get("expr", "")
                if old_expr:
                    new_expr = _quote_columns_in_expr(old_expr, alias, meta)
                    if new_expr != old_expr:
                        item["expr"] = new_expr
                        changed = True

        # Quote primary_key columns
        pk = tbl.get("primary_key")
        if pk and isinstance(pk, dict) and "columns" in pk:
            new_cols = []
            for c in pk["columns"]:
                # Skip already-quoted columns
                if c.startswith('"') and c.endswith('"'):
                    new_cols.append(c)
                    continue
                resolved = _resolve_column_case(c, alias, meta)
                quoted = safe_col(resolved)
                if quoted != c:
                    changed = True
                new_cols.append(quoted)
            pk["columns"] = new_cols

    # Quote relationship columns
    for rel in doc.get("relationships", []):
        from_table = rel.get("left_table", "")
        to_table = rel.get("right_table", "")
        for rc in rel.get("relationship_columns", []):
            for col_key, tbl_alias in [("left_column", from_table), ("right_column", to_table)]:
                c = rc.get(col_key, "")
                if c and not (c.startswith('"') and c.endswith('"')):
                    resolved = _resolve_column_case(c, tbl_alias, meta)
                    quoted = safe_col(resolved)
                    if quoted != c:
                        rc[col_key] = quoted
                        changed = True

    if not changed:
        return yaml_str

    logger.info("quote_columns_in_yaml_str: applied column quoting to YAML for %s", data_product_id)
    return yaml.dump(doc, default_flow_style=False, sort_keys=False, allow_unicode=False)


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
                            table_metadata: dict[str, set[str]] | None = None,
                            sample_values_map: dict[str, dict[str, dict[str, Any]]] | None = None) -> dict[str, Any]:
    """Auto-fix common structural issues in the LLM's JSON output.

    Runs before YAML assembly. Fixes:
    - Date/timestamp dimensions misclassified as regular dimensions
    - Missing data_type (inferred from column name or expr patterns)
    - Missing description (generated from name)
    - Duplicate names within a section
    - Empty/invalid entries
    - Root-level "comment" → "description"
    - Injects sample_values + is_enum on dimensions from profiling data
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

    # 10. Inject sample_values + is_enum on dimensions from discovery profiling
    if sample_values_map:
        # Build alias -> FQN mapping from tables
        alias_to_fqn: dict[str, str] = {}
        for tbl in s.get("tables", []):
            alias = tbl.get("alias", tbl.get("name", ""))
            fqn = f"{tbl.get('database', '')}.{tbl.get('schema', '')}.{tbl.get('table', '')}".upper()
            alias_to_fqn[alias] = fqn

        for dim in s.get("dimensions", []):
            tbl_alias = dim.get("table", "")
            fqn = alias_to_fqn.get(tbl_alias, "")
            col_name = dim.get("columns", {}).get("column", "")
            if not fqn or not col_name:
                continue
            col_info = sample_values_map.get(fqn, {})
            # Case-insensitive column lookup
            matched_info = None
            for stored_col, info in col_info.items():
                if stored_col.upper() == col_name.upper():
                    matched_info = info
                    break
            if matched_info:
                dim["sample_values"] = matched_info["sample_values"]
                dim["is_enum"] = matched_info["distinct_count"] <= 25
                logger.info("Lint: injected sample_values (%d values, is_enum=%s) for dimension '%s'",
                            len(matched_info["sample_values"]), dim["is_enum"], dim["name"])

    return s


def _sanitize_yaml_strings(obj: Any) -> None:
    """Replace non-ASCII characters in all string values in-place.

    Snowflake's YAML parser rejects smart quotes, em-dashes, and other
    Unicode characters. This recursively walks the dict/list and cleans
    every string value. Safe for Gemini (already ASCII) — no-op on clean text.
    """
    _REPLACEMENTS = {
        "\u2018": "'", "\u2019": "'",   # smart single quotes
        "\u201c": '"', "\u201d": '"',   # smart double quotes
        "\u2013": "-", "\u2014": "-",   # en-dash, em-dash
        "\u2026": "...",                 # ellipsis
        "\u00a0": " ",                   # non-breaking space
        "\u200b": "",                    # zero-width space
        "\u2022": "-",                   # bullet
    }

    def _clean(s: str) -> str:
        for old, new in _REPLACEMENTS.items():
            s = s.replace(old, new)
        # Strip any remaining non-ASCII
        return s.encode("ascii", errors="ignore").decode("ascii")

    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                obj[k] = _clean(v)
            elif isinstance(v, (dict, list)):
                _sanitize_yaml_strings(v)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                obj[i] = _clean(v)
            elif isinstance(v, (dict, list)):
                _sanitize_yaml_strings(v)


def assemble_semantic_view_yaml(structure: dict[str, Any],
                                 table_metadata: dict[str, set[str]] | None = None,
                                 sample_values_map: dict[str, dict[str, dict[str, Any]]] | None = None) -> str:
    """Assemble a Snowflake Semantic View YAML from the LLM's structured JSON.

    Produces table-scoped YAML: facts, dimensions, and metrics are nested
    inside each table definition (not at root level). Root-level metrics are
    reserved for derived (cross-table) metrics only.

    Relationships use left_table/right_table/relationship_columns format.

    Args:
        structure: The JSON structure produced by the generation agent.
        table_metadata: Optional mapping of table_alias -> set of column names
                        for validation. If None, skips validation.
        sample_values_map: Optional mapping of FQN -> {column -> {sample_values, distinct_count}}
                           from discovery profiling. If provided, injects sample_values and is_enum
                           on dimensions during the lint pass.

    Returns:
        YAML string conforming to Snowflake's semantic view YAML specification.
    """
    # Auto-fix common structural issues before assembly
    structure = _lint_and_fix_structure(structure, table_metadata, sample_values_map)

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
            # Resolve case + quote for SQL expressions
            columns = _quote_column_refs(columns, tbl_alias, table_metadata)

        expr = _fill_template(template_name, FACT_TEMPLATES, columns)
        if expr is None:
            expr = _auto_recover_expr(template_name, columns, FACT_TEMPLATES)
            if expr:
                logger.info("Auto-recovered expression for fact %s: %s", fact["name"], expr)
            else:
                logger.warning("Cannot resolve expression for fact %s, skipping", fact["name"])
                continue

        # Post-fill quoting: catch column refs in raw/expr templates (e.g. CASE expressions)
        if table_metadata and tbl_alias:
            expr = _quote_columns_in_expr(expr, tbl_alias, table_metadata)

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
            # Resolve case + quote for SQL expressions
            columns = _quote_column_refs(columns, tbl_alias, table_metadata)

        expr = _fill_template(template_name, FACT_TEMPLATES, columns)
        if expr is None:
            expr = _auto_recover_expr(template_name, columns, FACT_TEMPLATES)
            if expr:
                logger.info("Auto-recovered expression for dimension %s: %s", dim["name"], expr)
            else:
                logger.warning("Cannot resolve expression for dimension %s, skipping", dim["name"])
                continue

        # Post-fill quoting: catch column refs in raw/expr templates
        if table_metadata and tbl_alias:
            expr = _quote_columns_in_expr(expr, tbl_alias, table_metadata)

        d: dict[str, Any] = {"name": dim["name"], "expr": expr}
        if dim.get("synonyms"):
            d["synonyms"] = dim["synonyms"]
        if dim.get("description"):
            d["description"] = dim["description"]
        if dim.get("data_type"):
            d["data_type"] = dim["data_type"]
        if dim.get("sample_values"):
            d["sample_values"] = dim["sample_values"]
            if dim.get("is_enum"):
                d["is_enum"] = True

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
            # Resolve case + quote for SQL expressions
            columns = _quote_column_refs(columns, tbl_alias, table_metadata)

        expr = _fill_template(template_name, FACT_TEMPLATES, columns)
        if expr is None:
            expr = _auto_recover_expr(template_name, columns, FACT_TEMPLATES)
            if expr:
                logger.info("Auto-recovered expression for time_dimension %s: %s", tdim["name"], expr)
            else:
                logger.warning("Cannot resolve expression for time_dimension %s, skipping", tdim["name"])
                continue

        # Post-fill quoting: catch column refs in raw/expr templates
        if table_metadata and tbl_alias:
            expr = _quote_columns_in_expr(expr, tbl_alias, table_metadata)

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
        if table_metadata and tbl_alias and f_entry["expr"]:
            f_entry["expr"] = _quote_columns_in_filter_expr(f_entry["expr"], tbl_alias, table_metadata)
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

        # Post-fill quoting: catch column refs in raw/expr metrics
        if table_metadata and tbl_alias:
            expr = _quote_columns_in_expr(expr, tbl_alias, table_metadata)

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
                if table_metadata:
                    pk = [safe_col(_resolve_column_case(c, alias, table_metadata)) for c in pk]
                t["primary_key"] = {"columns": pk}
            elif isinstance(pk, dict) and "columns" in pk:
                cols_list = pk["columns"]
                if table_metadata:
                    cols_list = [safe_col(_resolve_column_case(c, alias, table_metadata)) for c in cols_list]
                t["primary_key"] = {"columns": cols_list}
            elif isinstance(pk, str):
                resolved = safe_col(_resolve_column_case(pk, alias, table_metadata)) if table_metadata else pk
                t["primary_key"] = {"columns": [resolved]}
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
        r["relationship_columns"] = []
        for fc, tc in zip(from_cols, to_cols):
            lc = safe_col(_resolve_column_case(fc, from_table, table_metadata)) if table_metadata else fc
            rc = safe_col(_resolve_column_case(tc, to_table, table_metadata)) if table_metadata else tc
            r["relationship_columns"].append({"left_column": lc, "right_column": rc})
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

    # Sanitize all string values to ASCII-safe characters before YAML dump.
    # Some LLMs (e.g. gpt-5-mini) insert smart quotes, em-dashes, etc.
    # that Snowflake's YAML parser rejects as "special characters".
    # This is a no-op for models that already output clean ASCII (like Gemini).
    _sanitize_yaml_strings(doc)

    # Serialize to YAML
    return yaml.dump(doc, default_flow_style=False, sort_keys=False, allow_unicode=False)


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
