# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Product Context

**ekai** is the parent product — an AI-powered data modeling platform. **ekai AIXcelerator (ekaiX)** is a separate, focused product variant built as a Snowflake Native App. When this document or conversations refer to "ekaiX" or "ekai Xcelerator", they mean this repository — not the full ekai platform.

ekaiX enables business users to create semantic models and Cortex Agents through AI conversation. It runs entirely within a customer's Snowflake account via SPCS (Snowpark Container Services). Users interact with an AI agent to discover gold layer databases, capture business requirements, generate semantic view YAML, and publish Cortex Agents to Snowflake Intelligence.

**Status:** Pre-development. The `ekaiX_Technical_PRD.md` file (2381 lines) contains the complete technical specification and is the authoritative source for all requirements.

## Design System (ekai Brand)

ekaiX inherits the ekai brand design language. Reference screenshots in `images/` for visual context.

### Logo

- **Text:** "ekai" — all lowercase
- **Font:** Clean, modern, slightly rounded sans-serif
- **Color:** White on dark backgrounds
- **Placement:** Top-left of sidebar, compact size

### Color Palette

| Role | Color | Usage |
|------|-------|-------|
| **Background (base)** | `#1A1A1E` / near-black | Main app background, sidebar, panels |
| **Surface / Cards** | `#252528` / dark charcoal | Cards, input fields, chat bubbles, elevated panels |
| **Surface (raised)** | `#2A2A2E` / lighter charcoal | Hover states, active sidebar items, right chat panel |
| **Border (default)** | `#3A3A3E` / subtle dark gray | Input borders, card edges, dividers |
| **Border (focus/active)** | `#D4A843` / gold | Focused inputs, selected items, active states |
| **Primary accent** | `#D4A843` / warm gold-amber | Primary buttons, active tabs, links, section labels, selected radio/checkbox, breadcrumb active text |
| **Primary button** | `#D4A843` background with dark text | "Save", "Add Model", "Download Artifacts" |
| **Secondary button** | Transparent with `#D4A843` border | "Cancel", outlined actions |
| **Text primary** | `#F5F5F5` / near-white | Headings, body text, labels |
| **Text secondary** | `#9E9E9E` / medium gray | Placeholder text, inactive tabs, breadcrumb parents |
| **Text accent** | `#D4A843` / gold | Active breadcrumb, "Mission Control" label, highlighted names |
| **Success** | `#4CAF50` / green | Checkmarks, "COMPLETED" badges, success messages |
| **Error/Warning** | Use sparingly, standard red/orange | Validation errors |

### Theme Rules

- **Dark and light mode.** The app must support both dark and light themes. The color palette above documents the dark theme; derive a corresponding light theme (light backgrounds, dark text, same gold accent).
- **UI framework:** Material UI (MUI). Use MUI's theming system (`createTheme`) to define the dark and light palettes with the ekai brand colors. Leverage MUI components throughout — do not mix in other component libraries.
- **Single accent color.** Gold/amber (`#D4A843`) is the only brand accent in both themes. Do not introduce additional accent colors (no blues, purples, etc.).
- **Premium aesthetic.** The warm gold accent creates a luxury/enterprise feel. Maintain this restraint in both themes.
- **Minimal color usage.** The palette is near-monochromatic — dark/light shades + gold. Content colors (data lineage diagrams, ERD nodes) may use pastels for distinction but the chrome/UI stays on-brand.

### Component Conventions

- **Buttons:** Primary = gold filled with contrasting text; Secondary = outlined with gold border; Destructive = use sparingly
- **Inputs:** MUI TextField with gold focus/active border color
- **Tabs:** MUI Tabs with gold indicator on active tab
- **Cards:** MUI Card with subtle border or elevation, rounded corners
- **Badges/Status:** Rounded pill shape (e.g., "COMPLETED" in green, "Limited support" in gold outline)
- **Chat messages:** Agent messages in card-style bubbles with tool call indicators (Write(), Bash() shown with indented descriptions)

## Architecture

```
Frontend (Next.js :3000) → Backend (Node.js :8000) → AI Service (FastAPI :8001) → Snowflake
                                    ↕                        ↕
                           Data Services (Docker):
                           PostgreSQL :5432 (app state, workspaces)
                           Neo4j :7474/:7687 (ERD graph)
                           Redis :6379 (agent state, cache)
                           MinIO :9000/:9001 (artifact storage)
```

All services deploy as containers in SPCS. Locally, the three app services run natively while data services run via Docker Compose.

### Key Architectural Decisions

- **RCR (Restricted Caller's Rights):** All Snowflake queries execute with `EXECUTE AS CALLER` - users only see data their role permits. This is non-negotiable.
- **Gold layer only:** No data transformations or DBT. Semantic views reference existing clean tables directly.
- **SPCS authentication:** No separate auth system. User identity comes from `Sf-Context-Current-User` header.
- **Workspace isolation:** Each Snowflake user has isolated workspaces. Sharing is explicit.
- **AI agents:** Six specialized subagents (Discovery, Requirements, Generation, Validation, Publishing, Explorer) orchestrated by a main agent via LangChain Deep Agents.

### Snowflake Integration

- Stored procedures use Python with `EXECUTE AS CALLER` for RCR
- Cortex AI (Claude Sonnet 4) is the primary LLM via `SNOWFLAKE.CORTEX.COMPLETE`
- Semantic views follow Snowflake's YAML specification (tables, measures, dimensions, time_dimensions, joins, filters)
- Published Cortex Agents are accessible via Snowflake Intelligence

## Development Commands

### Data Services
```bash
docker-compose up -d          # Start PostgreSQL, Neo4j, Redis, MinIO
docker-compose down           # Stop all data services
```

### All App Services (via PM2)
```bash
pm2 start ecosystem.config.js            # Start all services (frontend, backend, ai-service)
pm2 stop all                              # Stop all services
pm2 restart all                           # Restart all services
pm2 logs                                  # Tail all logs
pm2 logs backend                          # Tail specific service logs
pm2 status                                # Show service status
pm2 delete all                            # Remove all processes
```

### Frontend (Next.js)
```bash
cd frontend
npm install
npm run dev                   # Dev server on localhost:3000 (or use PM2)
npm run build                 # Production build
npm test                      # Unit tests (Jest/Vitest)
npm run lint
```

### Backend (Node.js)
```bash
cd backend
npm install
npm run dev                   # Dev server on localhost:8000 (or use PM2)
npm run build                 # Compile TypeScript
npm test
npm run lint
```

### AI Service (FastAPI) — always use venv
```bash
cd ai-service
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8001    # Dev server (or use PM2)
pytest                                    # Unit tests
pytest tests/test_discovery.py -k "test_name"  # Single test
mypy .                                    # Type checking
black . && isort .                        # Formatting
```

### E2E Tests
```bash
npx playwright test                       # All E2E tests
npx playwright test tests/discovery.spec.ts  # Single spec
```

## Implementation Phases

The PRD defines 11 build phases. Follow them in order:

1. Local dev environment setup (Docker Compose, Snowflake test account)
2. Backend foundation (PostgreSQL schema, Neo4j constraints, Redis/MinIO config)
3. Node.js backend API (Fastify/Express endpoints, middleware, DB connections)
4. FastAPI AI service (Deep Agents setup, LangChain tools, MCP integration)
5. Snowflake stored procedures (discovery, profiling, validation, publishing)
6. React frontend (Next.js, chat interface, ERD visualization, workspace management)
7. End-to-end integration testing
8. SPCS packaging (Dockerfiles, spec.yaml files, manifest.yml, setup_script.sql)
9. Documentation
10. Production readiness (security, performance, reliability)
11. Final verification

## Mandatory Rules (High Priority)

1. **Your training data is obsolete.** Your knowledge cuts off at 2024. It is now February 2026. Before implementing ANY component, dependency, or integration, you MUST search the internet for the latest 2026 documentation, API signatures, and package versions. Never rely on memorized APIs — they may have changed. This applies to everything: Next.js, MUI, FastAPI, LangChain, LangGraph, Deep Agents, langchain-mcp-adapters, Snowflake SPCS, Snowpark, and all other dependencies.
2. **All Python work must use a virtual environment (venv).** Always create/activate a venv before installing packages or running Python code. Never install Python packages globally.
3. **Use PM2 for process management.** All services (frontend, backend, AI service) must be managed via PM2 in local development. Use an `ecosystem.config.js` at the project root to define all processes. Start/stop/monitor services through PM2 commands.
4. **Keep the root folder clean.** The repository root must only contain project configuration files (`CLAUDE.md`, `ekaiX_Technical_PRD.md`, `package.json`, `ecosystem.config.js`, `docker-compose.yml`, `.gitignore`, etc.) and service directories (`frontend/`, `backend/`, `ai-service/`). Never dump screenshots, temporary files, logs, or build artifacts in the root. Playwright screenshots must be saved to the scratchpad directory or a dedicated `tmp/` folder outside the repo. Reference images belong in `images/` only.

## Critical Implementation Notes

- **TypeScript strict mode** for all frontend/backend code. No `any` types.
- **Python type hints** for all AI service code.
- **Async by default** where applicable (FastAPI async endpoints, Redis async client).
- **No hardcoded values.** All config via environment variables.
- **Structured JSON logging** across all services.

## Environment Variables

Three `.env` files are needed (see PRD section "Environment Variables" for full list):

- `frontend/.env.local` — API URLs (`NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_WS_URL`)
- `backend/.env` — Database URLs, Snowflake credentials, FastAPI URL
- `ai-service/.env` — Database URLs, Snowflake credentials, LLM API keys, LangSmith config

## Sensitive Files (DO NOT COMMIT)

- `images/gemini-key.json` — GCP credentials
- `sf.txt` — Snowflake account credentials
- Any `.env` files

## SPCS Packaging Structure

```
/manifest.yml
/setup_script.sql
/spcs_services/{frontend,backend,ai-service,postgresql,neo4j,redis,minio}/
  Dockerfile
  spec.yaml
/stored_procedures/{discovery,profiling,validation,publishing}.py
```

## Database Schemas

**PostgreSQL tables:** workspaces, data_products, data_product_shares, business_requirements, semantic_views, artifacts, audit_logs, uploaded_documents. All use UUID PKs, JSONB for flexible state, row-level security for workspace isolation.

**Neo4j nodes:** Database → Schema → Table → Column (hierarchy via HAS_SCHEMA/HAS_TABLE/HAS_COLUMN). Tables classified as FACT or DIMENSION. FK relationships have confidence scores and cardinality.

**Redis keys:** `agent:session:{id}` (state), `checkpoint:{session}:{id}` (LangGraph), `cache:erd:{id}`, `cache:profile:{fqn}`. TTL 1 hour on cache entries.

**MinIO buckets:** artifacts (`{data_product_id}/erd/`, `/yaml/`, `/brd/`), documents (`/uploads/`, `/extracted/`), workspace.

## API Endpoints

Backend exposes REST endpoints grouped as:
- `/auth/*` — User from SPCS header
- `/databases/*` — Discovery via Snowflake INFORMATION_SCHEMA (RCR)
- `/data-products/*` — CRUD with workspace isolation
- `/agent/*` — Message sending, SSE streaming, interrupt, rollback
- `/artifacts/*` — ERD, YAML, BRD retrieval and export
- `/documents/*` — Upload and Cortex Document AI extraction

## AI Agent Architecture

The orchestrator delegates to subagents based on conversation phase:
- **Discovery Agent** — Profiles schemas, detects PKs (>98% uniqueness), infers FKs, builds ERD in Neo4j. Biases toward false positives in relationship detection.
- **Requirements Agent** — Interactive BRD capture. Max 15 conversation turns. Shows actual data examples.
- **Generation Agent** — Creates semantic view YAML from BRD. Must use fully qualified table names and verify column existence.
- **Validation Agent** — Tests generated YAML against real data via RCR. Checks cardinality, nulls, ranges.
- **Publishing Agent** — Deploys semantic view + Cortex Agent to Snowflake Intelligence.
- **Explorer Agent** — Ad-hoc data queries during conversation.

Tools are organized by data store (Snowflake, Neo4j, PostgreSQL, MinIO) and integrated via LangChain. MCP servers provide external system access (Google Drive, Confluence, Slack, SharePoint).

Backend uses CompositeBackend: Redis (active state) + PostgreSQL (persistence) + MinIO (artifacts) + Neo4j (graph).

## Requirements Beyond the PRD

The following requirements extend or clarify the PRD based on product decisions made after the PRD was written.

### Data Quality Report (extends PRD's NFR7 quality gate)

The PRD defines a silent pass/fail quality gate (>60% or block). This is insufficient. After the Discovery phase completes, ekaiX must generate a **Data Quality Report** as a visible artifact showing:
- Overall health score (%) for the connected gold layer
- Per-table quality scores (nullability %, uniqueness, pattern conformance, row counts)
- Per-column flagged issues (high null %, low cardinality, suspicious patterns)
- Clear pass/warn/fail indicators

This protects ekai from blame when a customer's source data is inaccurate. The user must see the quality of their own data before proceeding to requirements capture. The report appears as an artifact card in the chat and is viewable in the artifact panel.

### Artifact Types

The artifact panel must support these types (in priority order):
1. **ERD Graph** — Interactive node/edge diagram of tables and relationships
2. **Data Quality Report** — Post-discovery source data health assessment
3. **BRD Document** — Structured business requirements
4. **YAML Viewer** — Syntax-highlighted semantic view YAML
5. **Data Preview** — Table/grid of query results (good to have)
