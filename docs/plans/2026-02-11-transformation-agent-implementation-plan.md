# Transformation Agent — Implementation Plan

**Date:** 2026-02-11
**Branch:** `feature/data-maturity-transformation-agent`
**Concept Doc:** [`2026-02-11-data-maturity-transformation-agent.md`](./2026-02-11-data-maturity-transformation-agent.md)
**Status:** Research complete, ready for implementation

---

## Research Summary

### Deep Agents v0.3.11 (Current)

- Uses `create_deep_agent(model, system_prompt, tools, subagents, checkpointer)` from the `deepagents` library
- Orchestrator delegates via `task(subagent_type="name", description="...")` tool
- Subagents are defined as dicts: `{name, description, system_prompt, tools, model}`
- Subagents do NOT see parent conversation history — all context must be passed in the `description` parameter
- `SubAgentMiddleware` provides the `task` tool; `SummarizationMiddleware` handles context offloading
- No breaking API changes anticipated — adding a 7th subagent is purely additive

### LangGraph State & Parallelism

- **Send API** for fan-out parallelism: `[Send("node", {"table": t}) for t in tables]` with `operator.add` reducer for collecting results
- **max_concurrency**: `graph.invoke(inputs, config={"max_concurrency": 5})` to throttle parallel nodes
- **interrupt/Command**: `interrupt("question")` pauses execution, `Command(resume=answer)` resumes — requires checkpointer
- **Decision**: NOT needed for this feature. Deep Agents' orchestrator PAUSE/DELEGATE pattern already handles human-in-the-loop. Sequential table processing is sufficient since Dynamic Table DDL is fast (<1s per table). Parallel processing adds complexity for marginal benefit.

### LangChain Tools (2026)

- **ToolRuntime** is the newer pattern replacing `InjectedToolArg` — `runtime: ToolRuntime` parameter auto-injected, hidden from LLM schema
- Provides `runtime.state["messages"]`, `runtime.context.user_id`, `runtime.store` for long-term memory
- **Decision**: Keep existing `contextvars` pattern. `ToolRuntime` requires `context_schema` on `create_agent` which is not compatible with Deep Agents' `create_deep_agent`. Our `contextvars` approach (used for data isolation, SSE queues) works well and is proven.

### Snowflake Dynamic Tables (2025-2026)

- **Core DDL**: `CREATE OR REPLACE DYNAMIC TABLE ... TARGET_LAG = '1 hour' WAREHOUSE = WH AS SELECT ...`
- **Incremental refresh**: Snowflake handles change tracking internally — no custom CDC logic
- **New features (2025)**: IMMUTABLE WHERE, BACKFILL FROM, Zero-Copy, Dual Warehouses, Adaptive Warehouse
- **FLATTEN support**: `LATERAL FLATTEN` now works with incremental refresh (2025 GA)
- **Cortex AI functions**: Supported in incremental refresh (Sep 2025 GA)
- **Limitations**: No sequences in Dynamic Tables; use ROW_NUMBER() for synthetic PKs
- **Decision**: Use basic `CREATE OR REPLACE DYNAMIC TABLE` with `TARGET_LAG`. IMMUTABLE WHERE / BACKFILL FROM are nice-to-have for Phase C.

### Existing Codebase (Audit Findings)

- **Discovery pipeline** (`discovery_pipeline.py`, 1059 lines): 5-step pipeline (metadata → profiling → classification → quality → artifact). Already computes `null_pct`, `uniqueness_pct`, `distinct_count`, `data_type`, `is_likely_pk`, `sample_values`
- **Health scoring** (`discovery.py`): `compute_health_score()` uses completeness_pcts + issue deductions. `classify_table()` does FACT/DIMENSION based on naming + FK count. Both can be extended
- **Profiling** (`snowflake_tools.py:profile_table`): Uses `TABLESAMPLE BERNOULLI` for large tables, `APPROX_COUNT_DISTINCT` for uniqueness. Returns per-column stats. Reusable for maturity classification
- **Orchestrator prompt**: 22 numbered rules using DELEGATE/AUTO-CHAIN/PAUSE pattern. New transformation rules insert between discovery (rules 1-5) and requirements (rules 6-9)
- **Phase tracking**: `_SUBAGENT_PHASE_MAP` in `agent.py` maps subagent names to phase names for frontend stepper. Needs `"transformation-agent": "transformation"` entry
- **Safety nets**: BRD safety net, generation safety net patterns are well-established. Transformation agent needs its own safety net for DDL validation
- **Redis cache**: `cache:discovery:{data_product_id}` stores profiling results. Classification results go here too
- **SSE events**: `phase_change`, `token`, `tool_call`, `artifact`, `message_done`. Transformation uses all existing event types

---

## Architecture Decision: Subagent (NOT Standalone Graph)

The transformation agent is a **7th subagent** in the existing Deep Agents orchestrator, not a standalone LangGraph graph. Rationale:

1. **Consistent with existing architecture** — all agents are subagents delegated via `task()`
2. **Shared checkpointer** — conversation state persists across transformations
3. **Orchestrator controls flow** — PAUSE for user confirmation, AUTO-CHAIN to requirements after transformation
4. **No new infrastructure** — reuses existing SSE streaming, phase tracking, safety nets
5. **Simpler than Send API** — tables processed sequentially within one subagent call (DDL is fast)

---

## Implementation Steps

### Phase A: Data Maturity Classification

**Goal**: Discovery pipeline classifies each table as BRONZE/SILVER/GOLD. User sees the classification. No transformation yet.

#### Step A1: Extend discovery profiling with classification signals

**File:** `ai-service/services/discovery_pipeline.py`

Add a new `_step_classify_maturity()` after `_step_profile()`. This step computes 6 additional signals per table from existing profiling data:

```python
async def _step_classify_maturity(queue, data_product_id, results):
    """Classify each table's data maturity (bronze/silver/gold)."""
    metadata = results.get("metadata", [])
    profiles = results.get("profiles", [])
    classifications = {}

    for table_meta, profile in zip(metadata, profiles):
        fqn = table_meta["fqn"]
        columns = profile.get("columns", [])

        # Signal 1: Type consistency (% VARCHAR/TEXT columns)
        total_cols = len(columns)
        varchar_cols = sum(1 for c in columns if c.get("data_type", "").upper() in
                         ("TEXT", "VARCHAR", "STRING", "VARIANT", "OBJECT", "ARRAY"))
        varchar_ratio = varchar_cols / total_cols if total_cols > 0 else 0

        # Signal 2: Null density (average null_pct)
        null_pcts = [c.get("null_pct", 0) for c in columns]
        avg_null_pct = sum(null_pcts) / len(null_pcts) if null_pcts else 0

        # Signal 3: Duplicate rate (requires a quick SQL query)
        dup_rate = await _compute_duplicate_rate(fqn)

        # Signal 4: Naming convention score
        naming_score = _compute_naming_score([c.get("column", "") for c in columns])

        # Signal 5: PK detectability
        pk_candidates = [c for c in columns if c.get("is_likely_pk", False)]
        pk_confidence = 1.0 if pk_candidates else 0.0

        # Signal 6: Nested structure count
        nested_count = sum(1 for c in columns if c.get("data_type", "").upper() in
                          ("VARIANT", "OBJECT", "ARRAY"))

        # Composite score (0-100)
        score = (
            (1 - varchar_ratio) * 25 +
            (1 - avg_null_pct / 100) * 20 +
            (1 - min(dup_rate, 1.0)) * 15 +
            naming_score * 15 +
            pk_confidence * 15 +
            (1 - min(nested_count / 5, 1.0)) * 10
        )

        if score >= 80:
            maturity = "gold"
        elif score >= 50:
            maturity = "silver"
        else:
            maturity = "bronze"

        classifications[fqn] = {
            "maturity": maturity,
            "score": round(score, 1),
            "signals": {
                "varchar_ratio": round(varchar_ratio, 2),
                "avg_null_pct": round(avg_null_pct, 1),
                "duplicate_rate": round(dup_rate, 3),
                "naming_score": round(naming_score, 2),
                "pk_confidence": pk_confidence,
                "nested_col_count": nested_count,
            },
        }

    results["maturity_classifications"] = classifications
    # Emit SSE event
    await queue.put({"type": "pipeline", "data": {
        "step": "maturity_classification",
        "status": "complete",
        "classifications": classifications,
    }})
    return classifications
```

Helper functions needed:
- `_compute_duplicate_rate(fqn)`: `SELECT 1 - COUNT(DISTINCT *) / COUNT(*) FROM table TABLESAMPLE(10000 ROWS)` — approximate
- `_compute_naming_score(col_names)`: Regex-based scoring: snake_case +points, `col_`/`raw_`/`sys_` prefixes -points, uppercase-only +points (Snowflake convention)

**File:** `ai-service/agents/discovery.py`

Add `classify_data_maturity()` function that wraps the scoring logic for reuse by the transformation agent's `profile_source_table` tool.

#### Step A2: Store classification in Redis cache

**File:** `ai-service/services/discovery_pipeline.py`

After `_step_classify_maturity`, save the classification results to the existing Redis cache key `cache:discovery:{data_product_id}`. The `maturity_classifications` dict is already added to `results` which gets cached.

#### Step A3: Display classification in discovery analysis

**File:** `ai-service/agents/prompts.py` (DISCOVERY_PROMPT)

Add a section to the discovery prompt that instructs the agent to include maturity classification in its analysis:

```
MATURITY CLASSIFICATION:
When the [INTERNAL CONTEXT] includes maturity_classifications, mention each table's
data readiness in your analysis. Use business-friendly terms:
- Gold: "ready for modeling" / "well-structured"
- Silver: "minor cleanup needed" / "mostly structured, a few columns need attention"
- Bronze: "needs significant preparation" / "raw data that requires transformation"
List specific issues for non-gold tables (e.g., "8 text columns storing numeric values").
```

#### Step A4: Update frontend pipeline display

**File:** `frontend/src/components/chat/MessageThread.tsx`

The existing pipeline status display shows steps. Add recognition for the `maturity_classification` step event. Display as:

```
Step 6/7: Classifying data maturity
  - IOT_READINGS_DATA: Silver (score 62)
  - MAINTENANCE_EVENTS: Silver (score 55)
  - WATER_SENSORS_MASTER: Gold (score 88)
```

---

### Phase B: Core Transformation Agent

**Goal**: New 7th subagent that creates Dynamic Tables for bronze/silver tables. Type casting, dedup, null handling.

#### Step B1: Create transformation prompt

**File:** `ai-service/agents/prompts.py`

Add `TRANSFORMATION_PROMPT`:

```python
TRANSFORMATION_PROMPT = """You are the Data Transformation Agent for ekaiX...
[Full prompt as specified in concept doc section 4.2, with additions:]

IMPORTANT RULES:
- Use CREATE OR REPLACE DYNAMIC TABLE with TARGET_LAG = '1 hour'
- Use the warehouse from the data product context
- Target schema: {database}.SILVER_EKAIX (created automatically)
- Always validate: row count, null reduction, type conformance after DDL execution
- Ask the user when transformations are ambiguous
- Never drop columns — transform or pass through
- Create one Dynamic Table per source table that needs transformation
- After ALL tables pass validation, call register_transformed_layer

TABLE SCOPE RULE: Each Dynamic Table SELECT can only reference its own source table.

SQL PATTERNS:
[Include the pattern library from concept doc section 9]

CONVERSATION RULES:
- First message: present transformation plan and ask about ambiguous decisions
- After user confirms: execute DDL, validate, report results
- If validation fails: adjust DDL and retry (max 3 attempts per table)
- After all tables pass: summarize and call register_transformed_layer
"""
```

#### Step B2: Create transformation tools

**File:** `ai-service/tools/transformation_tools.py` (NEW)

5 tools, following existing patterns (use `@tool` decorator, `contextvars` for data isolation, JSON return strings):

```python
# 1. profile_source_table — extends existing profiling with maturity signals
@tool
async def profile_source_table(table_fqn: str) -> str:
    """Profile a source table for transformation planning.
    Returns column types, null %, distinct counts, VARCHAR-stored-numeric detection,
    nested JSON detection, duplicate rate, maturity classification.
    """
    # Reuse discovery profiling + add maturity signals

# 2. generate_dynamic_table_ddl — validates syntax, returns DDL string
@tool
async def generate_dynamic_table_ddl(
    source_fqn: str,
    target_fqn: str,
    transformations_json: str,
    target_lag: str = "1 hour",
) -> str:
    """Generate CREATE OR REPLACE DYNAMIC TABLE DDL from transformation specs."""
    # Build DDL from JSON transformation list
    # Validate syntax (basic SQL parse check)
    # Return DDL string

# 3. execute_transformation_ddl — runs DDL against Snowflake
@tool
async def execute_transformation_ddl(ddl: str) -> str:
    """Execute a CREATE DYNAMIC TABLE statement against Snowflake."""
    # Data isolation check (same database)
    # Execute with RCR
    # Return success/error

# 4. validate_transformation — compares source and target
@tool
async def validate_transformation(source_fqn: str, target_fqn: str) -> str:
    """Validate transformation by comparing source and target table."""
    # Row count comparison
    # Column type check
    # Null rate comparison
    # PK uniqueness check (if synthesized)
    # Sample 5 rows spot-check

# 5. register_transformed_layer — persists table mapping
@tool
async def register_transformed_layer(
    data_product_id: str,
    table_mapping_json: str,
) -> str:
    """Register transformed tables as the working layer."""
    # Parse JSON: {"original_fqn": "transformed_fqn", ...}
    # Store in Redis: cache:working_layer:{data_product_id}
    # Store in PostgreSQL: data_products.settings (JSONB)
    # Return success
```

Key implementation details:
- `execute_transformation_ddl` must check the `CREATE SCHEMA IF NOT EXISTS {db}.SILVER_EKAIX` before creating the Dynamic Table
- `validate_transformation` runs 4 SQL queries: `COUNT(*)` on source/target, `SHOW COLUMNS` on target (type check), `SELECT APPROX_COUNT_DISTINCT(pk)` on target, `SELECT * FROM target LIMIT 5`
- `register_transformed_layer` stores mapping in Redis under `cache:working_layer:{data_product_id}` and also updates the PostgreSQL `data_products` table's `settings` JSONB field

#### Step B3: Register transformation agent as 7th subagent

**File:** `ai-service/agents/orchestrator.py`

Add to `_load_tools()`:
```python
from tools.transformation_tools import (
    profile_source_table,
    generate_dynamic_table_ddl,
    execute_transformation_ddl,
    validate_transformation,
    register_transformed_layer,
)

_transformation_tools = [
    profile_source_table,
    generate_dynamic_table_ddl,
    execute_transformation_ddl,
    validate_transformation,
    register_transformed_layer,
    execute_rcr_query,  # For ad-hoc inspection
]
```

Add to `_build_subagents()`:
```python
{
    "name": "transformation-agent",
    "description": (
        "Prepares non-gold data for semantic modeling by creating "
        "Snowflake Dynamic Tables. Handles type casting, deduplication, "
        "null handling, and column renaming. Use after discovery when "
        "tables are classified as bronze or silver quality."
    ),
    "system_prompt": _s(TRANSFORMATION_PROMPT),
    "tools": _transformation_tools,
    "model": model,
},
```

#### Step B4: Update orchestrator prompt with transformation rules

**File:** `ai-service/agents/prompts.py` (ORCHESTRATOR_PROMPT)

Insert new rules between existing discovery rules (3-5) and requirements rules (6+). Renumber all subsequent rules.

New rules to insert after rule 3:

```
3.5. Discovery completed AND maturity_classifications show ANY table as bronze or silver
     → DELEGATE to transformation-agent. Include:
     (a) data_product_id
     (b) maturity_classifications JSON (all tables with scores and signals)
     (c) the database.schema from the data product
     (d) warehouse name
     Tell it: "Create Dynamic Tables for tables classified as bronze or silver.
     Gold tables need no transformation. Target schema: {database}.SILVER_EKAIX."

3.6. Transformation agent presented a plan and asked questions AND user answered
     → DELEGATE to transformation-agent. Include the user's answers
     and the original classification data.

3.7. Transformation agent completed (register_transformed_layer was called)
     → PAUSE. Tell the user a summary of what was transformed and that
     the data is now ready for requirements capture.

3.8. Transformation completed AND user confirms or asks to proceed
     → DELEGATE to requirements-agent. Include:
     (a) data_product_id
     (b) the working layer mapping (original → transformed FQNs)
     (c) the Data Description content
     Tell it: "ROUND 1. These tables have been transformed for modeling.
     Working layer: {mapping}. Assess what you know and ask clarifying questions."
```

Update the SUBAGENT CONTEXT RULE to include working layer mapping when delegating to generation/validation agents.

#### Step B5: Update phase tracking in agent.py

**File:** `ai-service/routers/agent.py`

Add to `_SUBAGENT_PHASE_MAP`:
```python
"transformation-agent": "transformation",
```

Add transformation-specific tracking flag:
```python
_transformation_phase_ran: bool = False
```

In the `on_tool_start` handler for `task` tool:
```python
if phase_name == "transformation":
    _transformation_phase_ran = True
```

#### Step B6: Update generation agent to use working layer

**File:** `ai-service/agents/prompts.py` (GENERATION_PROMPT)

Add section:
```
WORKING LAYER TABLES:
If the task description includes a working_layer mapping, use the TRANSFORMED
FQNs (right side of the mapping) for all table references in the semantic model.
The original source FQNs should NOT appear in the YAML — use the clean versions.
Gold tables not in the mapping are used directly with their original FQNs.
```

**File:** `ai-service/agents/generation.py`

In `assemble_semantic_view_yaml()` and `_lint_and_fix_structure()`, add working layer FQN resolution. Load the mapping from Redis (`cache:working_layer:{data_product_id}`). When building the YAML `base_table` entries, use the transformed FQN if the original FQN is in the mapping.

#### Step B7: Update frontend phase stepper

**File:** `frontend/src/components/chat/MessageThread.tsx` (or wherever PHASES is defined)

Add conditional "Transformation" phase between Discovery and Requirements:

```typescript
const PHASES = [
  { key: 'discovery', label: 'Discovery' },
  // Transformation phase is conditionally inserted here
  { key: 'requirements', label: 'Requirements' },
  { key: 'generation', label: 'Generation' },
  { key: 'validation', label: 'Validation' },
  { key: 'publishing', label: 'Publishing' },
];

// If a phase_change event with to="transformation" is received,
// insert the transformation phase into the stepper
```

The existing `phase_change` SSE event handler already handles dynamic phase tracking. The stepper just needs to recognize `"transformation"` as a valid phase and position it correctly.

---

### Phase C: Advanced Transformations

**Goal**: JSON flattening, PK synthesis, column renaming, transformation report artifact, and user confirmation flow.

#### Step C1: JSON/VARIANT flattening patterns

**File:** `ai-service/tools/transformation_tools.py`

Extend `generate_dynamic_table_ddl` to support flatten transformations:

```python
# Transformation type: "flatten"
# Input: {"type": "flatten", "column": "METADATA", "extract": [
#     {"key": "source", "target_type": "VARCHAR", "target_name": "data_source"},
#     {"key": "version", "target_type": "NUMBER", "target_name": "schema_version"},
# ]}
#
# Output SQL:
#   METADATA:source::VARCHAR AS data_source,
#   METADATA:version::NUMBER AS schema_version,
```

For array flattening (LATERAL FLATTEN), add a separate pattern that creates additional rows:

```python
# Only used when user explicitly confirms array expansion
# SELECT t.*, f.VALUE:key::type AS extracted_name
# FROM source t, LATERAL FLATTEN(input => t.array_col) f
```

#### Step C2: PK synthesis

**File:** `ai-service/tools/transformation_tools.py`

Two patterns in `generate_dynamic_table_ddl`:

```python
# Hash-based (preferred — deterministic, survives re-creation)
"SHA2(CONCAT_WS('|', COALESCE(col1::VARCHAR,''), COALESCE(col2::VARCHAR,''))) AS _row_id"

# ROW_NUMBER (fallback — for truly keyless tables)
"ROW_NUMBER() OVER (ORDER BY {deterministic_cols}) AS _row_id"
```

Note: Dynamic Tables do NOT support sequences. ROW_NUMBER is the only option for synthetic sequential PKs.

#### Step C3: Column renaming heuristics

**File:** `ai-service/agents/discovery.py`

Add `suggest_column_renames()`:
- Strip common prefixes: `col_`, `raw_`, `sys_`, `src_`
- Expand abbreviations: `txn` → `transaction`, `amt` → `amount`, `dt` → `date`, `nm` → `name`, `qty` → `quantity`
- Convert to snake_case if not already
- The LLM reviews and may override suggestions

#### Step C4: Transformation report artifact

**File:** `ai-service/tools/transformation_tools.py`

After `register_transformed_layer`, generate a transformation report artifact:

```python
@tool
async def save_transformation_report(data_product_id: str, report_json: str) -> str:
    """Save the transformation report as an artifact."""
    # Persist to PostgreSQL artifacts table (artifact_type = "transformation_report")
    # Upload to MinIO
    # Return artifact_id
```

**File:** `frontend/src/components/artifacts/` (or existing artifact components)

Add a `TransformationReportViewer` component that renders:
- Source → Target mapping
- Transformations applied per table
- Quality comparison (before/after row count, null rate, type conformance)

**File:** `frontend/src/components/chat/ArtifactCard.tsx`

Add `transformation_report` to `ARTIFACT_META`.

#### Step C5: User confirmation flow for ambiguous decisions

This is handled naturally by the subagent pattern:

1. Transformation agent presents plan with questions (first message)
2. Orchestrator PAUSEs (rule 3.5 trigger condition not met — transformation agent asked questions)
3. User answers
4. Orchestrator DELEGATEs back to transformation agent with answers (rule 3.6)
5. Transformation agent executes DDL, validates, reports
6. Orchestrator PAUSEs (rule 3.7)

No new infrastructure needed — the existing PAUSE/DELEGATE cycle handles this.

---

### Phase D: Multi-Platform (Future)

Not part of this implementation plan. Documented in concept doc section 11. Requires `TransformationBackend` protocol abstraction.

---

## File Change Summary

| # | File | Action | Description |
|---|------|--------|-------------|
| 1 | `ai-service/services/discovery_pipeline.py` | MODIFY | Add `_step_classify_maturity()`, `_compute_duplicate_rate()`, `_compute_naming_score()` |
| 2 | `ai-service/agents/discovery.py` | MODIFY | Add `classify_data_maturity()` wrapper function |
| 3 | `ai-service/agents/prompts.py` | MODIFY | Add `TRANSFORMATION_PROMPT`, update `ORCHESTRATOR_PROMPT` (new rules 3.5-3.8), update `DISCOVERY_PROMPT` (maturity display), update `GENERATION_PROMPT` (working layer) |
| 4 | `ai-service/tools/transformation_tools.py` | CREATE | 6 tools: `profile_source_table`, `generate_dynamic_table_ddl`, `execute_transformation_ddl`, `validate_transformation`, `register_transformed_layer`, `save_transformation_report` |
| 5 | `ai-service/agents/orchestrator.py` | MODIFY | Add transformation tools + 7th subagent to `_build_subagents()` |
| 6 | `ai-service/routers/agent.py` | MODIFY | Add `"transformation-agent": "transformation"` to `_SUBAGENT_PHASE_MAP`, add `_transformation_phase_ran` flag |
| 7 | `ai-service/agents/generation.py` | MODIFY | Working layer FQN resolution in `assemble_semantic_view_yaml()` |
| 8 | `ai-service/config.py` | MODIFY | Add `transformation_target_lag` and `transformation_target_schema_suffix` settings |
| 9 | `frontend/src/components/chat/MessageThread.tsx` | MODIFY | Recognize `maturity_classification` pipeline step, conditional "Transformation" phase in stepper |
| 10 | `frontend/src/components/artifacts/TransformationReportViewer.tsx` | CREATE | Render transformation report artifact |
| 11 | `frontend/src/components/chat/ArtifactCard.tsx` | MODIFY | Add `transformation_report` to `ARTIFACT_META` |

---

## Build Sequence

```
Phase A (classification):
  A1 → A2 → A3 → A4  (sequential — each depends on previous)

Phase B (core agent):
  B1 ─┐
  B2 ─┤ (B1+B2 can be parallel — prompt and tools are independent)
      ├→ B3 → B4 → B5 (sequential — orchestrator needs both)
  B6 ─┘ (B6 can be parallel with B3-B5)
  B7    (frontend, can be parallel with all backend work)

Phase C (advanced):
  C1, C2, C3 (parallel — independent tool extensions)
  C4 → C5    (report needs to exist before confirmation flow is tested)
```

---

## Testing Strategy

### Unit Tests

1. **Classification scoring**: Test `classify_data_maturity()` with mock profiles at boundary scores (49, 50, 79, 80)
2. **DDL generation**: Test `generate_dynamic_table_ddl` with various transformation types (cast, dedup, flatten, pk_synthesize)
3. **Naming score**: Test `_compute_naming_score()` with snake_case, camelCase, prefixed columns
4. **Working layer resolution**: Test FQN mapping in `assemble_semantic_view_yaml()`

### Integration Tests

1. **Classification pipeline**: Run full discovery on DMTDEMO.BRONZE tables, verify maturity scores are in Redis cache
2. **Dynamic Table creation**: Create a test Dynamic Table with type casting + dedup, verify it refreshes
3. **Validation tool**: Compare source/target after transformation, verify row count and type checks
4. **Register + resolve**: Register working layer, verify generation agent uses transformed FQNs

### E2E Tests (Playwright)

1. **Gold-only flow**: Connect gold-quality data → verify NO transformation phase appears, normal 5-phase flow
2. **Silver flow**: Connect data with VARCHAR-stored numerics → verify transformation phase appears, Dynamic Tables created, downstream agents use transformed tables
3. **User confirmation**: Verify transformation agent asks about ambiguous decisions, user answers, agent proceeds
4. **Full pipeline**: Discovery → Classification → Transformation → Requirements → Generation → Validation → Publishing

---

## Risk Mitigations

| Risk | Mitigation |
|------|------------|
| LLM generates incorrect DDL | `validate_transformation` tool compares source/target. Max 3 retries. |
| Dynamic Table compute cost | Default `TARGET_LAG = '1 hour'` is conservative. Display estimated cost before creating. |
| Schema permission denied | Try `CREATE SCHEMA IF NOT EXISTS`. If denied, ask user for writable schema. Fall back to same schema with `_CLEAN` suffix. |
| Classification misclassifies gold as silver | Conservative thresholds (gold >= 80). Transformation on gold data is harmless (no-op transforms). User can override. |
| Agent loops on validation failure | Max 3 retries per table. After 3rd failure, inform user and PAUSE. |
| Dynamic Tables unavailable (old Snowflake edition) | Check `SHOW DYNAMIC TABLES` support at startup. Fall back to regular `CREATE TABLE AS SELECT` (static, no auto-refresh). |
| Working layer mapping lost (Redis eviction) | Persist to PostgreSQL `data_products.settings` JSONB as backup. Restore from PG on cache miss. |

---

## Dependencies

- No new Python packages required
- No new npm packages required
- Existing Snowflake connection supports Dynamic Tables (DMTDEMO account has them enabled)
- Existing Redis, PostgreSQL, Neo4j connections reused

---

## Estimated Effort

| Phase | Scope | Backend | Frontend |
|-------|-------|---------|----------|
| A | Classification | 1-2 days | 0.5 day |
| B | Core Agent | 3-4 days | 1 day |
| C | Advanced | 2-3 days | 1 day |
| **Total** | | **6-9 days** | **2.5 days** |
