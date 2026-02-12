# Gold Layer & Data Catalog Architecture Plan

## Problem Statement

The current ekaiX pipeline skips the Gold layer:
- Raw → Silver (transformation agent cleanup) → Semantic View (logical metadata) → Cortex Agent

This violates data warehousing best practices (DAMA DMBOK, Kimball). A Semantic View is a metadata layer, not a substitute for physically modeled analytical tables.

## Correct Architecture

```
Raw Source Tables (customer's Snowflake)
        ↓
Silver Layer (PUDL.SILVER_EKAIX)
  - Type standardization, dedup, null handling
  - Dynamic Tables with TARGET_LAG
  - 1:1 with source tables, same grain
        ↓
Gold Layer (PUDL.GOLD_EKAIX)
  - Star schema: Fact tables + Dimension tables
  - Dynamic Tables derived from Silver
  - Grain defined per fact table
  - Conformed dimensions
  - Pre-aggregated where needed
        ↓
Semantic View
  - Thin metadata on top of Gold tables
  - Measures, dimensions, time dimensions, joins, filters
  - Business-friendly names
        ↓
Cortex Agent
  - Natural language → SQL against Semantic View
```

## Key Principle: Requirements Drive Gold

You cannot design a star schema without knowing the business questions. The flow must be:

1. **Discovery** → understand the source data
2. **Transformation** → create Silver layer (cleanup)
3. **Requirements** → capture business questions, KPIs, metrics
4. **Gold Modeling** → design star schema based on requirements (NEW)
5. **Generation** → Semantic View on Gold tables
6. **Validation** → verify everything works
7. **Publishing** → deploy to Snowflake Intelligence

## What Changes

### Phase Stepper (7 phases now)

```
Discovery → Transformation → Requirements → Modeling → Generation → Validation → Publishing
```

The new **Modeling** phase sits between Requirements and Generation. It:
- Reads the BRD to understand required metrics/KPIs
- Designs fact table(s) at the correct grain
- Designs dimension tables (conformed)
- Creates Dynamic Tables in GOLD_EKAIX schema
- Generates documentation artifacts

### Gold Layer Design Rules (Kimball)

**Fact Tables:**
- One row per event/transaction at the declared grain
- Contains foreign keys to dimension tables
- Contains numeric measures (additive, semi-additive, non-additive)
- Named: `fact_{business_process}` (e.g., `fact_generator_readings`, `fact_maintenance_events`)

**Dimension Tables:**
- Descriptive attributes for filtering/grouping
- Surrogate keys (optional in Snowflake — natural keys often sufficient with Dynamic Tables)
- SCD Type 2 where historical tracking needed (use Silver SCD tables as source)
- Named: `dim_{entity}` (e.g., `dim_plant`, `dim_generator`, `dim_technology`)

**Relationships:**
- Fact → Dimension via foreign keys
- Star schema topology (facts at center, dims radiating out)

### Documentation Artifacts (generated alongside Gold tables)

Each artifact is stored in PostgreSQL and rendered in the frontend artifact panel.

#### 1. Data Catalog
- Every Gold table documented: name, description, grain, source lineage
- Every column documented: name, data type, description, source column, transformation applied
- Stored as structured JSON in a `data_catalog` PostgreSQL table

#### 2. Business Glossary
- Business terms with definitions and logic
- Maps business language to physical columns
- Example: "Active Generator" = `dim_generator.operational_status = 'OP'`
- Stored as structured JSON in a `business_glossary` PostgreSQL table

#### 3. Metrics/KPIs Definitions
- Each metric: name, description, formula (SQL expression), unit, grain, source fact table
- Example: "Total Capacity" = `SUM(fact_generator_readings.capacity_mw)`, unit: MW
- Already partially captured in BRD — Modeling agent enriches with exact formulas
- Stored as structured JSON in a `metrics_definitions` PostgreSQL table

#### 4. Data Validation Rules
- Grain validation: ensure fact table grain is correct (no duplicates at declared grain)
- Referential integrity: all FKs in facts resolve to dimension PKs
- Business rules: range checks, allowed values, NULL policies
- Severity levels: CRITICAL (blocks publishing), WARNING (documented), INFO
- Stored as structured JSON in a `validation_rules` PostgreSQL table

#### 5. Data Lineage
- Source table → Silver table → Gold table → Semantic View mapping
- Column-level lineage where possible
- Stored in Neo4j (extends existing ERD graph with Silver/Gold nodes)
- Rendered as an extended lineage diagram in the artifact panel

### Implementation: Modeling Agent (new subagent)

**Name:** `modeling-agent`
**Position:** After requirements, before generation
**Tools:**
- `get_latest_brd` — read business requirements
- `get_latest_data_description` — understand source data context
- `execute_rcr_query` — verify data in Silver tables
- `create_gold_table` — execute Dynamic Table DDL in GOLD_EKAIX schema (NEW)
- `validate_gold_grain` — run grain check query (NEW)
- `save_data_catalog` — persist catalog documentation (NEW)
- `save_business_glossary` — persist glossary (NEW)
- `save_metrics_definitions` — persist KPI definitions (NEW)
- `save_validation_rules` — persist validation rules (NEW)
- `register_gold_layer` — save Gold→Source lineage to Redis + Neo4j (NEW)
- `upload_artifact` — emit catalog/lineage artifacts to frontend

**Workflow:**
1. Read BRD → extract required metrics, dimensions, grain
2. Read Data Description → understand table roles, relationships
3. Design star schema: identify fact tables, dimension tables, grain per fact
4. Present design to user for approval (PAUSE)
5. User approves → create Dynamic Tables in GOLD_EKAIX
6. Run grain validation on each fact table
7. Generate documentation (catalog, glossary, metrics, validation rules)
8. Update lineage in Neo4j
9. Save all artifacts → hand off to generation agent

**Prompt pattern:** Same PAUSE/DELEGATE pattern as other agents. Presents the star schema design, waits for user approval, then executes DDL.

### Generation Agent Changes

Currently generates Semantic View YAML pointing to source/Silver tables. Changes:
- Points to **Gold** tables instead (GOLD_EKAIX schema)
- FQN resolution maps Silver→Gold (extends existing `working_layer_map`)
- Simpler YAML because Gold tables are already well-modeled (clean star schema)
- Metrics/KPIs from the modeling artifacts feed directly into Semantic View measures

### Frontend Changes

- Phase stepper: 7 phases (add "Modeling" between Requirements and Generation)
- New artifact viewers:
  - `DataCatalogViewer.tsx` — table/column documentation with search
  - `BusinessGlossaryViewer.tsx` — term definitions table
  - `MetricsViewer.tsx` — KPI cards with formulas
  - `ValidationRulesViewer.tsx` — rules with severity badges
  - `LineageDiagramViewer.tsx` — extended lineage (source→silver→gold→semantic view)
- Artifact types extended: `data_catalog`, `business_glossary`, `metrics`, `validation_rules`, `lineage`

### Database Changes

New PostgreSQL tables:
```sql
CREATE TABLE data_catalog (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_product_id UUID NOT NULL REFERENCES data_products(id),
  version INT NOT NULL DEFAULT 1,
  catalog_json JSONB NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE business_glossary (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_product_id UUID NOT NULL REFERENCES data_products(id),
  version INT NOT NULL DEFAULT 1,
  glossary_json JSONB NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE metrics_definitions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_product_id UUID NOT NULL REFERENCES data_products(id),
  version INT NOT NULL DEFAULT 1,
  metrics_json JSONB NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE validation_rules (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  data_product_id UUID NOT NULL REFERENCES data_products(id),
  version INT NOT NULL DEFAULT 1,
  rules_json JSONB NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now()
);
```

Auto-version triggers (same pattern as existing `semantic_views` table).

### Neo4j Lineage Extension

New node types and relationships:
```cypher
// Silver table nodes
(:SilverTable {fqn, source_fqn, transformations_applied})

// Gold table nodes
(:GoldTable {fqn, table_type: 'fact'|'dimension', grain, description})

// Lineage relationships
(:Table)-[:TRANSFORMED_TO]->(:SilverTable)
(:SilverTable)-[:MODELED_TO]->(:GoldTable)
(:GoldTable)-[:EXPOSED_VIA]->(:SemanticView)

// Column-level lineage
(:Column)-[:DERIVED_FROM]->(:Column)
```

## Estimated Effort

| Task | Effort |
|------|--------|
| PostgreSQL schema (4 tables + triggers) | 0.5 day |
| Modeling tools (create_gold_table, validate_grain, save_catalog, etc.) | 1.5 days |
| Modeling agent prompt + orchestrator wiring | 1 day |
| Neo4j lineage extension | 0.5 day |
| Frontend artifact viewers (5 new components) | 1.5 days |
| Phase stepper + orchestrator flow update | 0.5 day |
| Generation agent Gold FQN resolution | 0.5 day |
| E2E testing | 1 day |
| **Total** | **~7 days** |

## What Does NOT Change

- Discovery pipeline (as-is)
- Transformation agent (Silver layer — already correct)
- Requirements agent (BRD capture — already correct, may add KPI-focused questions)
- Validation agent (validates Semantic View — unchanged, but now on Gold tables)
- Publishing agent (deploys to Snowflake Intelligence — unchanged)
- Existing artifacts (ERD, Data Quality, Data Description, BRD, YAML)

## Design Decisions (Resolved)

1. **SCD Type 2: Auto-detect from data.** If Silver layer has SCD tables (e.g., `SCD_BOILERS`, `SCD_GENERATORS`), the modeling agent automatically creates Type 2 dimensions with effective dates. No user prompt needed — the data pattern is sufficient signal.

2. **Pre-aggregation: Propose + confirm.** When a fact table exceeds a size threshold (e.g., 10M+ rows), the modeling agent proposes summary/aggregate tables and pauses for user approval before creating them. Never auto-creates without consent.

3. **Lineage: Separate artifact.** A dedicated "Lineage" artifact with its own viewer showing the full data flow (source → silver → gold → semantic view). The existing ERD remains focused on table relationships within a single layer. Lineage shows cross-layer data flow — different purpose, different viewer.
