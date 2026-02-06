# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Product Context

**ekai** is the parent product — an AI-powered data modeling platform. **ekai AIXcelerator (ekaiX)** is a separate, focused product variant built as a Snowflake Native App. When this document or conversations refer to "ekaiX" or "ekai Xcelerator", they mean this repository — not the full ekai platform.

ekaiX enables business users to create semantic models and Cortex Agents through AI conversation. It runs entirely within a customer's Snowflake account via SPCS (Snowpark Container Services). Users interact with an AI agent to discover gold layer databases, capture business requirements, generate semantic view YAML, and publish Cortex Agents to Snowflake Intelligence.

**Status:** Pre-development. Key design documents:

- `ekaiX_Technical_PRD.md` — Complete technical specification (2381 lines). Authoritative source for all requirements.
- `docs/plans/2026-02-04-ui-ux-design-spec.md` — Validated UI/UX design spec (every screen, component, interaction, and state). Always reference when building frontend.
- `docs/plans/tech-stack-research.md` — Verified 2026 package versions, breaking changes, install commands, and decision log for all dependencies.
- `docs/plans/postgresql-schema.sql` — Complete PostgreSQL 18 schema (9 tables, indexes, RLS policies, triggers, partitioning). Run directly to initialize the database.
- `docs/plans/api-specification.yaml` — OpenAPI 3.1 spec (30+ endpoints, full request/response schemas, SSE streaming, error codes). Use as the contract between frontend and backend.

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
- LLM provider is configurable via UI (`/llm-configuration`). Supported: Vertex AI, Snowflake Cortex, Azure OpenAI, Anthropic, OpenAI
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
**Playwright MCP is available** — use Playwright MCP tools for browser testing instead of installing Playwright manually.

Available MCP tools:
- `mcp__plugin_playwright_playwright__browser_navigate` — Navigate to URL
- `mcp__plugin_playwright_playwright__browser_snapshot` — Capture accessibility snapshot
- `mcp__plugin_playwright_playwright__browser_click` — Click elements
- `mcp__plugin_playwright_playwright__browser_type` — Type text
- `mcp__plugin_playwright_playwright__browser_take_screenshot` — Take screenshots
- Full suite of interaction tools for testing

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
5. **No synthetic or mock data.** Never use hardcoded, simulated, or synthetic data in any service. All data must come from real Snowflake queries using the actual Snowflake account. Mock data endpoints are not acceptable — always connect to Snowflake and query real databases, schemas, and tables via the Snowflake SDK. Snowflake credentials are stored in `sf.txt` (never committed) and loaded via environment variables.
6. **Single `.env` file in repository root.** All environment variables MUST be defined in a single `.env` file located in the repository root. Do NOT create separate `.env` files in `frontend/`, `backend/`, or `ai-service/` directories. Each service is configured to load from the root `.env` file. This ensures consistent configuration across all services and prevents credential duplication.
7. **No hardcoded configuration in code.** Never hardcode URLs, ports, credentials, API keys, file paths, or any configuration values in source code. All configuration must come from environment variables defined in the root `.env` file. Default values in code are acceptable ONLY as fallbacks for development convenience, and must be overridable via environment variables.

## Snowflake Connection (Local Dev)

For local development, connect to the real Snowflake account. Credentials are in `sf.txt`:
- **Account:** `lqb12348.us-east-1`
- **User:** `EKAIBA`
- **Warehouse:** `COMPUTE_WH`
- **Role:** `ACCOUNTADMIN`
- **Password:** Loaded from `sf.txt` or `SNOWFLAKE_PASSWORD` env var

The backend must use the `snowflake-sdk` package to execute real queries against Snowflake. The databases, schemas, and tables endpoints must return actual data from `SHOW DATABASES`, `INFORMATION_SCHEMA.SCHEMATA`, etc. — never hardcoded lists.

## Critical Implementation Notes

- **TypeScript strict mode** for all frontend/backend code. No `any` types.
- **Python type hints** for all AI service code.
- **Async by default** where applicable (FastAPI async endpoints, Redis async client).
- **No hardcoded values.** All config via environment variables.
- **Structured JSON logging** across all services.

## Environment Variables

A single `.env` file in the repository root contains ALL configuration for every service. Never create separate `.env` files in subdirectories.

**File location:** `/.env` (repository root)

### Service Ports
- `FRONTEND_PORT` — Next.js port (default: 3000)
- `BACKEND_PORT` — Node.js API port (default: 8000)
- `AI_SERVICE_PORT` — FastAPI AI service port (default: 8001)

### Environment
- `NODE_ENV` — development | production | test

### CORS
- `ALLOWED_CORS_ORIGINS` — Comma-separated list of allowed origins

### Frontend URLs
- `NEXT_PUBLIC_API_URL` — Backend API URL (exposed to browser)
- `NEXT_PUBLIC_WS_URL` — WebSocket URL (exposed to browser)

### Internal Service URLs
- `AI_SERVICE_URL` — AI service URL for backend-to-AI-service communication

### PostgreSQL
- `DATABASE_URL` — Full connection string
- `POSTGRES_PASSWORD` — Password for Docker Compose

### Neo4j
- `NEO4J_URI` — Bolt protocol URI
- `NEO4J_USER` — Username
- `NEO4J_PASSWORD` — Password

### Redis
- `REDIS_URL` — Full connection string with password
- `REDIS_PASSWORD` — Password for Docker Compose

### MinIO
- `MINIO_ENDPOINT` — Hostname
- `MINIO_PORT` — Port (default: 9000)
- `MINIO_ACCESS_KEY` — Access key
- `MINIO_SECRET_KEY` — Secret key
- `MINIO_USE_SSL` — true | false
- `MINIO_ROOT_USER` — Root user for Docker Compose
- `MINIO_ROOT_PASSWORD` — Root password for Docker Compose

### Snowflake
- `SNOWFLAKE_ACCOUNT` — Account identifier (e.g., `lqb12348.us-east-1`)
- `SNOWFLAKE_USER` — Username
- `SNOWFLAKE_PASSWORD` — Password/token (load from `sf.txt`, not hardcoded)
- `SNOWFLAKE_WAREHOUSE` — Warehouse name
- `SNOWFLAKE_DATABASE` — Default database (optional)
- `SNOWFLAKE_ROLE` — Role to assume

### LLM Provider (UI-Only Configuration)
LLM provider and model selection is configured exclusively via the UI at `/llm-configuration`. These settings are stored in PostgreSQL and restored on service startup. **Do NOT add LLM configuration to .env files.**

For Vertex AI: Set `GOOGLE_APPLICATION_CREDENTIALS` in your shell profile (not .env) pointing to your service account JSON file.

### Langfuse (Tracing)
- `LANGFUSE_SECRET_KEY` — Secret key
- `LANGFUSE_PUBLIC_KEY` — Public key
- `LANGFUSE_BASE_URL` — Base URL

### LangChain/LangSmith (Optional)
- `LANGCHAIN_TRACING_V2` — true | false
- `LANGCHAIN_API_KEY` — API key
- `LANGCHAIN_PROJECT` — Project name

### Connection Tuning (Optional)
- `PG_IDLE_TIMEOUT_MS` — PostgreSQL idle timeout
- `NEXT_PUBLIC_SSE_MAX_RETRIES` — SSE retry attempts (frontend, requires NEXT_PUBLIC_ prefix)
- `NEXT_PUBLIC_SSE_BASE_DELAY_MS` — SSE retry base delay (frontend, requires NEXT_PUBLIC_ prefix)
- `LLM_MAX_TOKENS` — Default max tokens for LLM calls
- `LLM_TEMPERATURE` — Default temperature for LLM calls
- `AGENT_RECURSION_LIMIT` — Max recursion depth for agent execution
- `SESSION_TTL_SECONDS` — Redis session TTL
- `CACHE_TTL_SECONDS` — Redis cache TTL

## Sensitive Files (DO NOT COMMIT)

- `.env` — Root environment file (contains all credentials)
- `images/gemini-key.json` — GCP credentials
- `images/gcp-credentials.json` — GCP service account
- `sf.txt` — Snowflake account credentials

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

## Agent Communication Guidelines (CRITICAL — Read Before Touching Any Prompt)

**The ICP is a business analyst, NOT a data engineer.** All agent output — especially the Discovery Agent — must be written for someone who understands their business domain but does NOT know SQL, database internals, or data engineering.

**Never show in agent chat:** UUIDs, tool names, SQL keywords (VARCHAR, TABLESAMPLE), database technology names (Neo4j, Redis), implementation details ("persisted to...", "sampled using..."), or raw profiling jargon (null_pct, PK/FK).

**Discovery Agent must use the [Analysis] → [Recognition] → [Question] → [Suggestion] pattern:**
- Analyze column/table names for semantic meaning FIRST (not just statistics)
- Recognize the business domain pattern
- Ask sharp, business-focused questions (NOT "What is this table?")
- Suggest an answer based on the data

**Discovery is CONVERSATIONAL, not a silent batch job.** The agent must engage the user between analysis steps to build shared understanding of the business domain (DAMA CDM: Conceptual → Logical → Physical). See PRD section "Discovery Agent Communication Guidelines" for full spec with good/bad examples.

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
