# ERD, Discovery Pipeline & Data Quality Upgrades — 2026-02-06

**Date:** 2026-02-06
**Status:** Complete
**Builds on:** Proactive Discovery Phase 1 (2026-02-04), Artifact Integration (2026-02-05)

---

## Summary

Major upgrade to three interconnected systems: (1) complete ERD Diagram Panel UX overhaul with dagre auto-layout, dark-theme styling, and interactivity, (2) discovery pipeline accuracy fixes for PK/FK detection, and (3) data quality scoring integration. All changes verified end-to-end with real Snowflake data (DMTDEMO.BRONZE — 3 tables).

---

## 1. ERD Diagram Panel — Complete UX Overhaul

**File:** `frontend/src/components/panels/ERDDiagramPanel.tsx` (rewritten from ~350 to ~856 lines)

### What Changed

| Feature | Before | After |
|---------|--------|-------|
| **Layout** | Naive 3-column grid, spaghetti edges | Dagre hierarchical LR layout (fact left, dimensions right) |
| **Styling** | Light theme (#FFF bg, #333 text) | Dark theme (#252528 bg, #F5F5F5 text), brand colors |
| **Table types** | Indistinguishable | Gold left border = Fact, Green = Dimension, type badges |
| **Edge labels** | None (column info hidden) | `SENSOR_ID -> SENSOR_ID N:1` directly on edge |
| **Interactivity** | Pan/zoom only | Click-to-focus with highlight/dim, pane click to reset |
| **Navigation** | None | 220px sidebar with search + click-to-zoom |
| **Overview** | None | MiniMap colored by table type (bottom-right) |
| **PK display** | Listed like any column | Sorted to top, separator line, "COMPOSITE KEY (N cols)" header |
| **Edge tooltips** | None | Hover shows source->target columns, confidence %, cardinality |

### New Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `@dagrejs/dagre` | latest | Directed acyclic graph layout |
| `@dagrejs/graphlib` | 3.0.2 | Required by dagre (explicit install for webpack) |
| `@types/dagre` | (devDep) | TypeScript types |

### Dagre + Next.js Webpack Fix (Critical)

Dagre's ESM bundle wraps `require()` in a dynamic proxy that webpack replaces with `webpackEmptyContext`, causing runtime failures. Fixed in `frontend/next.config.ts`:

```typescript
serverExternalPackages: ['@dagrejs/dagre', '@dagrejs/graphlib'],
webpack: (config) => {
  config.resolve.alias = {
    ...config.resolve.alias,
    '@dagrejs/dagre': path.resolve(__dirname, 'node_modules/@dagrejs/dagre/dist/dagre.cjs.js'),
    '@dagrejs/graphlib': path.resolve(__dirname, 'node_modules/@dagrejs/graphlib/dist/graphlib.cjs.js'),
  };
  return config;
},
```

**Root cause:** dagre's ESM uses esbuild-generated `require()` shim. CJS bundles use plain `require()` that webpack can statically resolve. Both aliases + `serverExternalPackages` are required.

### Component Architecture

```
ERDDiagramPanel (Drawer, 1100px)
├── Header (title + close)
├── Legend (Fact gold / Dimension green)
├── Content Row
│   ├── TableSidebar (220px, search + clickable list)
│   └── ERDFlowCanvas (ReactFlowProvider)
│       ├── ReactFlow (nodes + edges)
│       ├── Controls (zoom in/out, fit view)
│       └── MiniMap (colored by type)
└── Footer (N tables, N relationships)
```

**Key components:**
- `TableNodeComponent` — Custom node with dark styling, fact/dimension colors, composite PK display
- `ERDEdgeComponent` — Custom edge with `BaseEdge` + `EdgeLabelRenderer`, column names on label, MUI Tooltip
- `getLayoutedElements()` — Dagre layout with `rankdir: 'LR'`, `nodesep: 80`, `ranksep: 200`

---

## 2. Discovery Pipeline Accuracy Fixes

**Files:**
- `ai-service/services/discovery_pipeline.py`
- `ai-service/agents/discovery.py`

### Fix 1: PK Semantic Filtering

**Problem:** Columns like `DESCRIPTION` (100% unique free text) were flagged as primary keys because they passed the >98% uniqueness threshold.

**Solution:** Added two exclusion layers before PK classification:

1. **Data type exclusion:** `TEXT`, `CLOB`, `NCLOB`, `STRING`, `VARIANT` types are never PKs
2. **Name exclusion:** Columns containing `description`, `comment`, `note`, `text`, `body`, `message`, `remark`, `summary`, `detail`, `content` are excluded

**Location:** `discovery_pipeline.py` ~line 426

### Fix 2: Composite Primary Key Detection

**Problem:** Tables like `IOT_READINGS_DATA` have no single-column PK — the natural key is a combination of columns (e.g., `SENSOR_ID + LOCATION_ID + TIMESTAMP`).

**Solution:** After single-column profiling, if no PK is found:
1. Collect NOT-NULL columns ending in `_id` or `_key`
2. Collect NOT-NULL timestamp columns
3. Test combinations via Snowflake GROUP BY:
   ```sql
   SELECT
     (SELECT COUNT(*) FROM table) AS total,
     (SELECT COUNT(*) FROM (SELECT 1 FROM table GROUP BY col1, col2)) AS uniq
   ```
4. If `uniq/total > 0.98`, mark combination as composite PK

**Snowflake gotcha:** `COUNT(DISTINCT (col1, col2))` creates a ROW type — Snowflake's COUNT doesn't accept this. Must use GROUP BY subquery instead.

**Location:** `discovery_pipeline.py` ~line 460-497

### Fix 3: FK Target Column Resolution

**Problem:** `infer_foreign_keys()` was hardcoding `"id"` as the target column, producing relationships like `SENSOR_ID -> id` even when no `id` column exists.

**Solution:** Rewrote target column resolution with priority chain:
1. Exact column name match (`SENSOR_ID -> SENSOR_ID`) — confidence 0.95
2. Entity ID match (`entity_id` in target) — confidence 0.95
3. `id` column exists — confidence 0.90
4. Table's actual PK column via `_find_pk_column()` — confidence 0.85
5. No match found — skip relationship entirely

**Location:** `ai-service/agents/discovery.py` `infer_foreign_keys()` function

### Fix 4: PK Data Passed to FK Inference

**Problem:** The FK inference step didn't receive PK information from profiling, so it couldn't resolve target columns accurately.

**Solution:** `_step_fk_inference()` now accepts `profiles` parameter. Builds `pk_lookup` map from profiling results and passes `is_pk` flags to `infer_foreign_keys()`.

**Location:** `discovery_pipeline.py` `_step_fk_inference()` function

### Verification Results (DMTDEMO.BRONZE)

| Table | Before | After |
|-------|--------|-------|
| IOT_READINGS_DATA | No PK | Composite PK: SENSOR_ID + LOCATION_ID + TIMESTAMP |
| MAINTENANCE_EVENTS | 4 PKs (including DESCRIPTION) | 1 PK: TIMESTAMP |
| WATER_SENSORS_MASTER | No PK | Composite PK: SENSOR_ID + LOCATION_ID + INSTALLATION_DATE |
| Edge labels | `SENSOR_ID -> id` | `SENSOR_ID -> SENSOR_ID` (95% confidence) |

---

## 3. Data Quality Scoring

**File:** `ai-service/agents/discovery.py` — `compute_health_score()`

### Scoring Algorithm

Starting score: 100. Deductions applied per issue:

| Check | Deduction | Source |
|-------|-----------|--------|
| Data completeness (avg non-null %) | -1 per % below 90% | Configurable via settings |
| Hard cap: <10% completeness | Score capped at 15 | — |
| Hard cap: <50% completeness | Score capped at 35 | — |
| Duplicate PKs | Configurable per table | `settings.deduction_duplicate_pk` |
| Orphaned FKs | Configurable per table | `settings.deduction_orphaned_fk` |
| Numeric as VARCHAR | Configurable per column | `settings.deduction_numeric_varchar` |
| Missing descriptions | Configurable per table | `settings.deduction_missing_description` |

Floor: 0. Current DMTDEMO score: **94/100**.

### Additional Discovery Functions

| Function | Purpose |
|----------|---------|
| `detect_primary_key()` | Single-column PK via uniqueness threshold (>98%) |
| `classify_table()` | FACT vs DIMENSION via naming prefixes + FK column count |
| `infer_foreign_keys()` | FK relationships via `_id` column pattern matching |

---

## 4. Discovery Pipeline (End-to-End Flow)

**File:** `ai-service/services/discovery_pipeline.py`

### Pipeline Steps (7 total)

| Step | Name | What it does | Progress |
|------|------|-------------|----------|
| 1 | metadata | Queries Snowflake INFORMATION_SCHEMA for tables + columns | 0-14% |
| 2 | profiling | Per-table statistical profiling (uniqueness, nulls, types) + PK detection | 14-28% |
| 3 | classification | FACT/DIMENSION classification via `classify_table()` | 28-42% |
| 4 | fk_inference | FK relationship detection via `infer_foreign_keys()` | 42-57% |
| 5 | erd | Writes nodes + edges to Neo4j graph | 57-71% |
| 6 | quality | Runs quality checks + computes health score | 71-85% |
| 7 | artifacts | Saves quality report to PostgreSQL + uploads to MinIO | 85-100% |

### Caching

- Results cached in Redis (`discovery:pipeline:{data_product_id}`)
- TTL: 1 hour
- Fresh threshold: 5 minutes (skip pipeline if cache this fresh)
- SSE events emitted for each step (progress bar in frontend)

### Proactive Trigger (from Phase 1 — 2026-02-04)

1. Frontend auto-sends `__START_DISCOVERY__` on page load (no messages + tables selected)
2. AI service detects trigger, runs pipeline
3. Pipeline emits SSE progress events (step N/7)
4. On completion, builds discovery summary (1900+ chars)
5. Summary sent to orchestrator agent for conversational response
6. Agent responds conversationally (business-friendly language)
7. Artifact cards (ERD + Data Quality) rendered inline in chat

---

## Files Modified

### Frontend
| File | Change |
|------|--------|
| `frontend/package.json` | Added `@dagrejs/dagre`, `@dagrejs/graphlib`, `@types/dagre` |
| `frontend/next.config.ts` | Added webpack aliases for CJS bundles + `serverExternalPackages` |
| `frontend/src/components/panels/ERDDiagramPanel.tsx` | Complete rewrite (dagre layout, dark styling, interactivity, sidebar, edge labels) |

### AI Service
| File | Change |
|------|--------|
| `ai-service/services/discovery_pipeline.py` | PK semantic filter, composite PK detection (Snowflake GROUP BY syntax), PK data passed to FK inference |
| `ai-service/agents/discovery.py` | FK target column resolution priority chain, `_find_pk_column()` helper, deduplication in `infer_foreign_keys()` |

### No Backend Changes
The backend ERD artifact endpoint already returned `sourceColumn`, `targetColumn`, `confidence`, `cardinality` on edges — the frontend was simply discarding them.

---

## Lessons Learned

1. **Dagre + webpack ESM conflict:** Force both `@dagrejs/dagre` and `@dagrejs/graphlib` to CJS bundles via webpack aliases. The ESM bundles use dynamic `require()` shim that webpack can't resolve.

2. **Snowflake `COUNT(DISTINCT (col1, col2))` doesn't work:** Creates ROW type that COUNT rejects. Use `GROUP BY` subquery: `SELECT COUNT(*) FROM (SELECT 1 FROM table GROUP BY col1, col2)`.

3. **`TABLESAMPLE BERNOULLI` fails on Snowflake views:** Returns 0 rows / all NULLs. Use `LIMIT N` subquery for views.

4. **PK detection needs semantic layer:** Statistical uniqueness alone is insufficient — free-text columns (descriptions, comments) can be 100% unique. Must exclude by data type AND column name.

5. **FK inference needs PK data:** Without knowing which column is the actual PK in the target table, the algorithm falls back to guessing "id" — which often doesn't exist.
