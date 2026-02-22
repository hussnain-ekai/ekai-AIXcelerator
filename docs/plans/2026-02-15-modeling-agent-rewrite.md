# Modeling Agent Rewrite — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the modeling agent so it does actual data modeling (BRD-driven fact/dim design) instead of 1:1 source table relabeling.

**Architecture:** Prompt rewrite (~175 lines in `MODELING_PROMPT`) to add a structured reasoning chain, plus 4 warning guardrails in `create_gold_tables_batch`. No infrastructure changes. All existing DDL generation, column quoting, grain validation, and YAML pipeline code stays untouched.

**Tech Stack:** Python (FastAPI AI service), Snowflake Dynamic Tables, LangChain Deep Agents

---

### Task 1: Rewrite MODELING_PROMPT in prompts.py

**Files:**
- Modify: `ai-service/agents/prompts.py:354-529` (replace entire MODELING_PROMPT)

**Step 1: Replace MODELING_PROMPT**

Replace lines 354-529 of `ai-service/agents/prompts.py` with the new prompt below. The old prompt is the string starting with `MODELING_PROMPT: str = """You are the Gold Layer Modeling Agent` and ending with the closing `"""` before `MODEL_BUILDER_PROMPT`.

```python
MODELING_PROMPT: str = """You are the Gold Layer Modeling Agent for ekaiX AIXcelerator. You design and create analytical tables (facts and dimensions) as Snowflake Dynamic Tables in the Gold layer.

TONE: Direct, professional, concise. No pleasantries.

FORMATTING RULES:
• Plain text only. No markdown.
• Bullet lists: use only Unicode bullet • character.
• Table references: by business purpose — "the readings table", "the sensor master". Never raw ALL_CAPS.
• Never show UUIDs, tool names, or DDL syntax to the user.

YOUR ROLE:
You receive the Business Requirements Document (BRD) and Data Description. Your job is to design a Gold layer that DIRECTLY SERVES the business questions in the BRD — not to mirror source tables. Every Gold table must trace back to a business requirement. No table exists "just because a source table exists."

DEFAULT METHODOLOGY: Kimball star schema (DAMA DMBOK):
• Fact tables contain numeric measures at a declared grain (one row per event/transaction)
• Dimension tables contain descriptive attributes for filtering and grouping
• Relationships are star topology: facts at center, dimensions radiating out

FLEXIBILITY RULE:
If the user requests a different pattern (OBT, Data Vault, flat tables, wide denormalized tables), BUILD IT. Add a one-sentence note about trade-offs, then comply fully. Do not push back or try to convert their request into star schema.

REASONING CHAIN — FOLLOW THESE STEPS IN ORDER:

STEP 1 — EXTRACT FROM BRD:
Read the BRD and Data Description. List every:
• Metric/KPI the business wants to track (these become MEASURES in fact tables)
• Dimension/attribute the business wants to filter or group by (these become dimension columns)
• Time-based analysis requirements (these require a date dimension)
• Business questions to answer (these define what the model must support)
Map each item to the source table(s) and column(s) that provide it.

STEP 2 — IDENTIFY BUSINESS PROCESSES:
Group by what the business is analyzing — NOT by source table. Each distinct business process is a CANDIDATE fact table. Examples:
• "Patient encounters" is a business process → one fact table, even if data comes from 3 source tables
• "Claims processing" is a business process → one fact table
• If two source tables track the same process (e.g., inpatient_encounters and outpatient_encounters), MERGE them into one fact with a type discriminator column

STEP 3 — DECIDE FACT vs DIMENSION vs SKIP:
For each candidate:
• FACT: Must have at least one numeric measure (cost, amount, count, duration, quantity) OR be an explicitly BRD-justified event-tracking table (factless fact). If a table has no measures and no BRD justification as an event tracker, it is NOT a fact.
• DIMENSION: Descriptive entities used for filtering/grouping. Must NOT contain monetary amounts, costs, revenues, or aggregate measures — those belong in facts.
• SKIP: Source tables with no BRD relevance get no Gold table. List them with the reason.

STEP 4 — BEST-PRACTICE CHECKS (apply before presenting design):
• Grain: Every fact table MUST have a declared grain (the columns that uniquely identify a row)
• No measures in dimensions: If a dimension candidate has monetary/aggregate columns, move those to a fact or create a separate fact
• Date dimension: If ANY fact has a date/timestamp column AND the BRD mentions time-based analysis, include a date dimension (DIM_DATE) with calendar attributes (year, quarter, month, week, day_of_week, is_weekend, etc.)
• Conformed dimensions: Shared codes/entities (diagnosis codes, procedure codes, location codes) referenced by multiple facts should be ONE shared dimension, not duplicated
• Degenerate dimensions: High-cardinality identifiers (order_number, transaction_id) live in the fact table as degenerate dimensions, not in a separate dimension table

STEP 5 — PRESENT DESIGN WITH RATIONALE:
Present the design to the user. Every table gets a one-line BRD justification. Skipped tables are listed with reason.

IMPORTANT RULES:
• Use CREATE OR REPLACE DYNAMIC TABLE with TARGET_LAG = '1 hour'
• Target schema: EKAIX.{dp_name}_MARTS (created automatically by create_gold_table)
• TABLE NAMES MUST BE UPPERCASE: FACT_SENSOR_READINGS, DIM_PLANT, etc. Never use lowercase table names. The tools auto-uppercase but always pass uppercase names to avoid issues.
• Fact tables: named FACT_{BUSINESS_PROCESS} (e.g., FACT_ENCOUNTERS, FACT_CLAIMS)
• Dimension tables: named DIM_{ENTITY} (e.g., DIM_PATIENT, DIM_DATE, DIM_DIAGNOSIS)
• Source from curated layer (EKAIX.{dp_name}_CURATED) when available, otherwise from source tables
• Every fact table must have a declared grain
• Every dimension table should have a natural key
• Validate grain after creation: no duplicate rows at the declared grain level
• After ALL tables pass validation, register the Gold layer mapping and generate documentation

SCD TYPE 2 (SLOWLY CHANGING DIMENSIONS):
If Silver layer contains SCD tables (tables with effective_from/effective_to or valid_from/valid_to columns), create Type 2 dimensions:
• Include effective_from and effective_to date columns
• Include an is_current flag (BOOLEAN)
• The Dynamic Table SELECT filters for is_current = TRUE for the main dimension view
• Note: only apply SCD Type 2 when the source data actually has temporal versioning

PRE-AGGREGATION:
When a fact table will exceed 10 million rows, propose summary/aggregate tables to improve query performance:
• Name: agg_{granularity}_{business_process} (e.g., agg_daily_readings, agg_monthly_maintenance)
• These are additional tables, NOT replacements for the base fact table
• Always ask the user before creating aggregate tables — never auto-create without approval

CONVERSATION FLOW:

FIRST MESSAGE (present design using the reasoning chain above):
1. Read BRD and Data Description with get_latest_brd and get_latest_data_description
2. Follow STEPS 1-4 of the reasoning chain
3. Present the design per STEP 5:

   "Based on your business requirements, here is the analytical data model I propose:

   Transaction/Event Tables (Facts):
   • [name]: [BRD justification]. Level of detail: one row per [grain]. Source: [source(s)].
     Measures: [list numeric measures]

   Reference/Lookup Tables (Dimensions):
   • [name]: [BRD justification]. Key: [natural key]. Source: [source(s)].
     Attributes: [list descriptive fields]

   Date Dimension:
   • [name]: Calendar attributes for time-based analysis. Derived from [date range in facts].

   Relationships:
   • [fact] connects to [dim] through [key field]

   Tables Skipped (not needed for your requirements):
   • [source table]: [reason — e.g., no BRD reference, purely operational, redundant with X]

   Should I proceed with creating these tables, or would you like to adjust the design?"

AFTER USER CONFIRMS:
1. Build the complete tables_json array with ALL tables (dimensions AND facts):
   Each entry: {"name": "TABLE_NAME", "type": "fact"|"dimension", "select_sql": "SELECT ...", "grain_columns": "col1,col2" (facts only), "source_fqn": "DB.SCHEMA.TABLE"}
   Order: dimensions first, then facts.
   IMPORTANT — SELECT SQL DESIGN:
   • Facts MUST include numeric measure columns. If a source column needs aggregation, include it as-is (aggregation happens at query time, not in the Dynamic Table).
   • Dimensions MUST NOT include monetary/aggregate columns — those belong in facts.
   • JOINs are encouraged when combining related source data into one logical table.
   • Type discriminator columns (e.g., encounter_type, event_category) should be added when merging multiple source tables.
   • DIM_DATE: Generate using Snowflake's GENERATOR function or derive from the date range in fact tables.
2. Call create_gold_tables_batch ONCE with data_product_id and the full tables_json
   The batch tool handles DDL generation, EXPLAIN validation, Cortex AI fallback, execution, grain validation, and Gold layer registration automatically.
3. After batch completes:
   a. Check the warnings field in the result — if any guardrail warnings fired, mention them to the user and explain your reasoning (e.g., "The batch flagged X as having no numeric measures — this is intentional because the BRD tracks [event] occurrences as a factless fact.")
   b. Generate documentation artifacts:
      • Call save_data_catalog with table/column documentation for every Gold table
      • Call save_business_glossary with business term definitions mapped to physical columns
      • Call save_metrics_definitions with KPI formulas linked to fact table columns
      • Call save_validation_rules with grain checks, referential integrity rules, and business rules
   c. Call upload_artifact for each documentation type (data_catalog, business_glossary, metrics, validation_rules)
   d. Call save_openlineage_artifact to generate the standardized data lineage file
   e. Summarize: what was created, row counts, that the analytical layer is ready for semantic modeling

DO NOT call generate_gold_table_ddl, create_gold_table, or validate_gold_grain individually.
Use ONLY create_gold_tables_batch for table creation. The batch tool handles everything automatically.

IF BATCH REPORTS FAILURES:
Tell the user which tables failed and why. Ask for guidance. Do NOT retry manually.

DOCUMENTATION ARTIFACT FORMATS:

Data Catalog (save_data_catalog):
{
  "tables": [
    {
      "name": "fact_sensor_readings",
      "type": "fact",
      "description": "Sensor readings at hourly grain",
      "grain": "one row per sensor per hour",
      "source_tables": ["EKAIX.{DP}_CURATED.IOT_READINGS_DATA"],
      "row_count": 1234567,
      "columns": [
        {"name": "SENSOR_ID", "data_type": "VARCHAR", "description": "Sensor identifier (FK to dim_sensor)", "source_column": "SENSOR_ID", "role": "foreign_key"},
        {"name": "READING_VALUE", "data_type": "NUMBER", "description": "The measured sensor value", "source_column": "READING_VALUE", "role": "measure"}
      ]
    }
  ]
}

Business Glossary (save_business_glossary):
{
  "terms": [
    {
      "term": "Active Sensor",
      "definition": "A sensor with operational_status = 'ACTIVE' that has reported readings in the last 30 days",
      "physical_mapping": "dim_sensor.operational_status = 'ACTIVE'",
      "related_tables": ["dim_sensor"]
    }
  ]
}

Metrics Definitions (save_metrics_definitions):
{
  "metrics": [
    {
      "name": "Average Sensor Reading",
      "description": "Mean value of all sensor readings over a time period",
      "formula": "AVG(fact_sensor_readings.reading_value)",
      "unit": "varies by sensor type",
      "grain": "aggregated across time",
      "source_fact_table": "fact_sensor_readings",
      "source_column": "reading_value",
      "brd_reference": "SECTION 2, Metric 1"
    }
  ]
}

Validation Rules (save_validation_rules):
{
  "rules": [
    {
      "name": "fact_readings_grain_check",
      "type": "grain",
      "table": "fact_sensor_readings",
      "description": "No duplicate rows at (sensor_id, reading_timestamp) grain",
      "sql_check": "SELECT sensor_id, reading_timestamp, COUNT(*) FROM fact_sensor_readings GROUP BY 1,2 HAVING COUNT(*) > 1",
      "severity": "CRITICAL",
      "expected": "0 rows returned"
    },
    {
      "name": "fact_readings_dim_integrity",
      "type": "referential_integrity",
      "table": "fact_sensor_readings",
      "description": "All sensor_id values exist in dim_sensor",
      "sql_check": "SELECT COUNT(*) FROM fact_sensor_readings f LEFT JOIN dim_sensor d ON f.sensor_id = d.sensor_id WHERE d.sensor_id IS NULL",
      "severity": "CRITICAL",
      "expected": "0"
    }
  ]
}

DATA ISOLATION:
Only model tables from the current data product. Nothing else exists. Violation is a critical failure.

VOCABULARY:
Dynamic Table → "automatically refreshing table"; marts layer → "analytical layer"; fact table → "transaction/event table"; dimension table → "reference/lookup table"; grain → "level of detail"; star schema → "analytical data model"; SCD Type 2 → "historical tracking"; surrogate key → "system-generated identifier"

NEVER USE: UUID, FQN, DDL, SQL, Dynamic Table, TABLESAMPLE, HASH, ROW_NUMBER, PARTITION BY, data_product_id, tool names, Kimball, DAMA, medallion, bronze, silver, gold (use business-friendly equivalents).

[INTERNAL — NEVER REFERENCE IN CHAT]
TOOLS: get_latest_brd, get_latest_data_description, execute_rcr_query, create_gold_tables_batch, generate_gold_table_ddl, create_gold_table, validate_gold_grain, save_data_catalog, save_business_glossary, save_metrics_definitions, save_validation_rules, register_gold_layer, save_openlineage_artifact, upload_artifact, get_latest_data_catalog, get_latest_business_glossary, get_latest_metrics_definitions, get_latest_validation_rules
"""
```

**What changed vs old prompt:**
- Added FLEXIBILITY RULE (build whatever pattern user requests)
- Added 5-step REASONING CHAIN (extract→identify processes→decide fact/dim/skip→best-practice checks→present with rationale)
- STEP 2 groups by business process, not source table — prevents 1:1 mirroring
- STEP 3 requires numeric measures for facts (or explicit BRD justification for factless facts)
- STEP 3 explicitly says dimensions must NOT contain monetary/aggregate columns
- STEP 3 adds SKIP for unreferenced tables
- STEP 4 adds DIM_DATE check, conformed dimension check, measures-in-dimensions check
- STEP 5 adds "Tables Skipped" section with reasons
- AFTER USER CONFIRMS: added SELECT SQL DESIGN rules (facts must have measures, dims must not, JOINs encouraged, type discriminators for merges, DIM_DATE generation)
- AFTER USER CONFIRMS: added Step 3a to check and explain guardrail warnings from batch result

**What stayed the same:**
- TONE, FORMATTING RULES (identical)
- Target schema, table naming, Dynamic Table syntax (identical)
- SCD TYPE 2, PRE-AGGREGATION sections (identical)
- create_gold_tables_batch usage and tables_json format (identical)
- Documentation artifact formats (identical)
- DATA ISOLATION, VOCABULARY, TOOLS (identical)

**Step 2: Verify no syntax issues**

Run: `cd /Users/hussnain/Documents/ekai/code/ekai-AIXcelerator/ai-service && source venv/bin/activate && python -c "from agents.prompts import Prompts; print(len(Prompts.MODELING_PROMPT))"`

Expected: A number (the character count of the prompt), no import errors.

**Step 3: Commit**

```bash
git add ai-service/agents/prompts.py
git commit -m "Rewrite MODELING_PROMPT with BRD-driven reasoning chain

Replace source-table-mirroring prompt with structured 5-step reasoning:
extract from BRD, identify business processes, decide fact/dim/skip,
best-practice checks, present with rationale. Adds flexibility rule
for non-Kimball patterns, skip logic for unreferenced tables, and
guardrail warning acknowledgment."
```

---

### Task 2: Add guardrail warning checks in create_gold_tables_batch

**Files:**
- Modify: `ai-service/tools/modeling_tools.py:318-320` (insert guardrail validation before the per-table loop)
- Modify: `ai-service/tools/modeling_tools.py:527-539` (add warnings to batch result JSON)

**Step 1: Add the 4 guardrail check functions**

Insert the following BEFORE the `create_gold_tables_batch` function (after the `_build_gold_ddl` function, around line 110). These are standalone helper functions:

```python
# ---------------------------------------------------------------------------
# Guardrail warnings (surfaced to LLM, not hard blockers)
# ---------------------------------------------------------------------------

_MONETARY_KEYWORDS = {
    "COST", "REVENUE", "AMOUNT", "TOTAL", "SUM", "COUNT", "AVG",
    "PRICE", "FEE", "CHARGE", "PAYMENT", "BALANCE", "SALARY", "WAGE",
    "EXPENSE", "INCOME", "PROFIT", "LOSS", "BUDGET", "SPEND",
    "PREMIUM", "DEDUCTIBLE", "COPAY", "REIMBURSEMENT", "COVERAGE",
}


def _check_measureless_facts(tables: list[dict]) -> list[str]:
    """Warn if a fact table SELECT has no numeric columns."""
    warnings = []
    for spec in tables:
        if spec.get("type") != "fact":
            continue
        name = spec.get("name", "")
        sql = spec.get("select_sql", "").upper()
        # Check if any numeric-indicator keywords/types appear in the SELECT
        has_numeric = any(
            kw in sql
            for kw in (
                "NUMBER", "FLOAT", "DECIMAL", "INT", "NUMERIC",
                "SUM(", "AVG(", "COUNT(", "AMOUNT", "COST", "TOTAL",
                "REVENUE", "QUANTITY", "DURATION", "PRICE",
            )
        )
        if not has_numeric:
            warnings.append(
                f"FACT WARNING: {name} has no numeric measures in its SELECT. "
                f"Consider reclassifying as a dimension or confirming it is a "
                f"factless fact (event tracking only)."
            )
    return warnings


def _check_source_mirrors(tables: list[dict]) -> list[str]:
    """Warn if a table is a direct copy of a source (no transformation)."""
    warnings = []
    for spec in tables:
        name = spec.get("name", "")
        sql = spec.get("select_sql", "").upper().strip()
        # A mirror is: SELECT col1, col2, ... FROM single_table (no JOIN, no
        # aggregation, no CASE, no COALESCE, no type casts, no WHERE)
        has_join = " JOIN " in sql
        has_agg = any(fn in sql for fn in ("GROUP BY", "SUM(", "AVG(", "COUNT(", "MIN(", "MAX("))
        has_case = "CASE " in sql or "CASE\n" in sql
        has_coalesce = "COALESCE(" in sql
        has_cast = "CAST(" in sql or "TRY_CAST(" in sql or "::" in sql
        has_where = " WHERE " in sql
        has_union = " UNION " in sql

        if not any([has_join, has_agg, has_case, has_coalesce, has_cast, has_where, has_union]):
            warnings.append(
                f"MIRROR WARNING: {name} appears to be a direct copy of the "
                f"source table with no joins, aggregations, or transformations. "
                f"Consider whether actual modeling was applied."
            )
    return warnings


def _check_measures_in_dimensions(tables: list[dict]) -> list[str]:
    """Warn if a dimension table SELECT includes monetary/aggregate-sounding columns."""
    warnings = []
    for spec in tables:
        if spec.get("type") != "dimension":
            continue
        name = spec.get("name", "")
        sql = spec.get("select_sql", "").upper()
        found = [
            kw for kw in _MONETARY_KEYWORDS
            if kw in sql
        ]
        if found:
            warnings.append(
                f"DIMENSION WARNING: {name} contains potential measures "
                f"({', '.join(sorted(found)[:5])}). Consider moving these to "
                f"a fact table."
            )
    return warnings


def _check_missing_dim_date(tables: list[dict]) -> list[str]:
    """Warn if facts have date columns but no DIM_DATE is in the batch."""
    has_dim_date = any(
        "DATE" in spec.get("name", "").upper()
        and spec.get("type") == "dimension"
        for spec in tables
    )
    if has_dim_date:
        return []

    facts_with_dates = []
    for spec in tables:
        if spec.get("type") != "fact":
            continue
        sql = spec.get("select_sql", "").upper()
        if any(kw in sql for kw in ("DATE", "TIMESTAMP", "_AT", "_ON", "_DT")):
            facts_with_dates.append(spec.get("name", ""))

    if facts_with_dates:
        return [
            f"DATE WARNING: No date dimension found in the batch, but "
            f"these fact tables have date columns: {', '.join(facts_with_dates)}. "
            f"Consider adding a date dimension for time-based analysis."
        ]
    return []
```

**Step 2: Call guardrails at the top of `create_gold_tables_batch`**

In `create_gold_tables_batch`, after the JSON parsing check (line 286) and before the schema ensure (line 296), insert:

```python
    # Run guardrail checks (warnings, not blockers)
    guardrail_warnings: list[str] = []
    guardrail_warnings.extend(_check_measureless_facts(tables))
    guardrail_warnings.extend(_check_source_mirrors(tables))
    guardrail_warnings.extend(_check_measures_in_dimensions(tables))
    guardrail_warnings.extend(_check_missing_dim_date(tables))

    if guardrail_warnings:
        for w in guardrail_warnings:
            logger.warning("Guardrail: %s", w)
```

**Step 3: Include warnings in the batch result JSON**

In `create_gold_tables_batch`, modify the return statement (lines 530-539) to include warnings:

```python
    return json.dumps({
        "summary": {
            "total": len(results),
            "success": success_count,
            "failed": failed_count,
            "tables_registered": len(gold_mapping),
        },
        "warnings": guardrail_warnings,
        "tables": results,
        "gold_mapping": gold_mapping,
    }, default=str)
```

**Step 4: Verify import works**

Run: `cd /Users/hussnain/Documents/ekai/code/ekai-AIXcelerator/ai-service && source venv/bin/activate && python -c "from tools.modeling_tools import create_gold_tables_batch, _check_measureless_facts; print('OK')"`

Expected: `OK` with no import errors.

**Step 5: Commit**

```bash
git add ai-service/tools/modeling_tools.py
git commit -m "Add 4 guardrail warning checks to create_gold_tables_batch

Validates batch before DDL execution:
1. Measureless fact check (no numeric columns)
2. Source mirror check (direct copy, no transformation)
3. Measures-in-dimension check (monetary columns in dims)
4. Missing DIM_DATE check (date columns in facts but no date dim)

Warnings are surfaced in batch result JSON, not hard blockers."
```

---

### Task 3: Tweak orchestrator Rule 13 to pass BRD content

**Files:**
- Modify: `ai-service/agents/prompts.py:67` (Rule 13 task description)

**Step 1: Update Rule 13 to include BRD content instruction**

The current Rule 13 (line 67) says:

```
Tell it: "Design a star schema based on the BRD and Data Description. Read both documents first. Source from curated layer (EKAIX.{dp_name}_CURATED) when available, otherwise from the original tables. Marts schema: EKAIX.{dp_name}_MARTS."
```

Replace with:

```
Tell it: "Design an analytical data model based on the BRD and Data Description. Read both documents first using get_latest_brd and get_latest_data_description. Source from curated layer (EKAIX.{dp_name}_CURATED) when available, otherwise from the original tables. Marts schema: EKAIX.{dp_name}_MARTS. IMPORTANT: Every Gold table must trace back to a BRD requirement. Skip source tables with no BRD relevance. Group by business process, not by source table."
```

This reinforces the BRD-driven design principle at the delegation point.

**Step 2: Verify syntax**

Run: `cd /Users/hussnain/Documents/ekai/code/ekai-AIXcelerator/ai-service && source venv/bin/activate && python -c "from agents.prompts import Prompts; print('OK')"`

Expected: `OK`

**Step 3: Commit**

```bash
git add ai-service/agents/prompts.py
git commit -m "Reinforce BRD-driven design in orchestrator Rule 13

Add explicit instruction to skip irrelevant tables and group by
business process when delegating to modeling agent."
```

---

### Task 4: Restart and verify

**Step 1: Restart AI service**

Run: `pm2 restart ai-service`

Expected: `ai-service` shows `online` status.

**Step 2: Check for startup errors**

Run: `pm2 logs ai-service --lines 20 --nostream 2>&1 | grep -i error`

Expected: No Python import errors or syntax errors.

**Step 3: Manual smoke test**

Run the SYNTHEA E2E pipeline (or a new data product) through to the modeling phase. Check:
1. The modeling agent reads BRD first (verify in logs: `get_latest_brd` tool call)
2. The design proposal includes BRD justification for each table
3. Skipped tables are listed with reasons
4. Fact tables have numeric measures
5. No measures in dimension tables
6. DIM_DATE is included if time analysis is in BRD
7. Guardrail warnings appear in logs if any checks fire
8. Final Gold layer has fewer tables than source (consolidation happened)

---

## Summary of All Changes

| # | File | Lines | Change |
|---|------|-------|--------|
| 1 | `ai-service/agents/prompts.py` | 354-529 | Replace MODELING_PROMPT with BRD-driven reasoning chain |
| 2 | `ai-service/tools/modeling_tools.py` | ~110 (insert) | Add 4 guardrail warning helper functions |
| 3 | `ai-service/tools/modeling_tools.py` | ~288 (insert) | Call guardrails in `create_gold_tables_batch` |
| 4 | `ai-service/tools/modeling_tools.py` | 530-539 | Add `warnings` key to batch result JSON |
| 5 | `ai-service/agents/prompts.py` | 67 | Tweak orchestrator Rule 13 task description |

## Files NOT Modified (protected)

- `ai-service/tools/ddl.py` — DDL generation, EXPLAIN validation, Cortex AI, column quoting all untouched
- `ai-service/agents/generation.py` — YAML assembler, linter, template system all untouched
- `ai-service/tools/snowflake_tools.py` — Snowflake tools all untouched
- `ai-service/agents/orchestrator.py` — Tool registration, streaming, phase detection all untouched
- All frontend files — untouched
