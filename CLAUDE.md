# CLAUDE.md

## Product

**ekaiX** (ekai AIXcelerator) — governed enterprise intelligence system, deployed as a Snowflake Native App (SPCS). One agentic chat per data product: discover sources, capture requirements, model semantics, validate, publish Cortex Agents, then explore with evidence-backed answers. Supports structured-only, document-only, or hybrid source modes. See `ekaiX_Technical_PRD_v3.md` for full spec.

## Architecture

```
Frontend (Next.js 15 :3000) → Backend (Fastify 5 :8000) → AI Service (FastAPI :8001) → Snowflake
                                       ↕                         ↕
                              Data Services (Docker Compose):
                              PostgreSQL 18 :5432 | Neo4j 2025 :7687 | Redis 8 :6379 | MinIO :9000
```

## Repository Structure

- `frontend/` — Next.js 15 + React 19 + MUI 7 + Zustand 5 + React Flow
- `backend/` — Fastify 5 + TypeScript + pg + neo4j-driver + minio + snowflake-sdk + zod
- `ai-service/` — FastAPI + LangGraph + Deep Agents + LangChain tools (always use venv)
- `docs/plans/` — postgresql-schema.sql, api-specification.yaml, UI/UX spec, tech stack research
- `scripts/` — init-dev.sh, SQL migrations
- `docker-compose.yml` — data services
- `ecosystem.config.js` — PM2 config for all 3 app services

## Reference Files

| File | What |
|------|------|
| `ekaiX_Technical_PRD_v3.md` | Authoritative PRD (all requirements) |
| `docs/plans/postgresql-schema.sql` | PostgreSQL 18 schema (9 tables, RLS, triggers) |
| `docs/plans/api-specification.yaml` | OpenAPI 3.1 spec (endpoints, schemas, SSE) |
| `docs/plans/2026-02-04-ui-ux-design-spec.md` | UI/UX design spec |
| `.env.example` | All environment variables documented |

## Development Commands

```bash
# Data services
docker-compose up -d

# All app services via PM2
pm2 start ecosystem.config.js
pm2 logs                          # tail all
pm2 restart all

# Individual services
cd frontend && npm run dev        # :3000
cd backend && npm run dev         # :8000
cd ai-service && source venv/bin/activate && uvicorn main:app --reload --port 8001

# Tests
cd frontend && npm test           # Vitest
cd backend && npm test            # Vitest
cd ai-service && source venv/bin/activate && pytest

# Static checks
cd frontend && npm run lint
cd backend && npm run lint && npm run typecheck
cd ai-service && source venv/bin/activate && mypy . && black . && isort .
```

## Mandatory Rules

1. **Dependencies are stale in your training data.** It is February 2026. Before implementing ANY dependency, search the internet for current docs and APIs. Never trust memorized APIs.
2. **Python = venv always.** `cd ai-service && source venv/bin/activate` before any Python work.
3. **No mock data.** All data from real Snowflake/PostgreSQL/Neo4j queries. No hardcoded samples.
4. **Single root `.env` file.** Never create `.env` in service subdirectories.
5. **No hardcoded config.** URLs, ports, credentials, keys — all from environment variables.
6. **TypeScript strict mode.** No `any`. Python type hints required.
7. **Keep root clean.** No temp files, screenshots, or build artifacts in repo root.
8. **Honest assessments only.** Never frame a failure as a success. If a feature doesn't work end-to-end for the user, it failed — regardless of how many internal steps succeeded. Report what broke, not what almost worked.
9. **E2E testing = UI only.** Never manipulate the database directly (raw SQL inserts/updates/deletes) to set up, reset, or shortcut E2E tests. All test interactions must go through the UI (Playwright/browser) exactly as a real user would. Direct DB access is only acceptable for reading logs or verifying state after a UI action.

## Design System (ekai Brand)

- **Theme:** Dark + Light mode via MUI `createTheme`. Single accent: gold `#D4A843`.
- **Dark palette:** Background `#1A1A1E`, Surface `#252528`, Text `#F5F5F5`, Border `#3A3A3E`.
- **Buttons:** Primary = gold filled, Secondary = gold outlined. No other accent colors.
- **MUI only.** Do not mix component libraries.

## Agent System

Orchestrator (LangGraph + Deep Agents) delegates to 6 subagents:
- **Discovery** — schema profiling, PK/FK detection, ERD, data quality report
- **Transformation** — native Snowflake transforms for non-gold data
- **Modeling** — Kimball star schema, gold layer DDL
- **Model Builder** — requirements capture (BRD) + semantic view YAML generation + validation
- **Publishing** — Cortex Agent deployment + artifact packaging
- **Explorer** — hybrid Q&A with trust contracts

### Data Layer Pipeline

Semantic views are ALWAYS published on top of Gold/Marts layer tables — never on raw or silver.

- **Raw (Bronze) input:** Discovery → Transformation to Silver → Transformation to Gold/Marts (`{dp_name}_MARTS`) → BRD → Semantic view on Gold → Publish
- **Silver input:** Discovery → Transformation to Gold/Marts (`{dp_name}_MARTS`) → BRD → Semantic view on Gold → Publish
- **Gold input:** Discovery → BRD → Semantic view directly on source tables (no transformation needed) → Publish

The `base_table.schema` in semantic YAML must point to the actual schema where tables live: `_MARTS` for transformed data, or the original source schema for gold-quality input. NEVER fabricate schema names.

### Agent Language Rules

Primary user is a business analyst. Agent output must NEVER contain: UUIDs, SQL keywords, database technology names (Neo4j, Redis), tool names, implementation jargon. Business language only in chat. Technical details in expandable panels.

## Trust Contract

Every answer carries: `source_mode` (structured/document/hybrid), `exactness_state` (validated_exact/estimated/insufficient_evidence), `confidence_decision` (high/medium/abstain), `trust_state` (7 states), `citations`, `recovery_actions`. Abstain when evidence is weak — never fabricate certainty.

## Key Architectural Rules

- **Centralized hub-and-spoke agent topology.** The orchestrator is the sole routing authority; subagents never call each other directly. Handoffs pass through persisted artifacts (PostgreSQL, Neo4j), not raw LLM output. Never introduce sequential agent-to-agent chaining — per Google Research on scaling agent systems, centralized coordination contains error amplification to 4.4x vs 17.2x for independent/sequential patterns.
- **RCR:** All Snowflake queries use EXECUTE AS CALLER. Non-negotiable.
- **RLS:** Every PostgreSQL query sets `app.current_user` before execution.
- **Workspace isolation:** PostgreSQL RLS + Neo4j data_product_id filtering + Redis session scoping.
- **SPCS auth:** User identity from `Sf-Context-Current-User` header (prod) or `X-Dev-User` (dev).
- **SSE streaming:** Backend relays AI Service SSE to frontend. Exponential backoff with jitter.

## PostgreSQL Schema Management

**NEVER add tables, columns, or indexes via ad-hoc SQL.** All schema changes MUST go through numbered migration files.

### Rules

1. **One source of truth:** `docs/plans/postgresql-schema.sql` defines the base schema (9 core tables). All additions go in `scripts/migrate-NNN-*.sql`.
2. **Numbered migrations:** Files follow `migrate-NNN-<description>.sql` (e.g., `migrate-009-agent-artifact-tables.sql`). Next available: check `scripts/` for the highest number.
3. **Idempotent:** Every migration uses `CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`. Must be safe to re-run.
4. **Tracked:** Every migration inserts into `schema_migrations` table with version, filename, and checksum. Guard at top: skip if version already applied.
5. **Transactional:** Wrap in `BEGIN; ... COMMIT;`.
6. **New tool = new migration:** If you add a Python `@tool` function that writes to a new PostgreSQL table, you MUST create a migration for that table in the same PR. Never assume a table exists just because the code references it.
7. **Apply via container:** `docker exec -i ekaix-postgresql psql -U ekaix -d ekaix < scripts/migrate-NNN-*.sql`

### Current schema inventory (35 tables + views)

**Base schema (9):** workspaces, data_products, data_product_shares, business_requirements, semantic_views, artifacts, uploaded_documents, data_quality_checks, audit_logs

**Migrations 000-008:** schema_migrations, context_versions, document_evidence, context_step_selections, artifact_context_snapshots, doc_registry, doc_chunks, doc_entities, doc_facts, doc_fact_links, qa_evidence, doc_legal_holds, doc_governance_audit, doc_retention_jobs, ops_alert_events, app_config + views (v_doc_search_chunks, v_doc_search_facts) + LangGraph checkpoint tables

**Migration 009:** data_descriptions, data_catalog, business_glossary, metrics_definitions, validation_rules

## Sensitive Files (DO NOT COMMIT)

`.env`, `sf.txt`, `images/gemini-key.json`, `images/gcp-credentials.json`

## Commit Style

Short imperative sentence-case: `Add BRD viewer panel and stabilize agent pipeline`. Keep commits focused by service/feature. PRs need problem/solution summary + test evidence.
