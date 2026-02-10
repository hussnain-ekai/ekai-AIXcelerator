# Data Maturity Classification & Transformation Agent

**Date:** 2026-02-11
**Status:** Concept / Future Enhancement
**Author:** ekaiX Architecture
**Depends on:** Phases 1-7 (complete), Discovery pipeline, Deep Agents orchestrator

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Vision](#2-vision)
3. [Data Maturity Classification](#3-data-maturity-classification)
4. [Transformation Agent Architecture](#4-transformation-agent-architecture)
5. [LangGraph State Machine](#5-langgraph-state-machine)
6. [Tool Inventory](#6-tool-inventory)
7. [Snowflake Dynamic Tables as the Transformation Primitive](#7-snowflake-dynamic-tables-as-the-transformation-primitive)
8. [Deep Agents Orchestration Flow](#8-deep-agents-orchestration-flow)
9. [Transformation Patterns Library](#9-transformation-patterns-library)
10. [User Experience](#10-user-experience)
11. [Multi-Platform Extensibility](#11-multi-platform-extensibility)
12. [Implementation Phases](#12-implementation-phases)
13. [Risk & Mitigations](#13-risk--mitigations)

---

## 1. Problem Statement

ekaiX currently assumes source data is "gold layer" — clean, typed, denormalized, and ready for semantic modeling. The PRD explicitly scopes it to "gold layer only, no data transformations."

In practice, enterprise customers have data at varying maturity levels:

| Layer | Characteristics | ekaiX Today |
|-------|----------------|-------------|
| **Gold** | Typed columns, business naming, PKs, clean joins | Works perfectly |
| **Silver** | Typed but may have duplicates, inconsistent naming, some nulls | Works with caveats (TRY_CAST, null coalescing) |
| **Bronze** | VARCHAR-everything, nested JSON, no PKs, raw system names, duplicates | Cannot build meaningful semantic model |

When users connect bronze or silver data, the generation agent produces YAML with excessive casting expressions, broken joins, and unreliable metrics. The result is a semantic model that produces wrong answers — worse than no model at all.

**The enhancement**: Make ekaiX intelligent enough to classify source data maturity and automatically create a transformation layer when needed, keeping everything inside Snowflake with zero external tooling.

---

## 2. Vision

```
User connects any data (bronze/silver/gold)
            ↓
Discovery agent profiles + classifies maturity
            ↓
    ┌───────┴───────┐
    │               │
  Gold            Bronze/Silver
    │               │
    ↓               ↓
Requirements    Transformation Agent
    ↓               │ Creates Dynamic Tables
    ↓               │ Validates output quality
    ↓               ↓
    ↓           Virtual Silver/Gold Layer
    ↓               │
    └───────┬───────┘
            ↓
     Requirements → Generation → Validation → Publishing
```

The user sees a single conversational flow. The transformation happens transparently — the agent explains what it found, what it's fixing, and asks for confirmation on ambiguous decisions (e.g., "Column `amt` appears to be USD currency — should I cast it to NUMERIC(12,2)?").

---

## 3. Data Maturity Classification

### 3.1 Classification Signals

During discovery profiling (which already collects column stats), compute these additional signals per table:

| Signal | Metric | Bronze | Silver | Gold |
|--------|--------|--------|--------|------|
| **Type consistency** | % of columns that are VARCHAR vs typed | >60% VARCHAR | 20-60% VARCHAR | <20% VARCHAR |
| **Null density** | Average null % across columns | >30% | 10-30% | <10% |
| **Duplicate rate** | % of rows that are exact duplicates | >5% | 1-5% | <1% |
| **Naming convention** | % of columns matching business patterns (snake_case, no prefixes like `col_`, `raw_`) | <40% match | 40-70% match | >70% match |
| **PK detectability** | Whether any column/combo has >98% uniqueness | No candidates | Weak candidates | Strong PK found |
| **Nested structures** | Columns with VARIANT/OBJECT/ARRAY types or JSON strings | >3 columns | 1-3 columns | 0 columns |

### 3.2 Composite Score

```python
def classify_table(profile: TableProfile) -> DataMaturity:
    score = 0
    score += (1 - profile.varchar_ratio) * 25        # Type consistency (0-25)
    score += (1 - profile.avg_null_pct) * 20          # Null density (0-20)
    score += (1 - profile.duplicate_rate) * 15        # Duplicate rate (0-15)
    score += profile.naming_convention_score * 15     # Naming (0-15)
    score += profile.pk_confidence * 15               # PK detectability (0-15)
    score += (1 - min(profile.nested_col_count / 5, 1.0)) * 10  # Nested (0-10)

    if score >= 80:
        return DataMaturity.GOLD
    elif score >= 50:
        return DataMaturity.SILVER
    else:
        return DataMaturity.BRONZE
```

### 3.3 Classification Output

Stored in Redis cache alongside existing profiling data. Also communicated to the user conversationally:

> "I've analyzed your three tables. The sensor readings table is well-structured (Gold quality) and ready for modeling directly. However, the maintenance log has several text columns storing numeric values and a 12% duplicate rate — I'll need to clean that up first. The raw events table has nested JSON and no clear primary key — that will need the most preparation."

---

## 4. Transformation Agent Architecture

### 4.1 Agent Identity

A new 7th subagent in the Deep Agents orchestrator:

```python
SUBAGENTS = {
    "discovery-agent":       { ... },
    "requirements-agent":    { ... },
    "transformation-agent":  {                          # NEW
        "system_prompt": TRANSFORMATION_PROMPT,
        "tools": [
            profile_source_table,
            generate_dynamic_table_ddl,
            execute_transformation_ddl,
            validate_transformation,
            register_transformed_layer,
            execute_rcr_query,          # For ad-hoc inspection
        ],
    },
    "generation-agent":      { ... },
    "validation-agent":      { ... },
    "publishing-agent":      { ... },
    "explorer-agent":        { ... },
}
```

### 4.2 Agent Prompt (Conceptual)

```
You are the Data Transformation Agent for ekaiX. Your job is to prepare
source data for semantic modeling by creating Snowflake Dynamic Tables.

CONTEXT: The discovery agent classified these tables:
{table_classifications}

YOUR TASK: For each table classified as BRONZE or SILVER, create a
Dynamic Table in {target_schema} that:
1. Casts VARCHAR columns to appropriate types (NUMERIC, DATE, TIMESTAMP, BOOLEAN)
2. Deduplicates rows using QUALIFY ROW_NUMBER()
3. Flattens nested JSON/VARIANT columns into typed scalar columns
4. Synthesizes a primary key if none exists (hash-based or sequence)
5. Renames columns to business-friendly snake_case names
6. Handles nulls with COALESCE defaults appropriate to the data type

RULES:
- Use CREATE OR REPLACE DYNAMIC TABLE with TARGET_LAG = '1 hour'
- Always validate output: row count, null reduction, type conformance
- Ask the user when transformations are ambiguous (currency precision,
  date format interpretation, business meaning of coded values)
- Never drop columns — transform or pass through
- Create one Dynamic Table per source table
- Register all created tables with register_transformed_layer

OUTPUT: After all tables pass validation, summarize what you created
and hand back to the orchestrator.
```

---

## 5. LangGraph State Machine

### 5.1 State Schema

```python
from typing import TypedDict, Literal

class TableTransformPlan(TypedDict):
    source_fqn: str                    # e.g., RAW_DB.BRONZE.EVENTS
    target_fqn: str                    # e.g., RAW_DB.SILVER.EVENTS_CLEAN
    maturity: Literal["bronze", "silver"]
    issues: list[str]                  # ["varchar_numerics", "duplicates", "nested_json"]
    ddl: str | None                    # Generated CREATE DYNAMIC TABLE SQL
    status: Literal["pending", "transforming", "validating", "passed", "failed"]
    validation_result: dict | None     # Row counts, type checks, quality score
    retry_count: int                   # Max 3

class TransformationState(TypedDict):
    tables: list[TableTransformPlan]
    iteration: int                     # Global iteration counter
    ready_tables: list[str]            # FQNs that passed validation
    user_decisions: list[str]          # Captured user confirmations
    messages: list                     # LangGraph message history
```

### 5.2 Graph Topology

```
                    ┌──────────────┐
                    │  START       │
                    │  (classify)  │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  plan_transforms │
                    │  (per table)    │
                    └──────┬───────┘
                           │
              ┌────────────▼────────────┐
              │  For each table:        │
              │  ┌─────────────────┐    │
              │  │ generate_ddl    │    │
              │  └────────┬────────┘    │
              │           │             │
              │  ┌────────▼────────┐    │
              │  │ execute_ddl     │    │
              │  └────────┬────────┘    │
              │           │             │
              │  ┌────────▼────────┐    │
              │  │ validate_output │    │
              │  └────────┬────────┘    │
              │           │             │
              │     pass? ▼ fail?       │
              │    ┌──────┴──────┐      │
              │    │ done  │ retry│     │
              │    │       │ (≤3) │     │
              │    └───────┘──┘──┘      │
              └────────────┬────────────┘
                           │
                    ┌──────▼───────┐
                    │  register    │
                    │  (all done)  │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  COMPLETE    │
                    └──────────────┘
```

### 5.3 Deep Agents Integration

The transformation agent runs as a subagent via the existing `task()` delegation:

```python
# Orchestrator calls:
task(
    subagent_type="transformation-agent",
    description="""
    CLASSIFICATION RESULTS:
    - IOT_READINGS_DATA: SILVER (score 62) — 3 VARCHAR columns storing numerics, 2% duplicates
    - MAINTENANCE_EVENTS: SILVER (score 55) — COST_USD and DOWNTIME_MINUTES as VARCHAR,
      8% null rate in TECHNICIAN_ID
    - WATER_SENSORS_MASTER: GOLD (score 88) — no transformation needed

    TARGET SCHEMA: DMTDEMO.SILVER

    Create Dynamic Tables for the two SILVER tables.
    WATER_SENSORS_MASTER can be referenced directly.
    """
)
```

The orchestrator's state tracks which layer the downstream agents should reference:

```python
# After transformation completes, orchestrator updates context:
context["working_tables"] = {
    "IOT_READINGS_DATA": "DMTDEMO.SILVER.IOT_READINGS_CLEAN",
    "MAINTENANCE_EVENTS": "DMTDEMO.SILVER.MAINTENANCE_EVENTS_CLEAN",
    "WATER_SENSORS_MASTER": "DMTDEMO.BRONZE.WATER_SENSORS_MASTER",  # unchanged
}
```

Generation agent then references these FQNs instead of the original source tables.

---

## 6. Tool Inventory

### 6.1 `profile_source_table`

Reuses existing discovery profiling logic. Returns per-column stats including the new classification signals.

```python
@tool
async def profile_source_table(table_fqn: str) -> str:
    """Profile a source table for transformation planning.
    Returns: column types, null %, distinct counts, sample values,
    duplicate rate, VARCHAR-stored-numeric detection, nested JSON detection.
    """
```

### 6.2 `generate_dynamic_table_ddl`

LLM generates the SQL. The tool validates syntax before returning.

```python
@tool
async def generate_dynamic_table_ddl(
    source_fqn: str,
    target_fqn: str,
    transformations: str,  # JSON list of column transformations
    target_lag: str = "1 hour",
) -> str:
    """Generate CREATE OR REPLACE DYNAMIC TABLE DDL.
    The transformations parameter describes what to do per column:
    - cast: {column: "COST_USD", from: "VARCHAR", to: "NUMERIC(12,2)"}
    - dedup: {method: "ROW_NUMBER", partition_by: ["EVENT_ID"], order_by: ["TIMESTAMP DESC"]}
    - flatten: {column: "METADATA", extract: ["source", "version", "tags"]}
    - rename: {from: "col_amt", to: "amount_usd"}
    - pk_synthesize: {method: "hash", columns: ["EVENT_ID", "TIMESTAMP"]}
    Returns: The generated DDL string.
    """
```

### 6.3 `execute_transformation_ddl`

Executes the DDL against Snowflake with RCR. Captures success/failure.

```python
@tool
async def execute_transformation_ddl(ddl: str) -> str:
    """Execute a CREATE DYNAMIC TABLE statement against Snowflake.
    Returns: Success message with table name, or error details.
    Uses EXECUTE AS CALLER for RCR compliance.
    """
```

### 6.4 `validate_transformation`

Compares source and target tables to ensure the transformation is correct.

```python
@tool
async def validate_transformation(
    source_fqn: str,
    target_fqn: str,
) -> str:
    """Validate a transformation by comparing source and target.
    Checks:
    - Row count (target should be <= source, within 5% if dedup applied)
    - Column type conformance (no VARCHAR where numeric expected)
    - Null reduction (target should have fewer nulls)
    - PK uniqueness (if PK was synthesized, verify >99.9% unique)
    - Sample value spot-check (5 random rows, compare before/after)
    Returns: Validation report with pass/warn/fail per check.
    """
```

### 6.5 `register_transformed_layer`

Persists the table mapping so downstream agents know which tables to use.

```python
@tool
async def register_transformed_layer(
    data_product_id: str,
    table_mapping: str,  # JSON: {"source_fqn": "target_fqn", ...}
) -> str:
    """Register transformed tables as the working layer for this data product.
    Stores mapping in Redis and PostgreSQL so generation/validation agents
    reference the clean tables instead of raw source.
    Tables not in the mapping are used as-is (already gold quality).
    """
```

---

## 7. Snowflake Dynamic Tables as the Transformation Primitive

### 7.1 Why Dynamic Tables

| Alternative | Pros | Cons |
|-------------|------|------|
| **Views** | Zero compute cost | No materialization, slow on large data |
| **CTAS (static tables)** | Simple | No auto-refresh, stale immediately |
| **Dynamic Tables** | Auto-refresh, declarative SQL, incremental, Snowflake-native | Compute cost for refresh |
| **dbt** | Industry standard | External tooling, not SPCS-compatible, added complexity |
| **Snowpark** | Flexible | Overkill for SQL transforms, harder to maintain |

Dynamic Tables are the right choice because:
1. **Declarative**: `CREATE DYNAMIC TABLE AS SELECT ...` — the LLM is already excellent at writing SELECT statements
2. **Auto-refresh**: `TARGET_LAG = '1 hour'` means the silver layer stays fresh automatically
3. **Incremental**: Snowflake handles change tracking internally — no custom incremental logic needed
4. **Native**: No external dependencies, works in SPCS, compatible with all Snowflake features
5. **Auditable**: `SHOW DYNAMIC TABLES` + `INFORMATION_SCHEMA.DYNAMIC_TABLE_REFRESH_HISTORY` for lineage

### 7.2 DDL Template

```sql
CREATE OR REPLACE DYNAMIC TABLE {target_schema}.{target_table}
    TARGET_LAG = '1 hour'
    WAREHOUSE = {warehouse}
AS
SELECT
    -- Type casting
    TRY_TO_DECIMAL(COST_USD, 12, 2) AS cost_usd,
    TRY_TO_NUMBER(DOWNTIME_MINUTES) AS downtime_minutes,

    -- Deduplication
    -- (handled via QUALIFY below)

    -- JSON flattening
    METADATA:source::VARCHAR AS data_source,
    METADATA:version::NUMBER AS schema_version,

    -- Null handling
    COALESCE(TECHNICIAN_ID, 'UNASSIGNED') AS technician_id,

    -- PK synthesis (when no natural key exists)
    SHA2(CONCAT_WS('|', EVENT_ID, TIMESTAMP::VARCHAR)) AS row_hash_pk,

    -- Pass-through columns
    EVENT_TYPE,
    STATUS,
    TIMESTAMP
FROM {source_schema}.{source_table}
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY EVENT_ID
    ORDER BY TIMESTAMP DESC
) = 1;
```

### 7.3 Target Schema Strategy

Transformations create tables in a parallel schema:

```
Source:   DMTDEMO.BRONZE.MAINTENANCE_EVENTS
Target:   DMTDEMO.SILVER_EKAIX.MAINTENANCE_EVENTS_CLEAN
```

The `SILVER_EKAIX` schema is created automatically by the agent. The `_EKAIX` suffix ensures no collision with existing silver layers the customer may already have.

---

## 8. Deep Agents Orchestration Flow

### 8.1 Updated Phase Flow

```
Phase 1: Discovery (existing)
    → Profile tables
    → Classify maturity (NEW)
    → Build ERD + Data Description + Quality Report
    → Conversational validation

Phase 1.5: Transformation (NEW — only if bronze/silver detected)
    → Transformation agent creates Dynamic Tables
    → Validates output quality
    → Registers working layer

Phase 2: Requirements (existing, unchanged)
    → Questions → BRD
    → References working layer tables (may be originals or Dynamic Tables)

Phase 3: Generation (existing, minor change)
    → YAML references working layer FQNs (from register_transformed_layer)
    → No other changes — generation doesn't care whether the table is original or Dynamic

Phase 4: Validation (existing, unchanged)
    → Validates against working layer tables

Phase 5: Publishing (existing, minor change)
    → Semantic view references Dynamic Tables
    → Lineage metadata includes source → Dynamic Table → Semantic View chain
```

### 8.2 Orchestrator Prompt Additions

```
RULE 0.5 — DATA MATURITY CLASSIFICATION
After discovery completes, check the classification results in the context.
- If ALL tables are GOLD: skip transformation, proceed to requirements (RULE 1)
- If ANY table is BRONZE or SILVER: DELEGATE to transformation-agent with
  classification details. After transformation completes, proceed to requirements.

RULE 0.6 — TRANSFORMATION CONTEXT PROPAGATION
After transformation-agent completes, the working layer mapping is in context.
When delegating to requirements-agent, generation-agent, or validation-agent,
include the working layer mapping in the task description so they reference
the correct (possibly transformed) table FQNs.
```

### 8.3 Phase Stepper Update

The frontend phase stepper gains a conditional step:

```
Discovery → [Transformation] → Requirements → Generation → Validation → Publishing
              ↑ only shown if transformation was needed
```

When all tables are gold, the stepper shows the original 5 phases. When transformation runs, it appears as a 6th phase between Discovery and Requirements.

---

## 9. Transformation Patterns Library

The transformation agent uses a library of proven SQL patterns. These are deterministic templates the LLM fills in — similar to the existing YAML assembler's FACT_TEMPLATES and METRIC_TEMPLATES.

### 9.1 Type Casting

```sql
-- VARCHAR to NUMERIC (safe)
TRY_TO_DECIMAL({col}, {precision}, {scale}) AS {col_clean}

-- VARCHAR to DATE (with format detection)
TRY_TO_DATE({col}, '{detected_format}') AS {col_clean}

-- VARCHAR to TIMESTAMP
TRY_TO_TIMESTAMP_NTZ({col}, '{detected_format}') AS {col_clean}

-- VARCHAR to BOOLEAN
CASE
    WHEN UPPER({col}) IN ('TRUE', 'YES', '1', 'Y', 'T') THEN TRUE
    WHEN UPPER({col}) IN ('FALSE', 'NO', '0', 'N', 'F') THEN FALSE
    ELSE NULL
END AS {col_clean}
```

### 9.2 Deduplication

```sql
-- Exact row dedup (keep latest)
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY {pk_columns}
    ORDER BY {timestamp_col} DESC
) = 1

-- Fuzzy dedup (when no clear PK — use hash of all columns)
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY SHA2(CONCAT_WS('|', {all_columns}))
    ORDER BY 1
) = 1
```

### 9.3 JSON Flattening

```sql
-- Top-level key extraction
{variant_col}:{key}::{target_type} AS {key_name}

-- Array flattening (creates additional rows)
-- Only used when explicitly confirmed by user
SELECT t.*, f.VALUE:{key}::{type} AS {extracted_name}
FROM {table} t, LATERAL FLATTEN(input => t.{array_col}) f

-- Nested object flattening
{variant_col}:{level1}:{level2}::{target_type} AS {name}
```

### 9.4 PK Synthesis

```sql
-- Hash-based composite key (when no natural key exists)
SHA2(CONCAT_WS('|',
    COALESCE({col1}::VARCHAR, ''),
    COALESCE({col2}::VARCHAR, ''),
    COALESCE({col3}::VARCHAR, '')
)) AS _row_id

-- Sequence-based (for truly keyless tables)
-- Note: Dynamic Tables don't support sequences directly
-- Use ROW_NUMBER() over deterministic ordering instead
ROW_NUMBER() OVER (ORDER BY {deterministic_columns}) AS _row_id
```

### 9.5 Column Renaming

```sql
-- System prefix removal
{col} AS {col_without_prefix}
-- e.g., raw_customer_name → customer_name, col_amt → amount

-- Abbreviation expansion (LLM decides)
-- e.g., cust_nm → customer_name, txn_dt → transaction_date
```

---

## 10. User Experience

### 10.1 Conversational Flow (Bronze Data Example)

```
ekaiX: I've analyzed your three tables and assessed their readiness
       for semantic modeling.

       - orders_raw: Needs significant preparation (Bronze quality)
         * 8 of 12 columns are untyped text
         * 15% duplicate rows detected
         * Nested JSON in the metadata column
         * No clear primary key

       - customers: Minor cleanup needed (Silver quality)
         * 3 text columns storing dates and amounts
         * 2% null rate in email column

       - products: Ready for modeling (Gold quality)

       I'll create optimized views for the first two tables.
       Before I proceed, a couple of questions:

       1. The orders_raw.amt column contains values like "$1,234.56" —
          should I treat this as USD with 2 decimal places?
       2. The metadata column has nested JSON with 6 keys. Should I
          extract all of them, or are there specific fields you need?

User:  1. Yes, USD with 2 decimals. Strip the $ and commas.
       2. Just extract source, channel, and campaign_id.

ekaiX: Creating optimized data layers...

       [Transformation progress indicator — similar to discovery pipeline]
       Step 1/4: Creating orders_clean (type casting, dedup, JSON extraction)
       Step 2/4: Validating orders_clean (2.1M rows → 1.8M after dedup, all types correct)
       Step 3/4: Creating customers_clean (date/amount casting)
       Step 4/4: Validating customers_clean (500K rows, 0 type failures)

       Done. I've prepared your data:
       - orders_clean: 1.8M rows (removed 15% duplicates),
         12 typed columns including 3 extracted from JSON
       - customers_clean: 500K rows, dates and amounts properly typed
       - products: Used directly (no changes needed)

       Ready to capture your business requirements. What metrics
       and dimensions matter most for your analysis?
```

### 10.2 Artifact: Transformation Report

A new artifact type displayed in the artifact panel:

```
TRANSFORMATION REPORT
─────────────────────

Source: RAW_DB.BRONZE.ORDERS_RAW → RAW_DB.SILVER_EKAIX.ORDERS_CLEAN
Refresh: Every 1 hour (Dynamic Table)

Transformations Applied:
  1. Type casting: 8 columns (VARCHAR → NUMERIC/DATE/TIMESTAMP)
  2. Deduplication: ROW_NUMBER() on ORDER_ID, kept latest by CREATED_AT
  3. JSON extraction: metadata → source, channel, campaign_id
  4. PK synthesis: SHA2(ORDER_ID || CREATED_AT) as _row_id
  5. Null handling: COALESCE on 3 columns

Quality Comparison:
                    Source      Target      Change
  Row count:        2,100,000   1,785,000   -15% (dedup)
  Null rate:        18.3%       2.1%        -88%
  Type conformance: 33%         100%        +200%
  PK uniqueness:    N/A         100%        New
```

---

## 11. Multi-Platform Extensibility

This architecture is designed to be platform-extensible. The transformation patterns are SQL-based, and most modern data platforms support equivalent constructs:

### 11.1 Platform Mapping

| Concept | Snowflake | Databricks | Microsoft Fabric |
|---------|-----------|------------|------------------|
| **Auto-refresh transform** | Dynamic Tables | Delta Live Tables (DLT) | Dataflow Gen2 / Lakehouse shortcuts |
| **DDL syntax** | `CREATE DYNAMIC TABLE AS SELECT` | `CREATE STREAMING TABLE AS SELECT` (DLT) | T-SQL `CREATE TABLE AS SELECT` + Spark |
| **Type casting** | `TRY_TO_DECIMAL()` | `TRY_CAST()` / `CAST()` | `TRY_CAST()` |
| **JSON flattening** | `LATERAL FLATTEN` + `:` notation | `explode()` + `from_json()` | `OPENJSON()` / `explode()` |
| **Deduplication** | `QUALIFY ROW_NUMBER()` | `ROW_NUMBER()` in subquery | `ROW_NUMBER()` in subquery |
| **Incremental** | Built into Dynamic Tables | DLT streaming / `APPLY CHANGES INTO` | Dataflow incremental refresh |

### 11.2 Abstraction Layer

To support multiple platforms, the transformation agent would use a platform abstraction:

```python
class TransformationBackend(Protocol):
    async def create_materialized_transform(self, source: str, target: str, sql: str, refresh: str) -> str: ...
    async def validate_transform(self, source: str, target: str) -> ValidationResult: ...
    async def get_type_cast_function(self, from_type: str, to_type: str) -> str: ...
    async def get_dedup_pattern(self, pk_cols: list[str], order_col: str) -> str: ...
    async def get_flatten_pattern(self, variant_col: str, keys: list[str]) -> str: ...

class SnowflakeTransformBackend(TransformationBackend): ...
class DatabricksTransformBackend(TransformationBackend): ...
class FabricTransformBackend(TransformationBackend): ...
```

The transformation agent's tools call through this abstraction. The prompt patterns stay the same — only the SQL dialect changes.

---

## 12. Implementation Phases

### Phase A: Classification (Low effort, high value)

- Add maturity classification signals to discovery profiling
- Compute composite score per table
- Display classification in discovery analysis message
- Store in Redis cache alongside profiling data
- No transformation yet — just visibility

**Deliverable:** User sees "Your data is Gold/Silver/Bronze quality" after discovery.

### Phase B: Core Transformation Agent (Medium effort)

- New `transformation-agent` subagent with 5 tools
- Orchestrator routing (classify → transform if needed → requirements)
- Dynamic Table DDL generation using pattern library
- Validation tool (source vs target comparison)
- `register_transformed_layer` for downstream context propagation
- Basic patterns: type casting, dedup, null handling

**Deliverable:** Silver-quality tables automatically cleaned via Dynamic Tables.

### Phase C: Advanced Transformations (Medium effort)

- JSON/VARIANT flattening patterns
- PK synthesis (hash-based and ROW_NUMBER)
- Column renaming heuristics
- Transformation Report artifact
- User confirmation flow for ambiguous decisions
- Frontend phase stepper update (conditional Transformation step)

**Deliverable:** Bronze-quality tables fully transformed to modeling-ready state.

### Phase D: Multi-Platform (High effort)

- `TransformationBackend` protocol abstraction
- Databricks DLT backend implementation
- Microsoft Fabric backend implementation
- Platform-specific DDL templates
- Platform detection during workspace setup

**Deliverable:** ekaiX works across Snowflake, Databricks, and Fabric.

---

## 13. Risk & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| LLM generates incorrect transformation SQL | Data corruption in silver layer | Validation tool compares source/target row counts, types, samples. Max 3 retries. User sees diff before proceeding |
| Dynamic Table compute cost surprises | Customer bill shock | Show estimated refresh cost before creating. Default `TARGET_LAG = '1 hour'` is conservative. User can adjust |
| Transformation changes data semantics | Wrong metric results downstream | Never drop columns, only transform. Spot-check 5 random rows. User confirms ambiguous transforms |
| Bronze data too messy for any automated fix | Agent loops indefinitely | Max 3 retries per table. If still failing, inform user: "This table needs manual preparation before I can work with it" |
| Dynamic Tables not available (older Snowflake editions) | Feature doesn't work | Check `SHOW PARAMETERS LIKE 'ENABLE_DYNAMIC_TABLES'` at startup. Fall back to regular views if unavailable |
| Schema permissions | Can't create in target schema | Try creating `SILVER_EKAIX` schema. If permission denied, ask user for a writable schema or fall back to same schema with `_CLEAN` suffix |

---

## Appendix A: Relationship to Existing Features

| Existing Feature | Interaction with Transformation |
|-----------------|-------------------------------|
| **Discovery pipeline** | Extended with classification signals. Profiling data reused by transformation agent |
| **Data Quality Report** | Shows pre-transformation quality. Post-transformation quality shown in Transformation Report artifact |
| **Data Description** | Updated after transformation to reflect the clean layer structure |
| **ERD** | Built on working layer (may include Dynamic Tables as nodes) |
| **BRD** | References working layer table names |
| **YAML Generation** | References working layer FQNs. No other changes needed |
| **YAML Validation** | Validates against working layer. No changes needed |
| **Publishing** | Semantic view references Dynamic Tables. Lineage metadata extended |
| **Post-publish revision** | Can modify transformations (re-run transformation agent) |

## Appendix B: Relationship to Next Features Document

This enhancement complements the features in `2026-02-10-next-features.md`:

1. **sample_values + synonyms** — Transformation agent can inject cleaner sample values post-transform
2. **Cortex Agent MCP** — Testing works identically on transformed data
3. **Quality validation with user Q&A** — More reliable on properly typed/deduped data
4. **ML Functions** — Time-series forecasting requires clean typed timestamps — transformation ensures this
