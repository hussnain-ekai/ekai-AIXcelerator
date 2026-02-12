"""Deterministic discovery pipeline — runs profiling, classification, ERD
construction, quality scoring, and artifact persistence BEFORE the LLM.

Each step emits ``pipeline_progress`` SSE events for real-time progress bars.
Results are cached in Redis to avoid re-running on repeat triggers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any
from uuid import uuid4

from config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pipeline step definitions
# ---------------------------------------------------------------------------

STEPS = [
    {"key": "metadata", "label": "Reading data structure"},
    {"key": "profiling", "label": "Analyzing data patterns"},
    {"key": "classification", "label": "Classifying data"},
    {"key": "maturity", "label": "Classifying data maturity"},
    {"key": "quality", "label": "Checking data quality"},
    {"key": "artifacts", "label": "Saving quality report"},
]

TOTAL_STEPS = len(STEPS)
CACHE_KEY_PREFIX = "discovery:pipeline"
CACHE_TTL = 86400  # 24 hours
FRESH_THRESHOLD = 300  # 5 minutes — skip pipeline if cache is this fresh

# Column types for which sample_values are collected during profiling
_STRING_TYPES = {"VARCHAR", "TEXT", "STRING", "CHAR", "NCHAR", "NVARCHAR", "NTEXT"}


# ---------------------------------------------------------------------------
# SSE progress helpers
# ---------------------------------------------------------------------------


async def _emit(
    queue: asyncio.Queue[dict | None],
    step_key: str,
    label: str,
    status: str,
    detail: str,
    current: int,
    total: int,
    step_index: int,
) -> None:
    """Push a pipeline_progress event to the SSE queue."""
    # Calculate overall percentage: completed steps + fraction of current step
    if status == "completed":
        completed_steps = step_index + 1
    else:
        frac = current / total if total > 0 else 0
        completed_steps = step_index + frac
    overall_pct = int(completed_steps / TOTAL_STEPS * 100)

    logger.info(
        "PIPELINE_EMIT step=%s status=%s detail=%s pct=%d",
        step_key, status, detail, min(overall_pct, 100),
    )
    await queue.put({
        "type": "pipeline_progress",
        "data": {
            "step": step_key,
            "label": label,
            "status": status,
            "detail": detail,
            "current": current,
            "total": total,
            "step_index": step_index,
            "total_steps": TOTAL_STEPS,
            "overall_pct": min(overall_pct, 100),
        },
    })
    # Yield control so the SSE generator can flush this event to the client
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def run_discovery_pipeline(
    data_product_id: str,
    tables: list[str],
    database: str,
    schemas: list[str],
    queue: asyncio.Queue[dict | None],
    force: bool = False,
) -> dict[str, Any]:
    """Execute the 5-step deterministic discovery pipeline (Phase 1).

    Args:
        data_product_id: UUID of the data product.
        tables: List of fully-qualified table names (DATABASE.SCHEMA.TABLE).
        database: Snowflake database name.
        schemas: List of schema names.
        queue: SSE event queue for progress updates.
        force: If True, skip cache and re-run the pipeline.

    Returns:
        A dict with Phase 1 results (metadata, profiles, classifications,
        quality, artifacts). FK inference and ERD construction happen in
        Phase 2 after the discovery conversation.
    """
    settings = get_settings()

    # -----------------------------------------------------------------------
    # Check Redis cache
    # -----------------------------------------------------------------------
    if not force:
        try:
            from services import redis as redis_service

            client = await redis_service.get_client(settings.redis_url)
            cache_key = f"{CACHE_KEY_PREFIX}:{data_product_id}"
            cached = await redis_service.get_json(client, cache_key)
            if cached:
                cached_at = cached.get("_cached_at", 0)
                age = time.time() - cached_at
                if age < FRESH_THRESHOLD:
                    logger.info(
                        "Pipeline cache hit for %s (age=%.0fs), skipping pipeline",
                        data_product_id, age,
                    )
                    # Emit instant completed progress for all steps
                    for idx, step in enumerate(STEPS):
                        await _emit(queue, step["key"], step["label"], "completed",
                                    "Cached", 1, 1, idx)
                    return cached
        except Exception as e:
            logger.warning("Redis cache check failed, running pipeline: %s", e)

    # -----------------------------------------------------------------------
    # Run the pipeline
    # -----------------------------------------------------------------------
    results: dict[str, Any] = {
        "data_product_id": data_product_id,
        "database": database,
        "schemas": schemas,
        "tables": tables,
    }

    try:
        # Step 1: Metadata ------------------------------------------------
        results["metadata"] = await _step_metadata(
            queue, database, schemas, tables,
        )

        # Step 2: Profiling ------------------------------------------------
        results["profiles"] = await _step_profiling(
            queue, tables,
        )

        # Step 3: Classification -------------------------------------------
        results["classifications"] = _step_classification(
            results["metadata"],
        )
        await _emit(queue, "classification", STEPS[2]["label"],
                     "completed", "Done", 1, 1, 2)

        # Step 4: Data maturity classification -----------------------------
        results["maturity_classifications"] = await _step_classify_maturity(
            queue, results["profiles"], results["metadata"],
        )

        # Step 5: Quality score --------------------------------------------
        results["quality"] = _step_quality(results)
        await _emit(queue, "quality", STEPS[4]["label"],
                     "completed", "Done", 1, 1, 4)

        # Step 6: Artifact persistence (quality report only) ---------------
        results["artifacts"] = await _step_artifacts(
            queue, data_product_id, results,
        )

    except Exception as e:
        logger.exception("Discovery pipeline failed: %s", e)
        await _emit(queue, "error", "Pipeline error", "error", str(e), 0, 0, 0)
        # Return partial results — never crash the stream
        results["_error"] = str(e)
        return results

    # -----------------------------------------------------------------------
    # Cache results
    # -----------------------------------------------------------------------
    try:
        from services import redis as redis_service

        client = await redis_service.get_client(settings.redis_url)
        cache_key = f"{CACHE_KEY_PREFIX}:{data_product_id}"
        results["_cached_at"] = time.time()
        await redis_service.set_json(client, cache_key, results, ttl=CACHE_TTL)
        logger.info("Pipeline results cached for %s", data_product_id)
    except Exception as e:
        logger.warning("Failed to cache pipeline results: %s", e)

    return results


# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------


async def _step_metadata(
    queue: asyncio.Queue[dict | None],
    database: str,
    schemas: list[str],
    tables: list[str],
) -> list[dict[str, Any]]:
    """Step 1: Fetch table/view/column metadata from Snowflake."""
    step_idx = 0
    await _emit(queue, "metadata", STEPS[step_idx]["label"],
                "running", "Querying Snowflake...", 0, 1, step_idx)

    from services.snowflake import execute_query
    from tools.snowflake_tools import _parse_data_type, _validate_identifier

    all_tables: list[dict[str, Any]] = []

    for schema_name in schemas:
        # Validate identifiers
        if _validate_identifier(database, "database") or _validate_identifier(schema_name, "schema"):
            continue

        try:
            sf_tables = await execute_query(
                f'SHOW TABLES IN SCHEMA "{database}"."{schema_name}"'
            )
            sf_views = await execute_query(
                f'SHOW VIEWS IN SCHEMA "{database}"."{schema_name}"'
            )
        except Exception as e:
            logger.warning("Failed to list objects in %s.%s: %s", database, schema_name, e)
            continue

        objects: list[dict[str, Any]] = []
        for t in sf_tables:
            objects.append({
                "name": t.get("name", ""),
                "row_count": t.get("rows", 0),
                "object_type": "TABLE",
                "comment": t.get("comment") or "",
            })
        for v in sf_views:
            objects.append({
                "name": v.get("name", ""),
                "row_count": None,
                "object_type": "VIEW",
                "comment": v.get("comment") or "",
            })

        for obj in objects:
            obj_name = obj["name"]
            fqn = f"{database}.{schema_name}.{obj_name}"
            # Only include tables that are in the data product's table list
            if fqn not in tables:
                continue

            try:
                cols = await execute_query(
                    f'SHOW COLUMNS IN TABLE "{database}"."{schema_name}"."{obj_name}"'
                )
            except Exception as e:
                logger.warning("Failed to get columns for %s: %s", fqn, e)
                cols = []

            columns = []
            for idx, col in enumerate(cols):
                col_name = col.get("column_name", "")
                nullable = col.get("null?", True)
                columns.append({
                    "name": col_name,
                    "data_type": _parse_data_type(col.get("data_type", "{}")),
                    "nullable": nullable in (True, "true", "Y", "YES"),
                    "comment": col.get("comment") or "",
                    "position": idx + 1,
                })

            all_tables.append({
                "fqn": fqn,
                "name": obj_name,
                "schema": schema_name,
                "row_count": obj["row_count"],
                "object_type": obj["object_type"],
                "comment": obj["comment"],
                "columns": columns,
            })

    await _emit(queue, "metadata", STEPS[step_idx]["label"],
                "completed", f"{len(all_tables)} tables found", 1, 1, step_idx)
    return all_tables


async def _step_profiling(
    queue: asyncio.Queue[dict | None],
    tables: list[str],
) -> list[dict[str, Any]]:
    """Step 2: Profile each table (batch aggregate SQL)."""
    step_idx = 1
    total = len(tables)
    profiles: list[dict[str, Any]] = []

    from services.snowflake import execute_query
    from tools.snowflake_tools import _validate_fqn, _quoted_fqn, SAMPLE_SIZE

    for i, table_fqn in enumerate(tables):
        table_name = table_fqn.split(".")[-1] if "." in table_fqn else table_fqn
        await _emit(queue, "profiling", STEPS[step_idx]["label"],
                    "running", f"Analyzing {table_name} ({i + 1} of {total})",
                    i, total, step_idx)

        parts, fqn_err = _validate_fqn(table_fqn)
        if fqn_err:
            logger.warning("Invalid FQN %s: %s", table_fqn, fqn_err)
            profiles.append({"table": table_fqn, "error": fqn_err, "columns": []})
            continue

        quoted = _quoted_fqn(parts)

        try:
            # Get metadata for sampling strategy
            meta = await execute_query(
                f'SELECT "ROW_COUNT", "TABLE_TYPE" FROM "{parts[0]}".INFORMATION_SCHEMA.TABLES '
                f"WHERE TABLE_SCHEMA='{parts[1]}' AND TABLE_NAME='{parts[2]}'"
            )
            meta_row = meta[0] if meta else {}
            table_type = meta_row.get("TABLE_TYPE")
            row_count_val = meta_row.get("ROW_COUNT")
            is_view = not meta or table_type in ("VIEW", "MATERIALIZED VIEW")
            metadata_row_count = int(row_count_val) if row_count_val is not None else None

            # Determine sampling strategy
            sampled = False
            if is_view or metadata_row_count is None:
                from_clause = f"(SELECT * FROM {quoted} LIMIT {SAMPLE_SIZE}) AS _sample"
                sampled = True
                total_rows = None
            elif metadata_row_count == 0:
                profiles.append({"table": table_fqn, "row_count": 0, "columns": [], "sampled": False})
                continue
            elif metadata_row_count <= SAMPLE_SIZE:
                from_clause = quoted
                total_rows = metadata_row_count
            else:
                from_clause = f"{quoted} TABLESAMPLE BERNOULLI ({SAMPLE_SIZE} ROWS)"
                sampled = True
                total_rows = metadata_row_count

            # Get columns
            raw_cols = await execute_query(f'SHOW COLUMNS IN TABLE {quoted}')
            from tools.snowflake_tools import _parse_data_type
            columns = []
            for col in raw_cols:
                nullable = col.get("null?", True)
                columns.append({
                    "column_name": col.get("column_name", ""),
                    "data_type": _parse_data_type(col.get("data_type", "{}")),
                    "is_nullable": "YES" if nullable in (True, "true", "Y", "YES") else "NO",
                })

            if not columns:
                profiles.append({"table": table_fqn, "row_count": total_rows or 0, "columns": [], "sampled": sampled})
                continue

            # Batch profile all columns
            col_expressions = []
            for col in columns:
                cn = col["column_name"]
                if not cn:
                    continue
                expr = (
                    f'COUNT("{cn}") AS "nn_{cn}", '
                    f'APPROX_COUNT_DISTINCT("{cn}") AS "dc_{cn}"'
                )
                # For string-type columns, also collect up to 25 sample distinct values
                if col["data_type"].upper() in _STRING_TYPES:
                    expr += f', ARRAY_SLICE(ARRAY_AGG(DISTINCT "{cn}"), 0, 25) AS "sv_{cn}"'
                col_expressions.append(expr)

            batch_row: dict[str, Any] = {}
            sample_n = 0
            if col_expressions:
                batch_sql = (
                    f'SELECT COUNT(*) AS "_sample_n", {", ".join(col_expressions)} '
                    f"FROM {from_clause}"
                )
                batch_result = await execute_query(batch_sql)
                batch_row = batch_result[0] if batch_result else {}

            sample_n = batch_row.get("_sample_n", 0) or 0
            if total_rows is None:
                total_rows = sample_n

            profile_cols = []
            for col in columns:
                col_name = col["column_name"]
                if not col_name:
                    continue
                try:
                    non_null = batch_row.get(f"nn_{col_name}", 0) or 0
                    distinct = batch_row.get(f"dc_{col_name}", 0) or 0
                    null_pct = round((1 - non_null / sample_n) * 100, 2) if sample_n > 0 else 0
                    uniqueness_pct = round((distinct / non_null) * 100, 2) if non_null > 0 else 0

                    # Semantic PK filtering: exclude columns unlikely to be keys
                    data_type_upper = col["data_type"].upper()
                    col_name_lower = col_name.lower()
                    pk_excluded_keywords = (
                        "description", "comment", "note", "text", "body",
                        "message", "remark", "summary", "detail", "content",
                    )
                    is_text_like = data_type_upper in (
                        "TEXT", "CLOB", "NCLOB", "STRING", "VARIANT",
                    )
                    is_excluded_name = any(kw in col_name_lower for kw in pk_excluded_keywords)
                    stats_say_pk = uniqueness_pct > 98 and null_pct == 0
                    is_likely_pk = stats_say_pk and not is_text_like and not is_excluded_name

                    entry: dict[str, Any] = {
                        "column": col_name,
                        "data_type": col["data_type"],
                        "nullable": col["is_nullable"] == "YES",
                        "null_pct": null_pct,
                        "uniqueness_pct": uniqueness_pct,
                        "distinct_count": distinct,
                        "total_rows": total_rows,
                        "is_likely_pk": is_likely_pk,
                        "sampled": sampled,
                    }

                    # Attach sample_values for string-type columns
                    sv_key = f"sv_{col_name}"
                    raw_sv = batch_row.get(sv_key)
                    if raw_sv is not None:
                        # Snowflake ARRAY comes back as JSON string from the connector
                        if isinstance(raw_sv, str):
                            try:
                                raw_sv = json.loads(raw_sv)
                            except (json.JSONDecodeError, TypeError):
                                raw_sv = None
                        if isinstance(raw_sv, list):
                            sample_vals = [str(v) for v in raw_sv if v is not None][:25]
                            if sample_vals:
                                entry["sample_values"] = sample_vals

                    profile_cols.append(entry)
                except Exception as col_err:
                    logger.warning("Profiling column %s.%s failed: %s", table_fqn, col_name, col_err)

            # Composite PK detection: if no single-column PK was found, test
            # candidate combinations of NOT-NULL columns ending in _id/_key
            # plus timestamp columns.
            has_single_pk = any(pc.get("is_likely_pk") for pc in profile_cols)
            if not has_single_pk and total_rows and total_rows > 0:
                id_cols = [
                    pc["column"] for pc in profile_cols
                    if pc["null_pct"] == 0
                    and (pc["column"].lower().endswith("_id")
                         or pc["column"].lower().endswith("_key"))
                ]
                ts_cols = [
                    pc["column"] for pc in profile_cols
                    if pc["null_pct"] == 0
                    and pc["data_type"].upper() in (
                        "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ",
                        "TIMESTAMP", "DATE", "DATETIME",
                    )
                ]
                # Try: all id_cols + first timestamp (common pattern)
                candidates: list[list[str]] = []
                if id_cols and ts_cols:
                    candidates.append(id_cols + [ts_cols[0]])
                if len(id_cols) >= 2:
                    candidates.append(id_cols)

                for combo in candidates:
                    if len(combo) < 2 or len(combo) > 5:
                        continue
                    quoted = ", ".join(f'"{c}"' for c in combo)
                    try:
                        # Snowflake doesn't support COUNT(DISTINCT (col1, col2)) tuple syntax.
                        # Use a subquery with GROUP BY instead.
                        ck_sql = (
                            f"SELECT "
                            f"(SELECT COUNT(*) FROM {from_clause}) AS total, "
                            f"(SELECT COUNT(*) FROM "
                            f"(SELECT 1 FROM {from_clause} GROUP BY {quoted})) AS uniq"
                        )
                        ck_result = await execute_query(ck_sql)
                        ck_row = ck_result[0] if ck_result else {}
                        ck_total = ck_row.get("TOTAL", 0) or 0
                        ck_uniq = ck_row.get("UNIQ", 0) or 0
                        if ck_total > 0 and ck_uniq / ck_total > 0.98:
                            # Mark these columns as composite PK
                            combo_set = set(combo)
                            for pc in profile_cols:
                                if pc["column"] in combo_set:
                                    pc["is_likely_pk"] = True
                            logger.info(
                                "Composite PK detected for %s: %s",
                                table_fqn, combo,
                            )
                            break  # Use first valid composite key
                    except Exception as ck_err:
                        logger.debug("Composite PK check failed for %s: %s", table_fqn, ck_err)

            profiles.append({
                "table": table_fqn,
                "row_count": total_rows,
                "column_count": len(columns),
                "columns": profile_cols,
                "sampled": sampled,
                "sample_size": sample_n if sampled else total_rows,
            })

        except Exception as e:
            logger.warning("Profiling table %s failed: %s", table_fqn, e)
            profiles.append({"table": table_fqn, "error": str(e), "columns": []})

    await _emit(queue, "profiling", STEPS[step_idx]["label"],
                "completed", f"{len(profiles)} tables profiled", total, total, step_idx)
    return profiles


def _step_classification(
    metadata: list[dict[str, Any]],
) -> dict[str, str]:
    """Step 3: Classify each table as FACT or DIMENSION (pure Python)."""
    from agents.discovery import classify_table

    classifications: dict[str, str] = {}
    for table in metadata:
        col_names = [c["name"] for c in table.get("columns", [])]
        classification = classify_table(
            table["name"],
            col_names,
            table.get("row_count") or 0,
        )
        classifications[table["fqn"]] = classification
    return classifications


_DUP_CHECK_LIMIT = 10_000  # Row limit for duplicate rate estimation


async def _compute_duplicate_rate(fqn: str) -> float:
    """Compute approximate duplicate row rate for a table.

    Returns a float between 0.0 (no duplicates) and 1.0 (all duplicates).
    Uses HASH(*) over a 10K-row sample for speed.
    """
    from services.snowflake import execute_query
    from tools.snowflake_tools import _validate_fqn, _quoted_fqn

    parts, err = _validate_fqn(fqn)
    if err:
        return 0.0

    quoted = _quoted_fqn(parts)

    try:
        sql = (
            f"SELECT COUNT(*) AS total, COUNT(DISTINCT HASH(*)) AS distinct_hashes "
            f"FROM (SELECT * FROM {quoted} LIMIT {_DUP_CHECK_LIMIT}) AS _dup_sample"
        )
        result = await execute_query(sql)
        if not result:
            return 0.0
        row = result[0]
        total = row.get("TOTAL", 0) or 0
        distinct = row.get("DISTINCT_HASHES", 0) or 0
        if total == 0:
            return 0.0
        return max(0.0, 1.0 - distinct / total)
    except Exception as e:
        logger.warning("Duplicate rate check failed for %s: %s", fqn, e)
        return 0.0


async def _step_classify_maturity(
    queue: asyncio.Queue[dict | None],
    profiles: list[dict[str, Any]],
    metadata: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Step 4: Classify data maturity (bronze/silver/gold) per table."""
    step_idx = 3
    total = len(profiles)
    await _emit(queue, "maturity", STEPS[step_idx]["label"],
                "running", "Analyzing data maturity...", 0, total, step_idx)

    from agents.discovery import classify_data_maturity

    classifications: dict[str, dict[str, Any]] = {}

    for i, profile in enumerate(profiles):
        if profile.get("error"):
            continue
        fqn = profile.get("table", "")
        columns = profile.get("columns", [])
        if not columns:
            continue

        table_name = fqn.split(".")[-1] if "." in fqn else fqn
        await _emit(queue, "maturity", STEPS[step_idx]["label"],
                    "running", f"Classifying {table_name} ({i + 1} of {total})",
                    i, total, step_idx)

        # Compute duplicate rate (the only signal that needs SQL)
        dup_rate = await _compute_duplicate_rate(fqn)

        # Use the shared classification function from discovery.py
        result = classify_data_maturity(columns, duplicate_rate=dup_rate)
        classifications[fqn] = result

    await _emit(queue, "maturity", STEPS[step_idx]["label"],
                "completed", f"{len(classifications)} tables classified",
                total, total, step_idx)
    return classifications


def _step_fk_inference(
    metadata: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Infer foreign key relationships (pure Python). Used by Phase 2."""
    from agents.discovery import infer_foreign_keys

    tables_for_fk = _build_fk_input(metadata, profiles)
    return infer_foreign_keys(tables_for_fk)


async def _step_erd(
    queue: asyncio.Queue[dict | None],
    data_product_id: str,
    results: dict[str, Any],
) -> dict[str, Any]:
    """Build ERD graph in Neo4j (Phase 2)."""
    await _emit(queue, "erd", "Building data map",
                "running", "Writing to graph...", 0, 1, 0)

    metadata = results.get("metadata", [])
    classifications = results.get("classifications", {})
    relationships = results.get("relationships", [])
    profiles = results.get("profiles", [])

    # Build profile lookup: fqn -> profile columns
    profile_map: dict[str, list[dict[str, Any]]] = {}
    for p in profiles:
        if "columns" in p:
            profile_map[p["table"]] = p.get("columns", [])

    try:
        from services import neo4j as neo4j_service

        if neo4j_service._driver is None:
            logger.warning("Neo4j driver not initialized, skipping ERD step")
            await _emit(queue, "erd", "Building data map",
                        "completed", "Skipped (Neo4j unavailable)", 1, 1, 0)
            return {"status": "skipped", "reason": "neo4j_unavailable"}

        driver = neo4j_service._driver

        # Upsert table + column nodes
        for table in metadata:
            fqn = table["fqn"]
            classification = classifications.get(fqn, "UNKNOWN")
            profile_cols = profile_map.get(fqn, [])

            table_cypher = """
            MERGE (t:Table {fqn: $fqn})
            SET t.data_product_id = $dp_id,
                t.classification = $classification,
                t.row_count = $row_count
            """
            await neo4j_service.execute_write(
                driver, table_cypher,
                fqn=fqn,
                dp_id=data_product_id,
                classification=classification,
                row_count=table.get("row_count") or 0,
            )

            for col in table.get("columns", []):
                # Determine if PK from profile data
                is_pk = False
                for pc in profile_cols:
                    if pc.get("column") == col["name"]:
                        is_pk = pc.get("is_likely_pk", False)
                        break

                col_cypher = """
                MATCH (t:Table {fqn: $table_fqn})
                MERGE (c:Column {name: $name, table_fqn: $table_fqn})
                SET c.data_type = $data_type,
                    c.nullable = $nullable,
                    c.is_pk = $is_pk
                MERGE (t)-[:HAS_COLUMN]->(c)
                """
                await neo4j_service.execute_write(
                    driver, col_cypher,
                    table_fqn=fqn,
                    name=col["name"],
                    data_type=col.get("data_type", "VARCHAR"),
                    nullable=col.get("nullable", True),
                    is_pk=is_pk,
                )

        # Upsert FK relationships
        for rel in relationships:
            edge_cypher = """
            MATCH (src:Table {fqn: $source})
            MATCH (tgt:Table {fqn: $target})
            MERGE (src)-[r:FK_REFERENCES]->(tgt)
            SET r.confidence = $confidence,
                r.cardinality = $cardinality,
                r.source_column = $source_column,
                r.target_column = $target_column
            """
            await neo4j_service.execute_write(
                driver, edge_cypher,
                source=rel["from_table"],
                target=rel["to_table"],
                confidence=rel.get("confidence", 0.0),
                cardinality=rel.get("cardinality", "many_to_one"),
                source_column=rel.get("from_column", ""),
                target_column=rel.get("to_column", ""),
            )

        await _emit(queue, "erd", "Building data map",
                    "completed", f"{len(metadata)} tables, {len(relationships)} connections",
                    1, 1, 0)
        return {
            "status": "ok",
            "nodes_upserted": len(metadata),
            "edges_upserted": len(relationships),
        }

    except Exception as e:
        logger.error("ERD step failed: %s", e)
        await _emit(queue, "erd", "Building data map",
                    "completed", "Partial (graph write failed)", 1, 1, 0)
        return {"status": "error", "error": str(e)}


def _step_quality(results: dict[str, Any]) -> dict[str, Any]:
    """Step 4: Compute data quality health score (pure Python)."""
    from agents.discovery import compute_health_score

    profiles = results.get("profiles", [])
    classifications = results.get("classifications", {})

    completeness_pcts: list[float] = []
    issues: list[dict[str, Any]] = []
    check_results: dict[str, list[Any]] = {
        "duplicate_pks": [],
        "orphaned_fks": [],
        "numeric_varchars": [],
        "missing_descriptions": [],
    }

    for profile in profiles:
        if profile.get("error"):
            continue
        columns = profile.get("columns", [])
        if not columns:
            completeness_pcts.append(0.0)
            continue

        # Completeness is measured ONLY on identifier columns (likely PKs/FKs).
        # Rationale: sparse optional columns (e.g., coal-specific fields on solar
        # plants) are structurally correct — their nulls are not quality issues.
        # What matters is whether the core identifiers that link tables together
        # are populated and consistent.
        id_null_pcts: list[float] = []
        for c in columns:
            if "null_pct" not in c:
                continue
            col_name = c.get("column", "").lower()
            is_id = (
                c.get("is_likely_pk", False)
                or col_name.endswith("_id")
                or col_name == "id"
                or col_name.endswith("_code")
                or col_name.endswith("_key")
            )
            if is_id:
                id_null_pcts.append(c["null_pct"])

        if id_null_pcts:
            avg_non_null = 100.0 - (sum(id_null_pcts) / len(id_null_pcts))
            completeness_pcts.append(max(0.0, avg_non_null))
        else:
            # No identifier columns found — fall back to all columns
            all_null_pcts = [c.get("null_pct", 0) for c in columns if "null_pct" in c]
            if all_null_pcts:
                avg_non_null = 100.0 - (sum(all_null_pcts) / len(all_null_pcts))
                completeness_pcts.append(max(0.0, avg_non_null))
            else:
                completeness_pcts.append(0.0)

        # Only flag identifier columns with gaps — these are real quality issues.
        # Non-identifier columns with high nulls are informational (shown in the
        # report but don't affect the score).
        table_name = profile.get("table", "").split(".")[-1]
        for col in columns:
            null_pct = col.get("null_pct", 0)
            if null_pct <= 5:
                continue
            col_name = col.get("column", "").lower()
            is_id = (
                col.get("is_likely_pk", False)
                or col_name.endswith("_id")
                or col_name == "id"
                or col_name.endswith("_code")
                or col_name.endswith("_key")
            )
            if is_id and null_pct > 5:
                issues.append({
                    "severity": "warning",
                    "message": f"{table_name}.{col['column']} is {100 - null_pct:.0f}% complete",
                    "affected_tables": [profile.get("table", "")],
                })

    # Check for missing descriptions in metadata
    for table in results.get("metadata", []):
        if not table.get("comment"):
            check_results["missing_descriptions"].append({
                "table": table["fqn"],
                "issue": "no_description",
            })

    check_results["completeness_pcts"] = completeness_pcts
    overall_score = compute_health_score(check_results)
    avg_completeness = sum(completeness_pcts) / len(completeness_pcts) if completeness_pcts else 0

    return {
        "overall_score": overall_score,
        "avg_completeness_pct": round(avg_completeness, 1),
        "table_count": len(profiles),
        "issues": issues,
        "check_results": {k: v for k, v in check_results.items() if k != "completeness_pcts"},
    }


async def _step_artifacts(
    queue: asyncio.Queue[dict | None],
    data_product_id: str,
    results: dict[str, Any],
) -> dict[str, Any]:
    """Step 6: Persist quality report to PostgreSQL + MinIO (Phase 1 only)."""
    step_idx = 5
    await _emit(queue, "artifacts", STEPS[step_idx]["label"],
                "running", "Saving quality report...", 0, 1, step_idx)

    quality = results.get("quality", {})
    artifact_ids: dict[str, str] = {}
    settings = get_settings()

    # 7a: Save quality report to PostgreSQL
    try:
        from services import postgres as pg_service

        if pg_service._pool is not None:
            pool = pg_service._pool
            report_id = str(uuid4())
            sql = """
            INSERT INTO data_quality_checks (id, data_product_id, overall_score, check_results, issues)
            VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5::jsonb)
            """
            await pg_service.execute(
                pool, sql,
                report_id,
                data_product_id,
                quality.get("overall_score", 0),
                json.dumps(quality.get("check_results", {})),
                json.dumps(quality.get("issues", [])),
            )
            artifact_ids["quality_report_db"] = report_id
            logger.info("Quality report saved to PostgreSQL: %s", report_id)
    except Exception as e:
        logger.warning("Failed to save quality report to PostgreSQL: %s", e)

    # 5b: Upload quality report to MinIO
    try:
        from services import minio as minio_service
        from services import postgres as pg_service

        if minio_service._client is not None:
            client = minio_service._client
            bucket = settings.minio_artifacts_bucket
            minio_service.ensure_bucket(client, bucket)

            # Quality report artifact (JSON)
            qr_data = {
                "overall_score": quality.get("overall_score", 0),
                "avg_completeness_pct": quality.get("avg_completeness_pct", 0),
                "table_count": quality.get("table_count", 0),
                "check_results": quality.get("check_results", {}),
                "issues": quality.get("issues", []),
                "profiles": results.get("profiles", []),
            }
            qr_path, qr_artifact_id = await _upload_artifact_with_pg(
                data_product_id, "quality_report", "quality_report.json",
                json.dumps(qr_data, default=str).encode("utf-8"),
                "application/json",
            )
            if qr_artifact_id:
                artifact_ids["quality_report"] = qr_artifact_id
                await queue.put({
                    "type": "artifact",
                    "data": {
                        "artifact_id": qr_artifact_id,
                        "artifact_type": "data_quality",
                    },
                })

    except Exception as e:
        logger.warning("Failed to upload artifacts to MinIO: %s", e)

    await _emit(queue, "artifacts", STEPS[step_idx]["label"],
                "completed", "Done", 1, 1, step_idx)
    return {"status": "ok", "artifact_ids": artifact_ids}


# ---------------------------------------------------------------------------
# Phase 2: ERD pipeline (after discovery conversation)
# ---------------------------------------------------------------------------


def _build_fk_input(
    metadata: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build the table list expected by infer_foreign_keys from pipeline data."""
    profile_map: dict[str, dict[str, bool]] = {}
    for p in profiles:
        pk_lookup: dict[str, bool] = {}
        for pc in p.get("columns", []):
            pk_lookup[pc["column"]] = pc.get("is_likely_pk", False)
        profile_map[p["table"]] = pk_lookup

    tables_for_fk: list[dict[str, Any]] = []
    for table in metadata:
        pk_lookup = profile_map.get(table["fqn"], {})
        tables_for_fk.append({
            "name": table["fqn"],
            "columns": [
                {"name": c["name"], "is_pk": pk_lookup.get(c["name"], False)}
                for c in table.get("columns", [])
            ],
        })
    return tables_for_fk


async def _upload_erd_artifact(
    data_product_id: str,
    erd_data: dict[str, Any],
) -> str | None:
    """Upload ERD artifact to MinIO + PostgreSQL, return artifact_id."""
    try:
        erd_path, erd_artifact_id = await _upload_artifact_with_pg(
            data_product_id, "erd", "erd.json",
            json.dumps(erd_data, default=str).encode("utf-8"),
            "application/json",
        )
        return erd_artifact_id
    except Exception as e:
        logger.warning("Failed to upload ERD artifact: %s", e)
        return None


async def run_erd_pipeline(
    data_product_id: str,
    data_description: dict[str, Any] | str,
) -> dict[str, Any]:
    """Phase 2: Build ERD using data description context.

    Loads Phase 1 results from Redis cache, runs enhanced FK inference,
    builds Neo4j graph, and saves ERD artifact.
    """
    from services import redis as redis_service

    settings = get_settings()
    client = await redis_service.get_client(settings.redis_url)
    cache_key = f"{CACHE_KEY_PREFIX}:{data_product_id}"
    cached = await redis_service.get_json(client, cache_key)
    if not cached:
        raise ValueError("Phase 1 results not found in cache — run discovery pipeline first")

    results = cached
    metadata = results.get("metadata", [])
    profiles = results.get("profiles", [])

    # Enhanced FK inference using data description context
    from agents.discovery import infer_foreign_keys_enhanced

    fk_input = _build_fk_input(metadata, profiles)
    relationships = infer_foreign_keys_enhanced(fk_input, data_description)
    results["relationships"] = relationships

    # Build ERD in Neo4j (reuse existing _step_erd with a dummy queue)
    dummy_queue: asyncio.Queue[dict | None] = asyncio.Queue()
    erd_result = await _step_erd(dummy_queue, data_product_id, results)

    # Save ERD artifact
    erd_data = _build_erd_artifact(results)
    erd_artifact_id = await _upload_erd_artifact(data_product_id, erd_data)

    # Update cache with relationships for downstream use
    results["_cached_at"] = time.time()
    try:
        await redis_service.set_json(client, cache_key, results, ttl=CACHE_TTL)
    except Exception as e:
        logger.warning("Failed to update cache with ERD results: %s", e)

    return {
        "relationships": relationships,
        "erd_status": erd_result,
        "erd_artifact_id": erd_artifact_id,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_erd_artifact(results: dict[str, Any]) -> dict[str, Any]:
    """Build the ERD JSON artifact from pipeline results."""
    metadata = results.get("metadata", [])
    classifications = results.get("classifications", {})
    relationships = results.get("relationships", [])
    profiles = results.get("profiles", [])

    profile_map: dict[str, list[dict[str, Any]]] = {}
    for p in profiles:
        if "columns" in p:
            profile_map[p["table"]] = p.get("columns", [])

    nodes = []
    for table in metadata:
        fqn = table["fqn"]
        profile_cols = profile_map.get(fqn, [])
        cols = []
        for col in table.get("columns", []):
            is_pk = False
            for pc in profile_cols:
                if pc.get("column") == col["name"]:
                    is_pk = pc.get("is_likely_pk", False)
                    break
            cols.append({
                "name": col["name"],
                "data_type": col.get("data_type", "VARCHAR"),
                "nullable": col.get("nullable", True),
                "is_pk": is_pk,
            })
        nodes.append({
            "table_fqn": fqn,
            "classification": classifications.get(fqn, "UNKNOWN"),
            "row_count": table.get("row_count"),
            "columns": cols,
        })

    edges = []
    for rel in relationships:
        edges.append({
            "source": rel["from_table"],
            "target": rel["to_table"],
            "confidence": rel.get("confidence", 0.0),
            "cardinality": rel.get("cardinality", "many_to_one"),
            "source_column": rel.get("from_column", ""),
            "target_column": rel.get("to_column", ""),
        })

    return {"nodes": nodes, "edges": edges}


async def _upload_artifact_with_pg(
    data_product_id: str,
    artifact_type: str,
    filename: str,
    content_bytes: bytes,
    content_type: str,
) -> tuple[str, str | None]:
    """Upload artifact to MinIO and persist metadata to PostgreSQL.

    Returns (minio_path, artifact_id) or (path, None) on PG failure.
    """
    from services import minio as minio_service
    from services import postgres as pg_service

    settings = get_settings()
    client = minio_service._client
    bucket = settings.minio_artifacts_bucket
    artifact_id = str(uuid4())
    version = 1

    # Persist to PostgreSQL for versioning
    try:
        if pg_service._pool is not None:
            pool = pg_service._pool
            sql = """
            INSERT INTO artifacts (id, data_product_id, artifact_type, minio_path, filename, file_size_bytes, content_type, created_by)
            VALUES ($1::uuid, $2::uuid, $3::artifact_type, $4, $5, $6, $7, $8)
            RETURNING version
            """
            placeholder_path = f"{data_product_id}/{artifact_type}/{filename}"
            rows = await pg_service.query(
                pool, sql,
                artifact_id, data_product_id, artifact_type,
                placeholder_path, filename, len(content_bytes), content_type, "pipeline",
            )
            if rows:
                version = rows[0]["version"] if "version" in rows[0].keys() else 1
            final_path = f"{data_product_id}/{artifact_type}/v{version}/{filename}"
            update_sql = "UPDATE artifacts SET minio_path = $1 WHERE id = $2::uuid"
            await pg_service.execute(pool, update_sql, final_path, artifact_id)
    except Exception as e:
        logger.warning("Failed to persist artifact metadata to PostgreSQL: %s", e)
        artifact_id = str(uuid4())

    final_path = f"{data_product_id}/{artifact_type}/v{version}/{filename}"

    # Upload to MinIO
    if client is not None:
        minio_service.ensure_bucket(client, bucket)
        minio_service.upload_file(client, bucket, final_path, content_bytes, content_type=content_type)

    return final_path, artifact_id
