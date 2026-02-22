# Modeling Agent Rewrite — Design Document

**Date:** 2026-02-15
**Status:** Approved
**Scope:** Prompt rewrite + code guardrails. No infrastructure changes.

## Problem

The modeling agent mirrors source tables 1:1 (slaps FACT_/DIM_ prefixes) instead of doing actual data modeling. Result: 9 fact tables where 5 have zero measures, no DIM_DATE, measures stuffed in dimensions, no table consolidation.

## Guiding Principles

1. **BRD drives the design.** Every Gold table must trace back to a business requirement. No table exists "just because a source table exists."
2. **Data modeling best practices as default.** Kimball star schema unless the user asks otherwise. Grain-first design, conformed dimensions, measures in facts only, DIM_DATE when time analysis is needed.
3. **No force-fitting.** If the user wants OBT, Data Vault, or flat tables — build it. Add a brief note about trade-offs, then comply.
4. **Skip what's not needed.** Source tables with no BRD relevance get no Gold table.
5. **Merge when it makes sense.** Multiple source tables serving the same business process can become one fact with a type discriminator.

## Changes

### A. Prompt Rewrite (`ai-service/agents/prompts.py` — MODELING_PROMPT)

Replace the current prompt (~175 lines) with a structured reasoning chain:

1. **Extract from BRD** — List every metric, dimension, filter, business question. Map each to source table + column.
2. **Identify business processes** — Group by what the business is analyzing (not by source table). Each process = candidate fact.
3. **Decide fact vs dimension** — Must have a numeric measure or BRD-justified event count to be a fact. Descriptive entities are dimensions. Unreferenced tables are skipped.
4. **Best-practice checks** — Grain declared for every fact. No measures in dimensions. DIM_DATE if time analysis exists. Conformed dimensions for shared codes.
5. **Present with rationale** — Every table gets a one-line BRD justification. Skipped tables are listed with reason.

Add flexibility clause: comply with user-requested patterns, note trade-offs briefly.

### B. Code Guardrails (`ai-service/tools/modeling_tools.py` — `create_gold_tables_batch`)

Add validation BEFORE executing DDL. These are warnings surfaced to the user, not hard blockers.

1. **Measureless fact check** — If a fact table's SELECT has no numeric columns (NUMBER, FLOAT, DECIMAL, INT), emit warning: "FACT_X has no numeric measures — consider reclassifying as dimension or factless fact."
2. **Source mirror check** — If a table's SELECT is just `SELECT col1, col2, ... FROM source` with no joins, aggregations, type changes, or column additions beyond renaming, emit warning: "FACT_X appears to be a direct copy of SOURCE — no modeling transformation applied."
3. **Measures-in-dimension check** — If a dimension table's SELECT includes columns with monetary/aggregate-sounding names (cost, revenue, amount, total, sum, count, avg), emit warning: "DIM_X contains potential measures (REVENUE, AMOUNT_COVERED) — consider moving to a fact table."
4. **Missing DIM_DATE check** — If any fact table has a date/timestamp column but no DIM_DATE is in the batch, emit warning: "No DIM_DATE found. Consider adding a date dimension for time-based analysis."

Warnings are collected and returned in the batch result JSON under a `"warnings"` key. The LLM sees them. The user sees them in the design proposal.

### C. Orchestrator Rules (minor — `prompts.py` orchestrator section)

No structural changes. Just ensure Rule 13 passes the BRD content (not just "BRD exists") to the modeling agent's task description, so the agent has the actual requirements to reason against.

## Files Modified

| File | Change | Risk |
|------|--------|------|
| `ai-service/agents/prompts.py` | Rewrite MODELING_PROMPT (~175 lines) | Medium — prompt changes affect LLM behavior |
| `ai-service/tools/modeling_tools.py` | Add 4 warning checks in `create_gold_tables_batch` | Low — warnings only, no logic changes |
| `ai-service/agents/prompts.py` | Minor tweak to orchestrator Rule 13 context | Low |

## Files NOT Modified

- `ai-service/tools/ddl.py` — untouched
- `ai-service/agents/generation.py` — untouched (YAML assembler, linter, column resolution all stay)
- `ai-service/tools/snowflake_tools.py` — untouched
- `ai-service/agents/orchestrator.py` — untouched (tool registration stays the same)
- All frontend files — untouched

## Verification

1. Restart AI service
2. Run SYNTHEA E2E test again (same data product or new one)
3. Check Gold layer: should see 2-5 fact tables with real measures, 5-8 dimensions including DIM_DATE, no measureless facts, no measures in dimensions
4. Check that skipped tables are mentioned in the agent's design proposal
5. Check warnings in logs if any guardrail triggers
