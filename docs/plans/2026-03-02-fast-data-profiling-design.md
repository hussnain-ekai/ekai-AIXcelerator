# Fast Incremental Data Profiling for ekaiX

**Date:** 2026-03-02
**Status:** Design
**Source:** ekai Python Profiler (`ekai-servives/EkaiPythonProfiler-main/`)

## Problem

ekaiX Discovery profiling is slow and wasteful:

1. **One-column-at-a-time stats** — current `_step_profiling` runs `COUNT(col)` + `APPROX_COUNT_DISTINCT(col)` per column in a single batch, but misses value patterns (min/max, avg_length, numeric_ratio), HLL sketches, and data type intelligence.
2. **No checkpointing** — if the user adds or removes tables from a data product, the entire profiling re-runs. No pause/resume.
3. **Limited sampling** — fixed 1M-row BERNOULLI sample regardless of table size. No adaptive sizing.
4. **Shallow column analysis** — no value patterns, no HLL cardinality sketches, no column summaries.

The ekai parent product solved all of these. This PRD documents those techniques and how to bring them into ekaiX.

## What ekai Does (Reference Implementation)

### Architecture

```
SnowflakeProfiler (profiling/profilers/snowflake_profilers.py)
  extends WarehouseAnalyzer (profiling/profilers/profiler.py)

Entry: analyze_schema(database, schema, tables)
  -> get_table_metadata()        # batch INFORMATION_SCHEMA query for all tables
  -> for each table:
       analyze_table()
         -> get_sample_percentage_by_rows()  # adaptive sampling
         -> get_columns_stats()              # single-scan CTE for all column stats
         -> calculate_value_patterns()       # single-scan CTE for value patterns
         -> analyze_column() per column      # build ColumnAnalysis from stats
       checkpoint.mark_complete(table)       # persist immediately
```

### Technique 1: Single-Scan CTE Column Stats

Instead of running individual COUNT/DISTINCT queries per column, ekai generates a single dynamic SQL query that scans the table once and computes all column statistics simultaneously.

**How it works:**

1. Query `INFORMATION_SCHEMA.COLUMNS` to get all column names and data types
2. Use that result set in a CTE with `LISTAGG` to dynamically build aggregate expressions:
   - `HLL_ACCUMULATE("col")` for cardinality (not `APPROX_COUNT_DISTINCT` — HLL gives exportable sketches)
   - `COUNT_IF("col" IS NULL)` for null count
3. The CTE outputs a single SQL string that, when executed, does ONE scan of the table and returns all stats via `UNION ALL`

**Source:** `snowflake_profilers.py` lines 349-414 (`get_columns_stats`)

**Key SQL pattern:**
```sql
-- Phase 1: Generate the stats query (runs on metadata, not data)
WITH column_info AS (
    SELECT column_name, data_type,
           REGEXP_REPLACE(column_name, '[" .\-]', '_') as col_alias
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE table_name = ? AND table_schema = ?
),
query_parts AS (
    SELECT
        LISTAGG('HLL_ACCUMULATE("' || column_name || '") as hll_' || col_alias
                 || ', COUNT_IF("' || column_name || '" IS NULL) as null_' || col_alias,
                 ', ') as agg_expressions,
        LISTAGG('SELECT ''' || column_name || ''' as column_name, ...',
                 ' UNION ALL ') as union_statements
    FROM column_info
)
SELECT 'WITH single_scan_stats AS (SELECT ' || agg_expressions
       || ' FROM db.schema.table SAMPLE) ' || union_statements
FROM query_parts;

-- Phase 2: Execute the generated query (ONE scan of actual data)
-- Returns: column_name, data_type, unique_count, null_count per column
```

### Technique 2: Single-Scan Value Patterns

Same approach, separate CTE, computes value distribution metadata in one scan:

- **numeric_ratio** — `SUM(CASE WHEN TRY_CAST(col AS FLOAT) IS NOT NULL THEN 1 ELSE 0 END) / COUNT(col)` for string columns; 1.0 for known numeric types
- **avg_length** — `AVG(LENGTH(CAST(col AS VARCHAR)))`
- **min_value / max_value** — `MIN(col)` / `MAX(col)` for numeric types
- **Type-aware handling** — numerics, temporals, booleans, complex (VARIANT/ARRAY/OBJECT), and strings each get appropriate expressions

**Source:** `snowflake_profilers.py` lines 559-727 (`calculate_value_patterns`)

### Technique 3: HLL Sketch Export

After computing patterns, ekai runs one additional scan to export HyperLogLog sketches:

```sql
SELECT HLL_EXPORT(HLL_ACCUMULATE("col1")) AS col1_hll,
       HLL_EXPORT(HLL_ACCUMULATE("col2")) AS col2_hll, ...
FROM db.schema.table SAMPLE
```

These JSON sketches enable:
- Cardinality estimation without re-scanning
- Cross-table join cardinality prediction
- Column intersection analysis (for FK detection)

**Source:** `snowflake_profilers.py` lines 696-721

### Technique 4: Adaptive Row-Based Sampling

ekai sizes its samples based on actual row count, not fixed thresholds:

```python
def get_sample_percentage_by_rows(total_rows, target_sample_size=5000):
    MIN_ROWS_FOR_SAMPLING = 1_000_000
    if total_rows < MIN_ROWS_FOR_SAMPLING:
        return (100.0, False)  # full scan
    percentage = (target_sample_size / total_rows) * 100
    return (max(0.001, min(percentage, 100.0)), True)
```

- **< 1M rows:** full scan, no sampling
- **1M+ rows:** target 5,000 rows via `SAMPLE SYSTEM(N%)`
- **Views:** `SAMPLE ROW(N%)` (SYSTEM sampling unavailable on views)

There's also a size-based fallback:
- < 10 GB: 100%
- 10-100 GB: 1%
- 100 GB - 1 TB: 0.01%
- > 1 TB: 0.001%

**Source:** `profiler.py` lines 173-224

### Technique 5: Checkpointing (Pause/Resume/Incremental)

The most critical feature for ekaiX UX — users frequently add/remove tables.

**Checkpoint model:**
```python
class Checkpoint:
    job_id: str
    database: str
    schema: str
    current_table: str | None       # table being profiled right now
    processed_tables: list[str]     # already done
    requested_tables: list[str]     # what user asked for
    results: dict[str, dict]        # profile data per completed table
    status: "in_progress" | "completed" | "paused"
```

**Key operations:**
- `get_pending_tables()` — returns `requested - processed` (only profile what's new)
- `mark_complete(table, result)` — saves result immediately, adds to processed
- `reconcile_tables(new_tables)` — when user changes table selection: updates requested list, returns only the delta to process, preserves existing results for tables still in the list
- `get_requested_results()` — filters stored results to only include currently-requested tables (auto-excludes removed tables without deleting data)

**Persistence:** ekai uses S3 (with MinIO-compatible interface). In ekaiX, store in **PostgreSQL** — a new `profile_checkpoints` table (see schema below).

**Source:** `checkpointer/models.py`, `checkpointer/checkpointer.py`

### Technique 6: Async Query Execution

All Snowflake queries run via `asyncio.to_thread()` wrapping synchronous cursor operations:

```python
async def _execute_query_async(self, query: str):
    return await asyncio.to_thread(self._execute_query_sync, query)
```

Snowflake's `execute_async` + polling loop enables:
- Non-blocking query execution
- Mid-query cancellation via `SYSTEM$CANCEL_QUERY(query_id)`
- OAuth token auto-refresh between queries (for long-running jobs)

**Source:** `snowflake_profilers.py` lines 157-197

### Technique 7: Batch Metadata Fetch

Instead of querying metadata per-table, ekai fetches all table metadata in one shot:

```sql
SELECT TABLE_NAME, TABLE_TYPE, ROW_COUNT, BYTES/1024/1024 as SIZE_MB,
       CREATED, LAST_ALTERED, COMMENT
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = ? AND TABLE_NAME IN (...)
```

Then iterates over the result to drive per-table profiling decisions.

**Source:** `snowflake_profilers.py` lines 199-253

## Data Model

### Profile Output (per column)

Matching ekai's `ColumnAnalysis` model:

| Field | Type | Description |
|-------|------|-------------|
| column_name | str | Column identifier |
| data_type | str | Snowflake data type |
| length_of_column | int | Non-null row count |
| unique_count | int | Distinct values (HLL-estimated) |
| null_count | int | NULL count |
| is_unique | bool | All non-null values distinct (with 2% error margin) |
| value_pattern.numeric_ratio | float | Proportion of values castable to number (0.0-1.0) |
| value_pattern.avg_length | float | Average string length |
| value_pattern.min_value | float? | Min numeric value |
| value_pattern.max_value | float? | Max numeric value |
| value_pattern.hll_sketch | dict? | Exportable HyperLogLog sketch (JSON) |
| analysis_sample_size | int | Rows used for analysis |

### Profile Output (per table)

Matching ekai's `TableContext` model:

| Field | Type | Description |
|-------|------|-------------|
| table_name | str | Table identifier |
| description | str | Table comment from Snowflake |
| row_count | int | Total rows |
| columns_analysis | dict | Column name -> ColumnAnalysis |
| metadata.data_volume | str | Size in MB |
| metadata.last_modified | str | Last altered timestamp |

### PostgreSQL Checkpoint Table

New migration `scripts/migrate-010-profile-checkpoints.sql`:

```sql
CREATE TABLE IF NOT EXISTS profile_checkpoints (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    data_product_id UUID NOT NULL REFERENCES data_products(id) ON DELETE CASCADE,
    database_name   TEXT NOT NULL,
    schema_name     TEXT NOT NULL,
    current_table   TEXT,
    processed_tables TEXT[] NOT NULL DEFAULT '{}',
    requested_tables TEXT[] NOT NULL DEFAULT '{}',
    results         JSONB NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'in_progress'
                    CHECK (status IN ('in_progress', 'completed', 'paused')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(data_product_id)
);

CREATE INDEX IF NOT EXISTS idx_profile_checkpoints_dp
    ON profile_checkpoints(data_product_id);
```

## Integration into ekaiX

### Where It Fits

The new profiler replaces `_step_profiling()` in `ai-service/services/discovery_pipeline.py` (lines 308-542).

```
Current:  _step_profiling() -> sequential per-table COUNT/DISTINCT queries
                            -> no checkpointing
                            -> no value patterns

New:      _step_profiling() -> load/create checkpoint from PostgreSQL
                            -> get_pending_tables() (only new/changed)
                            -> batch metadata fetch (one INFORMATION_SCHEMA query)
                            -> for each pending table:
                                 single-scan CTE stats
                                 single-scan CTE value patterns
                                 HLL sketch export
                                 checkpoint.mark_complete()
                            -> save checkpoint to PostgreSQL
```

### Incremental Behavior

When user adds/removes tables from a data product:

1. **Re-run Discovery** button is clicked
2. Pipeline loads existing checkpoint for this data_product_id
3. `checkpoint.reconcile_tables(new_table_list)` computes the delta:
   - New tables: added to pending
   - Removed tables: excluded from `get_requested_results()` (data preserved in case they re-add)
   - Already profiled: skipped
4. Only pending tables are profiled
5. Checkpoint updated with new results

### Pause/Resume

- User navigates away or closes browser mid-profiling → checkpoint persists in PostgreSQL with `status = 'paused'`
- User returns → pipeline loads checkpoint, resumes from `get_pending_tables()`
- Explicit cancel → `request_cancel()` sends `SYSTEM$CANCEL_QUERY` to Snowflake, saves checkpoint as paused

### Query Count Comparison

For a 17-table schema:

| Step | Current ekaiX | New (ekai technique) |
|------|---------------|---------------------|
| Metadata fetch | 17 queries (1 per table) | 1 query (batch) |
| Column list | 17 queries (SHOW COLUMNS) | 17 queries (INFORMATION_SCHEMA.COLUMNS) |
| Stats (unique, null) | 17 queries (batch per table) | 17 queries (single-scan CTE per table) |
| Value patterns | 0 (not computed) | 0 additional (included in stats CTE) |
| HLL export | 0 (not computed) | 17 queries (optional, one per table) |
| Composite PK | up to 17 * N_combos | same (unchanged) |
| **Total data scans** | **17** | **17 (or 34 with HLL)** |
| **Total metadata queries** | **34** | **18** |
| **Stats per column** | 3 (null%, distinct, type) | 8 (+ min, max, avg_len, numeric_ratio, HLL) |

The win is **not fewer data scans** — it's **richer output per scan** and **incremental re-runs via checkpointing** (0 scans for unchanged tables).

## What We Keep From Current ekaiX

These existing features are retained and not replaced:

- **Composite PK detection** (ekai parent doesn't have this — ekaiX advantage)
- **Semantic PK filtering** (excluding "description", "comment" etc. columns)
- **Data maturity classification** (bronze/silver/gold heuristics)
- **Health score computation** (0-100 scoring)
- **Duplicate rate detection** via `HASH(*)`
- **FK detection** pipeline step (runs after profiling)

## Files to Modify

| File | Change |
|------|--------|
| `ai-service/services/discovery_pipeline.py` | Replace `_step_profiling()` with checkpoint-aware, single-scan CTE profiler |
| `ai-service/tools/snowflake_tools.py` | Add new profiling query builders (CTE generation functions) |
| `ai-service/models/schemas.py` | Add `ColumnAnalysis`, `ValuePattern`, `TableContext` Pydantic models (matching ekai) |
| `scripts/migrate-010-profile-checkpoints.sql` | New PostgreSQL table for checkpoint persistence |
| `backend/src/routes/` | Endpoint for pause/resume/checkpoint status (optional, can be agent-driven) |

## Success Criteria

1. **Incremental profiling works** — adding 1 table to an existing 17-table product re-profiles only that 1 table
2. **Pause/resume works** — closing browser mid-profile and returning picks up where it left off
3. **Value patterns populated** — every column has numeric_ratio, avg_length, min/max where applicable
4. **HLL sketches available** — downstream agents can estimate join cardinality without re-scanning
5. **No regression** — composite PK detection, health scores, and maturity classification still work
6. **Performance** — 17-table schema profiles in < 3 minutes (current is ~2 minutes but with half the stats)
