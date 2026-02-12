"""LangChain tools for Gold layer modeling operations.

Tools create star schema (fact + dimension) Dynamic Tables in a GOLD_EKAIX
schema, validate grain constraints, and persist documentation artifacts
(data catalog, business glossary, metrics definitions, validation rules).

Uses the same data isolation context and Snowflake connection as other tools.
"""

import json
import logging
import re
from typing import Any
from uuid import uuid4

from langchain_core.tools import tool

from config import get_settings
from services.snowflake import execute_query_sync, get_connection
from services import postgres as pg_service

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool_error(tool_name: str, message: str, **extra: Any) -> str:
    """Return a structured JSON error string for tool results."""
    result: dict[str, Any] = {"error": message, "tool": tool_name}
    result.update(extra)
    return json.dumps(result)


def _get_gold_schema_suffix() -> str:
    return getattr(get_settings(), "gold_target_schema_suffix", "GOLD_EKAIX")


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


def _quote_lowercase_columns_in_sql(sql: str) -> str:
    """Quote lowercase column identifiers in SQL to preserve Snowflake case sensitivity.

    Fetches actual column names from tables referenced in the SQL via SHOW COLUMNS.
    Any column that isn't fully uppercase gets wrapped in double quotes.
    Already-quoted identifiers are left unchanged.
    """
    try:
        conn = get_connection()
        cur = conn.cursor()

        # Find all FQN references like "DB"."SCHEMA"."TABLE" or DB.SCHEMA.TABLE
        fqn_pattern = re.compile(
            r'"([^"]+)"\."([^"]+)"\."([^"]+)"|(\w+)\.(\w+)\.(\w+)',
        )
        lowercase_cols: set[str] = set()

        seen_tables: set[str] = set()
        for m in fqn_pattern.finditer(sql):
            if m.group(1):
                db, schema, table = m.group(1), m.group(2), m.group(3)
            else:
                db, schema, table = m.group(4), m.group(5), m.group(6)
            fqn_key = f"{db}.{schema}.{table}".upper()
            if fqn_key in seen_tables:
                continue
            seen_tables.add(fqn_key)

            try:
                cur.execute(f'SHOW COLUMNS IN TABLE "{db}"."{schema}"."{table}"')
                for row in cur.fetchall():
                    col_name = row[2]  # column_name is 3rd field in SHOW COLUMNS
                    if col_name != col_name.upper():
                        lowercase_cols.add(col_name)
            except Exception as col_err:
                logger.debug("Could not fetch columns for %s: %s", fqn_key, col_err)

        if not lowercase_cols:
            cur.close()
            return sql

        # Sort by length descending to avoid partial replacements
        result = sql
        for col in sorted(lowercase_cols, key=len, reverse=True):
            # Replace unquoted occurrences (not already inside double quotes)
            # Match word boundary but not preceded/followed by double quote
            pattern = re.compile(
                r'(?<!")(?<!\w)' + re.escape(col) + r'(?!\w)(?!")',
            )
            result = pattern.sub(f'"{col}"', result)

        cur.close()
        logger.info("Quoted %d lowercase columns in DDL SQL", len(lowercase_cols))
        return result

    except Exception as e:
        logger.warning("_quote_lowercase_columns_in_sql failed (non-fatal): %s", e)
        return sql  # Return original SQL on failure


# ---------------------------------------------------------------------------
# Snowflake DDL Tools
# ---------------------------------------------------------------------------


@tool
def create_gold_table(ddl: str) -> str:
    """Execute a CREATE DYNAMIC TABLE statement to build a Gold layer table.

    Creates fact or dimension tables in the GOLD_EKAIX schema as Dynamic Tables.
    Only CREATE OR REPLACE DYNAMIC TABLE and CREATE SCHEMA statements are allowed.

    Before executing, ensures the target schema exists.

    Args:
        ddl: The DDL statement to execute (CREATE OR REPLACE DYNAMIC TABLE ...)
    """
    from tools.snowflake_tools import _validate_fqn

    ddl_upper = ddl.strip().upper()

    if not ddl_upper.startswith("CREATE OR REPLACE DYNAMIC TABLE") and \
       not ddl_upper.startswith("CREATE DYNAMIC TABLE") and \
       not ddl_upper.startswith("CREATE SCHEMA"):
        return _tool_error(
            "create_gold_table",
            "Only CREATE [OR REPLACE] DYNAMIC TABLE and CREATE SCHEMA statements are allowed.",
        )

    try:
        # Quote lowercase column references before executing
        ddl = _quote_lowercase_columns_in_sql(ddl)

        conn = get_connection()
        cur = conn.cursor()

        # Ensure target schema exists
        ddl_upper = ddl.strip().upper()
        if "DYNAMIC TABLE" in ddl_upper:
            import re
            fqn_match = re.search(
                r'DYNAMIC\s+TABLE\s+"([^"]+)"\."([^"]+)"\."([^"]+)"',
                ddl, re.IGNORECASE,
            )
            if fqn_match:
                db, schema = fqn_match.group(1), fqn_match.group(2)
                try:
                    cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{db}"."{schema}"')
                    logger.info("Ensured Gold schema exists: %s.%s", db, schema)
                except Exception as schema_err:
                    logger.warning("CREATE SCHEMA failed (may already exist): %s", schema_err)

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
        return _tool_error("create_gold_table", str(e))


@tool
def generate_gold_table_ddl(
    source_fqn: str,
    target_table_name: str,
    table_type: str,
    select_sql: str,
    target_lag: str = "",
) -> str:
    """Generate CREATE OR REPLACE DYNAMIC TABLE DDL for a Gold layer table.

    Constructs a fact or dimension Dynamic Table DDL from the provided SELECT
    statement. The target is placed in the GOLD_EKAIX schema of the source
    database.

    Args:
        source_fqn: One of the source Silver table FQNs (used to derive database name).
            Format: DATABASE.SCHEMA.TABLE
        target_table_name: Name of the Gold table (e.g., fact_generator_readings, dim_plant).
        table_type: Either "fact" or "dimension".
        select_sql: Complete SELECT statement that defines the table content.
            Must reference Silver layer tables with fully qualified names.
        target_lag: Refresh lag (default from config, typically "1 hour").
    """
    from tools.snowflake_tools import _validate_fqn, _quoted_fqn

    src_parts, src_err = _validate_fqn(source_fqn)
    if src_err:
        return _tool_error("generate_gold_table_ddl", f"Invalid source FQN: {src_err}")

    database = src_parts[0]
    gold_schema = _get_gold_schema_suffix()
    lag = target_lag or _get_target_lag()
    settings = get_settings()
    warehouse = settings.snowflake_warehouse or "COMPUTE_WH"

    target_fqn = f'"{database}"."{gold_schema}"."{target_table_name}"'

    ddl = f"""CREATE OR REPLACE DYNAMIC TABLE {target_fqn}
  TARGET_LAG = '{lag}'
  WAREHOUSE = "{warehouse}"
AS
{select_sql}"""

    return json.dumps({
        "ddl": ddl,
        "target_fqn": f"{database}.{gold_schema}.{target_table_name}",
        "table_type": table_type,
        "source_database": database,
    })


@tool
def validate_gold_grain(
    table_fqn: str,
    grain_columns: str,
) -> str:
    """Validate that a Gold fact table has the correct grain (no duplicates).

    Runs a GROUP BY + HAVING COUNT(*) > 1 query on the declared grain columns.
    If any duplicates are found, the grain is violated.

    Args:
        table_fqn: Fully qualified table name of the Gold table (DATABASE.GOLD_EKAIX.TABLE).
        grain_columns: Comma-separated list of columns that define the grain.
            Example: "plant_id_eia, report_date, generator_id"
    """
    from tools.snowflake_tools import _validate_fqn, _quoted_fqn

    parts, err = _validate_fqn(table_fqn)
    if err:
        return _tool_error("validate_gold_grain", f"Invalid FQN: {err}")

    quoted = _quoted_fqn(parts)
    cols = [c.strip() for c in grain_columns.split(",") if c.strip()]

    if not cols:
        return _tool_error("validate_gold_grain", "grain_columns cannot be empty")

    quoted_cols = ", ".join(f'"{c}"' for c in cols)

    try:
        conn = get_connection()
        cur = conn.cursor()

        # Check total row count
        cur.execute(f"SELECT COUNT(*) FROM {quoted}")
        total_rows = cur.fetchone()[0] or 0

        # Check for duplicates at declared grain
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

        # Check distinct grain combinations (use GROUP BY subquery to avoid ROW type)
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
        return _tool_error("validate_gold_grain", str(e), table=table_fqn)


# ---------------------------------------------------------------------------
# Documentation Artifact Tools (PostgreSQL persistence)
# ---------------------------------------------------------------------------


@tool
async def save_data_catalog(
    data_product_id: str,
    catalog_json: str,
) -> str:
    """Save a data catalog document for Gold layer tables.

    The catalog documents every Gold table and column: name, description,
    data type, source lineage, and transformations applied.

    Args:
        data_product_id: UUID of the data product.
        catalog_json: JSON string containing the catalog. Expected structure:
            {
              "tables": [
                {
                  "name": "fact_generator_readings",
                  "type": "fact",
                  "grain": "one row per generator per report_date",
                  "description": "...",
                  "source_tables": ["PUDL.SILVER_EKAIX.CORE_EIA860__SCD_GENERATORS"],
                  "columns": [
                    {"name": "plant_id_eia", "type": "NUMBER", "description": "...", "source": "..."}
                  ]
                }
              ]
            }
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
async def save_business_glossary(
    data_product_id: str,
    glossary_json: str,
) -> str:
    """Save a business glossary mapping business terms to physical columns.

    Each term has a definition, the business logic behind it, and which
    Gold table/column it maps to.

    Args:
        data_product_id: UUID of the data product.
        glossary_json: JSON string containing the glossary. Expected structure:
            {
              "terms": [
                {
                  "term": "Active Generator",
                  "definition": "A generator currently in operational status",
                  "logic": "dim_generator.operational_status = 'OP'",
                  "table": "dim_generator",
                  "column": "operational_status"
                }
              ]
            }
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
async def save_metrics_definitions(
    data_product_id: str,
    metrics_json: str,
) -> str:
    """Save KPI and metric definitions with formulas.

    Each metric has a name, description, SQL formula, unit, grain,
    and source fact table.

    Args:
        data_product_id: UUID of the data product.
        metrics_json: JSON string containing the metrics. Expected structure:
            {
              "metrics": [
                {
                  "name": "Total Capacity",
                  "description": "Sum of nameplate capacity across all generators",
                  "formula": "SUM(fact_generator_readings.capacity_mw)",
                  "unit": "MW",
                  "grain": "per plant, per report_date",
                  "source_table": "fact_generator_readings"
                }
              ]
            }
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
async def save_validation_rules(
    data_product_id: str,
    rules_json: str,
) -> str:
    """Save data validation rules for Gold layer tables.

    Each rule defines a check, severity (CRITICAL/WARNING/INFO),
    and the SQL query that verifies it.

    Args:
        data_product_id: UUID of the data product.
        rules_json: JSON string containing the rules. Expected structure:
            {
              "rules": [
                {
                  "name": "Grain: One Row Per Generator Per Date",
                  "category": "GRAIN",
                  "severity": "CRITICAL",
                  "table": "fact_generator_readings",
                  "check_sql": "SELECT ... GROUP BY ... HAVING COUNT(*) > 1",
                  "expected_result": "Zero rows returned",
                  "failure_action": "BLOCK — do not proceed to publishing"
                }
              ]
            }
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
    sql = """
    SELECT catalog_json, version, created_at
    FROM data_catalog
    WHERE data_product_id = $1::uuid
    ORDER BY version DESC
    LIMIT 1
    """
    rows = await pg_service.fetch(pool, sql, data_product_id)
    if not rows:
        return json.dumps({"status": "not_found", "data_product_id": data_product_id})

    row = rows[0]
    return json.dumps({
        "status": "ok",
        "version": row["version"],
        "catalog": row["catalog_json"],
        "created_at": str(row["created_at"]),
    }, default=str)


@tool
async def get_latest_business_glossary(data_product_id: str) -> str:
    """Retrieve the latest business glossary for a data product.

    Args:
        data_product_id: UUID of the data product.
    """
    pool = await _get_pool()
    sql = """
    SELECT glossary_json, version, created_at
    FROM business_glossary
    WHERE data_product_id = $1::uuid
    ORDER BY version DESC
    LIMIT 1
    """
    rows = await pg_service.fetch(pool, sql, data_product_id)
    if not rows:
        return json.dumps({"status": "not_found", "data_product_id": data_product_id})

    row = rows[0]
    return json.dumps({
        "status": "ok",
        "version": row["version"],
        "glossary": row["glossary_json"],
        "created_at": str(row["created_at"]),
    }, default=str)


@tool
async def get_latest_metrics_definitions(data_product_id: str) -> str:
    """Retrieve the latest metrics/KPI definitions for a data product.

    Args:
        data_product_id: UUID of the data product.
    """
    pool = await _get_pool()
    sql = """
    SELECT metrics_json, version, created_at
    FROM metrics_definitions
    WHERE data_product_id = $1::uuid
    ORDER BY version DESC
    LIMIT 1
    """
    rows = await pg_service.fetch(pool, sql, data_product_id)
    if not rows:
        return json.dumps({"status": "not_found", "data_product_id": data_product_id})

    row = rows[0]
    return json.dumps({
        "status": "ok",
        "version": row["version"],
        "metrics": row["metrics_json"],
        "created_at": str(row["created_at"]),
    }, default=str)


@tool
async def get_latest_validation_rules(data_product_id: str) -> str:
    """Retrieve the latest validation rules for a data product.

    Args:
        data_product_id: UUID of the data product.
    """
    pool = await _get_pool()
    sql = """
    SELECT rules_json, version, created_at
    FROM validation_rules
    WHERE data_product_id = $1::uuid
    ORDER BY version DESC
    LIMIT 1
    """
    rows = await pg_service.fetch(pool, sql, data_product_id)
    if not rows:
        return json.dumps({"status": "not_found", "data_product_id": data_product_id})

    row = rows[0]
    return json.dumps({
        "status": "ok",
        "version": row["version"],
        "rules": row["rules_json"],
        "created_at": str(row["created_at"]),
    }, default=str)


@tool
def register_gold_layer(
    data_product_id: str,
    gold_table_mapping_json: str,
) -> str:
    """Register Gold layer tables as the final working layer for semantic modeling.

    Stores a mapping of Silver FQNs to Gold FQNs so the generation agent
    points the Semantic View at Gold tables instead of Silver.

    Also updates the Neo4j graph with lineage relationships:
    (:SilverTable)-[:MODELED_TO]->(:GoldTable)

    Args:
        data_product_id: UUID of the data product.
        gold_table_mapping_json: JSON object mapping Silver FQN to Gold FQN.
            Example: {"PUDL.SILVER_EKAIX.TABLE": "PUDL.GOLD_EKAIX.FACT_TABLE"}
    """
    try:
        mapping = json.loads(gold_table_mapping_json)
    except json.JSONDecodeError as e:
        return _tool_error("register_gold_layer", f"Invalid JSON: {e}")

    if not isinstance(mapping, dict) or not mapping:
        return _tool_error("register_gold_layer", "Mapping must be a non-empty JSON object")

    try:
        import asyncio
        from services import redis as redis_service

        settings = get_settings()

        async def _save() -> dict[str, str]:
            client = await redis_service.get_client(settings.redis_url)

            # Load existing silver working layer (source→silver mapping)
            silver_key = f"cache:working_layer:{data_product_id}"
            silver_map = await redis_service.get_json(client, silver_key) or {}

            # Build the complete source→gold chain
            # silver_map: {SOURCE_FQN: SILVER_FQN}
            # mapping:    {SILVER_FQN: GOLD_FQN}
            # Result:     {SOURCE_FQN: GOLD_FQN} — generation uses this
            complete_map: dict[str, str] = {}
            mapping_upper = {k.upper(): v for k, v in mapping.items()}

            for source_fqn, silver_fqn in silver_map.items():
                gold_fqn = mapping_upper.get(silver_fqn.upper())
                if gold_fqn:
                    complete_map[source_fqn.upper()] = gold_fqn
                else:
                    # Silver table not modeled into Gold — keep Silver as target
                    complete_map[source_fqn.upper()] = silver_fqn

            # If no silver map exists, treat mapping as direct source→gold
            if not silver_map:
                complete_map = {k.upper(): v for k, v in mapping.items()}

            # Overwrite the working layer with the complete chain
            await redis_service.set_json(client, silver_key, complete_map, ttl=86400)

            # Also store gold-specific mapping separately
            gold_key = f"cache:gold_layer:{data_product_id}"
            await redis_service.set_json(client, gold_key, mapping, ttl=86400)

            # Persist to PostgreSQL
            if pg_service._pool is not None:
                pool = pg_service._pool
                sql = """
                UPDATE data_products
                SET settings = COALESCE(settings, '{}'::jsonb)
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

            return silver_map

        silver_map_snapshot = asyncio.run(_save())

        # Update Neo4j lineage (pass silver map for Table→SilverTable links)
        _update_neo4j_lineage(data_product_id, mapping, silver_map_snapshot)

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
        return _tool_error("register_gold_layer", str(e))


def _update_neo4j_lineage(
    data_product_id: str,
    silver_to_gold: dict[str, str],
    source_to_silver: dict[str, str] | None = None,
) -> None:
    """Write full lineage relationships to Neo4j.

    Creates three types of relationships when data is available:
    - (:Table)-[:TRANSFORMED_TO]->(:SilverTable) — source→silver links
    - (:SilverTable)-[:MODELED_TO]->(:GoldTable) — silver→gold links
    """
    try:
        from neo4j import GraphDatabase

        settings = get_settings()
        uri = settings.neo4j_uri
        user = settings.neo4j_user
        password = settings.neo4j_password

        if not uri:
            logger.warning("Neo4j not configured — skipping lineage update")
            return

        driver = GraphDatabase.driver(uri, auth=(user, password))

        with driver.session() as session:
            # 1. Source → Silver lineage (Table nodes already exist from ERD)
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
                logger.info("Neo4j lineage: %d Source→Silver relationships", len(source_to_silver))

            # 2. Silver → Gold lineage
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
