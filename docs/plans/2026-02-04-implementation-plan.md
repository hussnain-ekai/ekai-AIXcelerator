# ekaiX Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

> **Status (2026-02-07):** Phases 1-7 substantially complete. See `2026-02-07-implementation-progress.md` for detailed status, deviations from this plan, and remaining work.

**Goal:** Build ekaiX — a Snowflake Native App for creating semantic models and Cortex Agents through AI conversation.

**Architecture:** Three app services (Next.js frontend :3000, Fastify backend :8000, FastAPI AI service :8001) connecting to four data services (PostgreSQL :5432, Neo4j :7687, Redis :6379, MinIO :9000) running in Docker. AI service orchestrates 6 LangChain subagents via Deep Agents. All Snowflake queries use RCR (Restricted Caller's Rights). Deploys as Snowflake Native App via SPCS.

**Tech Stack:** Next.js 15 + MUI v7 + React 19 | Node.js 24 + Fastify 5 | Python 3.11 + FastAPI 0.128 + LangChain 1.2.8 + Deep Agents 0.3.9 + LangGraph 1.0.7 | PostgreSQL 18 + Neo4j 2025.x + Redis 8 + MinIO

**Reference docs (read before each task):**
- `ekaiX_Technical_PRD.md` — requirements
- `docs/plans/2026-02-04-ui-ux-design-spec.md` — every screen/component
- `docs/plans/tech-stack-research.md` — exact versions + breaking changes
- `docs/plans/postgresql-schema.sql` — complete schema (run directly)
- `docs/plans/api-specification.yaml` — OpenAPI 3.1 contract (27 endpoints)
- `v0/` — reference prototype (shadcn/Tailwind — convert to MUI)

---

## Phase 1: Local Dev Environment

### Task 1: Docker Compose + Environment

**Files:**
- Create: `docker-compose.yml`
- Create: `.env.example`
- Create: `frontend/.env.local`
- Create: `backend/.env`
- Create: `ai-service/.env`
- Create: `scripts/init-dev.sh`

**Step 1: Create docker-compose.yml**

Compose Specification format (NO `version:` key per Docker Compose v5). Four services:

| Service | Image | Ports | Key Config |
|---------|-------|-------|------------|
| postgresql | `postgres:18` | 5432:5432 | `POSTGRES_DB=ekaix`, `POSTGRES_USER=ekaix` |
| neo4j | `neo4j:2025-community` | 7474:7474, 7687:7687 | `NEO4J_AUTH=neo4j/password`, `NEO4J_PLUGINS=["apoc"]` |
| redis | `redis:8` | 6379:6379 | `--appendonly yes`, `--requirepass` from env |
| minio | `minio/minio:latest` | 9000:9000, 9001:9001 | `server /data --console-address :9001` |

All services: healthchecks, named volumes, shared `ekaix-net` network.

**Step 2: Create .env.example with all variables**

```bash
# PostgreSQL
POSTGRES_PASSWORD=changeme
DATABASE_URL=postgresql://ekaix:changeme@localhost:5432/ekaix

# Neo4j
NEO4J_AUTH=neo4j/changeme
NEO4J_URI=bolt://localhost:7687

# Redis
REDIS_PASSWORD=changeme
REDIS_URL=redis://:changeme@localhost:6379

# MinIO
MINIO_ROOT_USER=ekaix
MINIO_ROOT_PASSWORD=changeme123
MINIO_ENDPOINT=localhost
MINIO_PORT=9000

# Snowflake
SNOWFLAKE_ACCOUNT=
SNOWFLAKE_USER=
SNOWFLAKE_PASSWORD=
SNOWFLAKE_WAREHOUSE=
SNOWFLAKE_ROLE=

# AI Service
FASTAPI_URL=http://localhost:8001
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=
LANGCHAIN_PROJECT=ekaix

# Frontend
NEXT_PUBLIC_API_URL=http://localhost:8000
```

**Step 3: Create scripts/init-dev.sh**

Starts Docker Compose, waits for healthchecks, runs PG schema SQL, runs Neo4j constraint Cypher, creates MinIO buckets via `mc`.

**Step 4: Verify**

Run: `docker-compose up -d && docker-compose ps`
Expected: All 4 services healthy.

**Step 5: Commit**
```bash
git add docker-compose.yml .env.example scripts/
git commit -m "feat: add Docker Compose for data services + env templates"
```

### Task 2: PM2 Configuration

**Files:**
- Create: `pm2.config.js`

**Step 1: Create PM2 ecosystem file**

Three processes: `frontend` (Next.js :3000), `backend` (Fastify :8000), `ai-service` (uvicorn :8001). Each with `cwd`, `script`, `interpreter`, `env`, `watch` settings. AI service must use `./venv/bin/python` interpreter.

**Step 2: Commit**
```bash
git add pm2.config.js
git commit -m "feat: add PM2 ecosystem config for local dev"
```

---

## Phase 2: Backend Foundation

### Task 3: Initialize PostgreSQL Schema

**Files:**
- Existing: `docs/plans/postgresql-schema.sql` (run directly)

**Step 1: Run the schema**

Run: `docker exec -i ekaix-postgresql psql -U ekaix -d ekaix < docs/plans/postgresql-schema.sql`

This creates: 5 ENUM types, 10 tables (workspaces, data_products, data_product_shares, business_requirements, semantic_views, artifacts, uploaded_documents, data_quality_checks, audit_logs + 6 partitions), 25+ indexes (B-tree + GIN), RLS policies on all tables, 3 triggers (set_updated_at, auto_version_brd, auto_version_sv), 2 helper views (v_data_products_summary, v_shared_with_me), ekaix_app role with grants.

**Step 2: Verify**

Run: `docker exec ekaix-postgresql psql -U ekaix -d ekaix -c "\dt"`
Expected: All tables listed.

Run: `docker exec ekaix-postgresql psql -U ekaix -d ekaix -c "SELECT * FROM pg_policies;"`
Expected: RLS policies for each table.

### Task 4: Initialize Neo4j + Redis + MinIO

**Files:**
- Create: `scripts/init-neo4j.cypher`
- Create: `scripts/init-minio.sh`

**Step 1: Create Neo4j constraints**

```cypher
CREATE CONSTRAINT db_fqn IF NOT EXISTS FOR (d:Database) REQUIRE d.fqn IS UNIQUE;
CREATE CONSTRAINT schema_fqn IF NOT EXISTS FOR (s:Schema) REQUIRE s.fqn IS UNIQUE;
CREATE CONSTRAINT table_fqn IF NOT EXISTS FOR (t:Table) REQUIRE t.fqn IS UNIQUE;
CREATE CONSTRAINT column_fqn IF NOT EXISTS FOR (c:Column) REQUIRE c.fqn IS UNIQUE;
CREATE INDEX table_classification IF NOT EXISTS FOR (t:Table) ON (t.classification);
CREATE INDEX table_data_product IF NOT EXISTS FOR (t:Table) ON (t.data_product_id);
```

Run: `cat scripts/init-neo4j.cypher | docker exec -i ekaix-neo4j cypher-shell -u neo4j -p password`

**Step 2: Create MinIO buckets**

```bash
mc alias set ekaix http://localhost:9000 ekaix changeme123
mc mb ekaix/artifacts
mc mb ekaix/documents
mc mb ekaix/workspace
mc version enable ekaix/artifacts
mc version enable ekaix/documents
```

**Step 3: Verify Redis**

Run: `docker exec ekaix-redis redis-cli -a changeme PING`
Expected: `PONG`

**Step 4: Commit**
```bash
git add scripts/
git commit -m "feat: add Neo4j constraints, MinIO bucket init scripts"
```

---

## Phase 3: Node.js Backend API

### Task 5: Backend Scaffold

**Files:**
- Create: `backend/package.json`
- Create: `backend/tsconfig.json`
- Create: `backend/src/index.ts`
- Create: `backend/src/config.ts`
- Create: `backend/.env`

**Step 1: Initialize project and install deps**

```bash
cd backend && npm init -y
npm install fastify@5 @fastify/cors @fastify/rate-limit @fastify/multipart \
  zod@3 pg@8 redis@5 minio@8 neo4j-driver@5 snowflake-sdk pino \
  dotenv
npm install -D typescript@5 tsx @types/node @types/pg vitest
```

Ref: `docs/plans/tech-stack-research.md` for exact versions + breaking changes.

**tsconfig.json:** `strict: true`, `noUncheckedIndexedAccess: true`, `target: "ES2024"`, `module: "Node16"`.

**Step 2: Create config.ts**

Load all env vars with validation. No hardcoded values. Export typed config object.

**Step 3: Create src/index.ts**

Bootstrap Fastify with: CORS, rate limit, multipart, structured JSON logging (pino built-in). Register route modules. Listen on `config.PORT || 8000`. Graceful shutdown on SIGTERM/SIGINT.

**Step 4: Write test**

```typescript
// backend/src/__tests__/config.test.ts
import { describe, it, expect } from 'vitest';
// Test config loads required vars, throws on missing
```

**Step 5: Verify**

Run: `cd backend && npx tsx src/index.ts`
Expected: Server starts on port 8000, logs JSON.

Run: `curl http://localhost:8000/health`
Expected: 200

**Step 6: Commit**
```bash
git add backend/
git commit -m "feat: scaffold Node.js backend with Fastify 5"
```

### Task 6: Backend Database Services

**Files:**
- Create: `backend/src/services/postgresService.ts`
- Create: `backend/src/services/neo4jService.ts`
- Create: `backend/src/services/redisService.ts`
- Create: `backend/src/services/minioService.ts`
- Create: `backend/src/services/snowflakeService.ts`

Each service: typed singleton, connection pool, health check method, graceful close.

**Critical:** `postgresService` must execute `SET app.current_user = '{user}'` before EVERY query for RLS enforcement.

**neo4j-driver v6:** Use `executeRead`/`executeWrite` (old `readTransaction`/`writeTransaction` removed).

**node-redis:** Use `createClient()` (NOT ioredis). Redis 8 has built-in JSON support.

**Step 1: Write each service file**

**Step 2: Write tests**

```typescript
// backend/src/__tests__/services.test.ts
// Mock pg.Pool, neo4j.driver, redis.createClient, Minio.Client
// Test each service: connects, health check passes, graceful close
```

**Step 3: Verify**

Run: `cd backend && npx vitest run`
Expected: All tests pass.

**Step 4: Commit**
```bash
git add backend/src/services/
git commit -m "feat: add database service layer (pg, neo4j, redis, minio, snowflake)"
```

### Task 7: Backend Middleware

**Files:**
- Create: `backend/src/middleware/authMiddleware.ts`
- Create: `backend/src/middleware/errorHandler.ts`
- Create: `backend/src/middleware/requestLogger.ts`

**authMiddleware:** Extract `Sf-Context-Current-User` header. For local dev, also accept `X-Dev-User` header. 401 if missing (except `/health*`). Attach `request.user` string.

**errorHandler:** Catch-all. Map known error classes to HTTP codes. Return `{error, message, request_id}`. NEVER expose stack traces.

**requestLogger:** Already handled by Fastify's built-in pino. Configure `serializers`, `redact` sensitive fields.

**Rate limit:** 100 req/min general, 20 req/min for `/agent/*`.

**Step 1: Write middleware files**

**Step 2: Write tests**

```typescript
// Test auth: valid header → passes, missing header → 401, /health bypassed
// Test error: known error → correct code, unknown → 500 + no stack trace
```

**Step 3: Commit**
```bash
git add backend/src/middleware/
git commit -m "feat: add auth, error handling, rate limiting middleware"
```

### Task 8: Backend Routes — Health + Auth + Databases

**Files:**
- Create: `backend/src/routes/health.ts`
- Create: `backend/src/routes/auth.ts`
- Create: `backend/src/routes/databases.ts`
- Create: `backend/src/schemas/database.ts` (Zod)

**Endpoints (per api-specification.yaml):**

| Method | Path | Behavior |
|--------|------|----------|
| GET | `/health` | Returns `{status, services: {postgresql, neo4j, redis, minio, snowflake}}` |
| GET | `/auth/user` | Parse user from header, query Snowflake for role/account metadata |
| GET | `/databases` | `SHOW DATABASES` via Snowflake RCR → `{data: [{name, owner, created_on}]}` |
| GET | `/databases/{database}/schemas` | `INFORMATION_SCHEMA.SCHEMATA` → schema list |
| GET | `/databases/{database}/schemas/{schema}/tables` | `INFORMATION_SCHEMA.TABLES` + `.COLUMNS` → table+column metadata |
| POST | `/databases/{database}/reference` | REFERENCES callback for SPCS privilege grant |

**Step 1: Create Zod schemas for request/response validation**

**Step 2: Implement route handlers**

**Step 3: Write tests**

```typescript
// Mock snowflakeService for database queries
// Test: GET /health returns all service statuses
// Test: GET /databases returns list
// Test: GET /databases/:name/schemas returns schemas
```

**Step 4: Verify manually**

Run: `curl -H "X-Dev-User: testuser" http://localhost:8000/auth/user`

**Step 5: Commit**
```bash
git add backend/src/routes/ backend/src/schemas/
git commit -m "feat: add health, auth, database discovery routes"
```

### Task 9: Backend Routes — Data Products CRUD

**Files:**
- Create: `backend/src/routes/dataProducts.ts`
- Create: `backend/src/schemas/dataProduct.ts` (Zod)

**Endpoints (per api-specification.yaml):**

| Method | Path | Body | Response |
|--------|------|------|----------|
| GET | `/data-products` | query: page, per_page, search | `{data: DataProduct[], meta: {page, per_page, total, total_pages}}` |
| POST | `/data-products` | `{name, description?, database_name, schemas[]}` | `{data: {id, session_id, ...}}` |
| GET | `/data-products/{id}` | — | Full data product with state, artifacts, quality score |
| PUT | `/data-products/{id}` | `{name?, description?}` | Updated product |
| DELETE | `/data-products/{id}` | — | 204 (soft delete: set deleted_at) |
| POST | `/data-products/{id}/share` | `{user_email, permission}` | Share record |
| DELETE | `/data-products/{id}/share/{share_id}` | — | 204 |
| POST | `/data-products/{id}/health-check/{check_id}/acknowledge` | — | Updated check |
| POST | `/data-products/{id}/publish` | — | `{semantic_view_fqn, cortex_agent_fqn, url}` |

All queries use PostgreSQL RLS (set `app.current_user` per request). GET list joins `data_quality_checks` for health dot. Pagination format: `{data, meta: {page, per_page, total, total_pages}}`.

**Step 1: Create Zod schemas**

**Step 2: Implement all CRUD handlers**

**Step 3: Write tests**

```typescript
// Mock postgresService
// Test: list with pagination, search filter
// Test: create inserts into workspaces + data_products
// Test: workspace isolation (user A can't see user B)
// Test: share creates record in data_product_shares
// Test: delete sets deleted_at (soft delete)
```

**Step 4: Commit**
```bash
git add backend/src/routes/dataProducts.ts backend/src/schemas/dataProduct.ts
git commit -m "feat: add data product CRUD with workspace isolation"
```

### Task 10: Backend Routes — Agent (SSE Proxy)

**Files:**
- Create: `backend/src/routes/agent.ts`
- Create: `backend/src/schemas/agent.ts` (Zod)

**Endpoints:**

| Method | Path | Behavior |
|--------|------|----------|
| POST | `/agent/message` | `{session_id, message, attachments[]}` → proxy to AI service |
| GET | `/agent/stream/{session_id}` | SSE proxy from AI service. Headers: `Content-Type: text/event-stream`, `Cache-Control: no-cache` |
| POST | `/agent/interrupt` | `{session_id}` → cancel current execution |
| POST | `/agent/rollback/{checkpoint_id}` | Rollback to checkpoint |
| GET | `/agent/checkpoints` | `{session_id}` → list checkpoints |
| POST | `/agent/approve` | `{session_id, approved, feedback?}` → respond to approval_request |

**SSE streaming:** Open HTTP connection to AI service `/stream/{session_id}`, pipe events to client. Keepalive `:ping\n\n` every 15s. Handle client/server disconnect. Event types: `token`, `tool_call`, `tool_result`, `phase_change`, `artifact`, `approval_request`, `error`, `done`.

**Step 1: Implement SSE proxy with event piping**

**Step 2: Implement message/interrupt/rollback/approve handlers**

**Step 3: Write tests**

```typescript
// Mock HTTP to AI service
// Test: POST /agent/message proxies correctly
// Test: SSE stream pipes events
// Test: interrupt sends cancel to AI service
```

**Step 4: Commit**
```bash
git add backend/src/routes/agent.ts backend/src/schemas/agent.ts
git commit -m "feat: add agent message + SSE streaming proxy routes"
```

### Task 11: Backend Routes — Artifacts + Documents

**Files:**
- Create: `backend/src/routes/artifacts.ts`
- Create: `backend/src/routes/documents.ts`
- Create: `backend/src/schemas/document.ts` (Zod)

**Artifact endpoints:**

| Method | Path | Source |
|--------|------|--------|
| GET | `/artifacts?data_product_id=` | PostgreSQL `artifacts` table, grouped by phase |
| GET | `/artifacts/{id}` | Content from MinIO |
| GET | `/artifacts/{id}/export` | Download as file (YAML, JSON, PDF) |

**Document endpoints:**

| Method | Path | Behavior |
|--------|------|----------|
| POST | `/documents/upload` | Multipart (max 50MB). Store in MinIO `documents/uploads/`. Insert into `uploaded_documents`. |
| POST | `/documents/{id}/extract` | Trigger Cortex Document AI extraction |
| GET | `/documents/{id}/content` | Get extracted text |
| GET | `/documents?data_product_id=` | List documents |
| DELETE | `/documents/{id}` | Remove document + MinIO object |

**Step 1: Implement artifact routes (read from MinIO + PG)**

**Step 2: Implement document upload with @fastify/multipart**

**Step 3: Write tests**

**Step 4: Commit**
```bash
git add backend/src/routes/artifacts.ts backend/src/routes/documents.ts
git commit -m "feat: add artifact retrieval and document upload routes"
```

---

## Phase 4: FastAPI AI Service

### Task 12: AI Service Scaffold

**Files:**
- Create: `ai-service/requirements.txt`
- Create: `ai-service/pyproject.toml`
- Create: `ai-service/main.py`
- Create: `ai-service/config.py`
- Create: `ai-service/routers/health.py`
- Create: `ai-service/.env`

**Step 1: Create venv and install deps**

```bash
cd ai-service
python3 -m venv venv && source venv/bin/activate
```

**requirements.txt** (from tech-stack-research.md):
```
fastapi==0.128.0
uvicorn[standard]
pydantic==2.11.3
pydantic-settings
deepagents==0.3.9
langgraph==1.0.7
langchain==1.2.8
langchain-mcp-adapters==0.2.0
snowflake-connector-python[pandas]==4.2.0
snowflake-snowpark-python==1.44.0
neo4j==5.34.0
redis[hiredis]==5.2.1
minio==7.2.15
langsmith
httpx
python-dotenv
```

```bash
pip install -r requirements.txt
```

**Step 2: Create main.py with lifespan**

Use `lifespan` context manager (NOT deprecated `on_event`). Initialize all DB connections on startup, close on shutdown.

**Step 3: Create config.py**

Pydantic Settings class loading from `.env`.

**Step 4: Verify**

Run: `cd ai-service && source venv/bin/activate && uvicorn main:app --port 8001`
Expected: FastAPI running on :8001, Swagger docs at `/docs`.

**Step 5: Commit**
```bash
git add ai-service/
git commit -m "feat: scaffold FastAPI AI service with lifespan"
```

### Task 13: AI Service — Database Connections

**Files:**
- Create: `ai-service/services/snowflake.py`
- Create: `ai-service/services/neo4j.py`
- Create: `ai-service/services/redis.py`
- Create: `ai-service/services/minio.py`
- Create: `ai-service/services/postgres.py`

Each: async where possible, typed, health check method. Same patterns as backend but Python.

**Step 1: Implement each service**

**Step 2: Write tests**

```python
# ai-service/tests/test_services.py
# Mock each client, verify connect/disconnect/health
```

**Step 3: Commit**
```bash
git add ai-service/services/ ai-service/tests/
git commit -m "feat: add AI service database connections"
```

### Task 14: AI Service — LangChain Tools (Snowflake)

**Files:**
- Create: `ai-service/tools/snowflake_tools.py`
- Create: `ai-service/tests/test_snowflake_tools.py`

**7 tools:**

| Tool | Description | Key Detail |
|------|-------------|------------|
| `query_information_schema` | Query INFORMATION_SCHEMA for tables/columns/constraints | RCR via EXECUTE AS CALLER |
| `profile_table` | Statistical profiling (row count, nulls, cardinality, min/max, patterns) | HyperLogLog via HLL_ACCUMULATE/HLL_ESTIMATE |
| `execute_rcr_query` | Run arbitrary SELECT with RCR | Limit 1000 rows, 30s timeout |
| `create_semantic_view` | CREATE OR REPLACE SEMANTIC VIEW SQL | Via stored procedure |
| `create_cortex_agent` | CREATE CORTEX AGENT with semantic view ref | AI_COMPLETE (replaces SNOWFLAKE.CORTEX.COMPLETE) |
| `grant_agent_access` | GRANT USAGE ON CORTEX AGENT TO ROLE | |
| `validate_sql` | EXPLAIN plan check without execution | |

Use `@tool` decorator from LangChain 1.2.8. All tools return structured results.

**Step 1: Implement each tool**

**Step 2: Write tests (mock Snowflake connector)**

**Step 3: Commit**
```bash
git add ai-service/tools/snowflake_tools.py ai-service/tests/
git commit -m "feat: add 7 Snowflake LangChain tools"
```

### Task 15: AI Service — LangChain Tools (Neo4j + PostgreSQL + MinIO)

**Files:**
- Create: `ai-service/tools/neo4j_tools.py`
- Create: `ai-service/tools/postgres_tools.py`
- Create: `ai-service/tools/minio_tools.py`
- Create: `ai-service/tests/test_tools.py`

**Neo4j tools (4):** `query_erd_graph`, `update_erd` (MERGE-based), `get_relationship_path`, `classify_entity` (FACT/DIMENSION).

**PostgreSQL tools (5):** `save_workspace_state`, `load_workspace_state`, `save_brd` (auto-version trigger), `save_semantic_view` (auto-version trigger), `log_agent_action` (partitioned audit_logs).

**MinIO tools (3):** `upload_artifact`, `retrieve_artifact`, `list_artifacts`.

**Step 1: Implement all 12 tools**

**Step 2: Write tests**

**Step 3: Commit**
```bash
git add ai-service/tools/
git commit -m "feat: add Neo4j, PostgreSQL, MinIO LangChain tools (12 total)"
```

### Task 16: AI Service — CompositeBackend

**Files:**
- Create: `ai-service/backends/composite.py`
- Create: `ai-service/backends/state_backend.py`
- Create: `ai-service/backends/store_backend.py`
- Create: `ai-service/backends/filesystem_backend.py`
- Create: `ai-service/backends/neo4j_backend.py`

**CompositeBackend** combines:
- **StateBackend** (Redis): Active agent state, LangGraph checkpoints. Keys: `agent:session:{id}`, `checkpoint:{session}:{id}`
- **StoreBackend** (PostgreSQL): Persistent workspace state, BRD, semantic views
- **FilesystemBackend** (MinIO): Artifacts, documents. Paths: `{data_product_id}/erd/`, `/yaml/`, `/brd/`
- **Neo4jBackend**: ERD graph queries

Use Deep Agents 0.3.9 backend interfaces. Ref: tech-stack-research.md for API patterns.

**Step 1: Implement each backend**

**Step 2: Compose into CompositeBackend**

**Step 3: Write tests**

**Step 4: Commit**
```bash
git add ai-service/backends/
git commit -m "feat: add CompositeBackend (Redis + PG + MinIO + Neo4j)"
```

### Task 17: AI Service — Subagents + Orchestrator

**Files:**
- Create: `ai-service/agents/prompts.py`
- Create: `ai-service/agents/discovery.py`
- Create: `ai-service/agents/requirements.py`
- Create: `ai-service/agents/generation.py`
- Create: `ai-service/agents/validation.py`
- Create: `ai-service/agents/publishing.py`
- Create: `ai-service/agents/explorer.py`
- Create: `ai-service/agents/orchestrator.py`

**prompts.py:** All 6 subagent system prompts from PRD (lines 730-928). Plus orchestrator prompt.

**Each subagent** gets:
- System prompt (from PRD)
- Specific tool subset (see Task 14-15 for which tools each agent gets)
- Phase-appropriate behavior constraints

| Agent | Tools | Key Constraint |
|-------|-------|---------------|
| Discovery | snowflake: query_info_schema, profile_table. neo4j: update_erd, classify_entity. minio: upload_artifact | PK detection >98% uniqueness. FK inference biases false positive. Run 4 data quality checks. |
| Requirements | neo4j: query_erd_graph. snowflake: execute_rcr_query. pg: save_brd. minio: upload_artifact | Max 15 turns. One question at a time. Show actual data examples. |
| Generation | neo4j: query_erd_graph. pg: load_workspace_state, save_semantic_view. minio: upload_artifact | Fully qualified table names. Verify column existence via ERD. |
| Validation | snowflake: validate_sql, execute_rcr_query. pg: save_semantic_view. minio: upload_artifact | EXPLAIN plan validation. Check cardinality, nulls, ranges. |
| Publishing | snowflake: create_semantic_view, create_cortex_agent, grant_agent_access. pg: log_agent_action | Requires approval_request event before publishing. Append data quality disclaimer. |
| Explorer | snowflake: execute_rcr_query. neo4j: query_erd_graph. snowflake: profile_table | Available in any phase. Ad-hoc queries. |

**orchestrator.py:** Use `create_deep_agent` from Deep Agents 0.3.9:
```python
from deepagents import create_deep_agent, CompiledSubAgent

agent = create_deep_agent(
    model="snowflake-cortex/claude-4-sonnet",
    system_prompt=ORCHESTRATOR_PROMPT,
    subagents=[discovery, requirements, generation, validation, publishing, explorer],
    tools=[...],
    backend=composite_backend,
    context_schema=EkaiXContext,  # LangGraph 1.0.7 Context API
)
```

**Step 1: Create prompts.py with all system prompts**

**Step 2: Create each subagent with `CompiledSubAgent`**

**Step 3: Create orchestrator with `create_deep_agent`**

**Step 4: Write tests**

```python
# Test: orchestrator routes to correct subagent based on phase
# Test: each subagent has correct tool list
# Test: approval_request emitted before publishing
```

**Step 5: Commit**
```bash
git add ai-service/agents/
git commit -m "feat: add 6 subagents + orchestrator via Deep Agents"
```

### Task 18: AI Service — SSE Streaming Router

**Files:**
- Create: `ai-service/routers/agent.py`
- Create: `ai-service/models/schemas.py`

**Endpoints:**

| Method | Path | Behavior |
|--------|------|----------|
| POST | `/invoke` | Invoke agent with message, return final result |
| GET | `/stream/{session_id}` | SSE stream of agent execution |

**SSE event types:** `token`, `tool_call`, `tool_result`, `phase_change`, `artifact`, `approval_request`, `error`, `done`.

```python
@router.get("/stream/{session_id}")
async def stream(session_id: str):
    async def event_generator():
        async for event in agent.astream(session_id):
            yield f"event: {event.type}\ndata: {json.dumps(event.data)}\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

**Step 1: Create Pydantic v2 models in schemas.py**

**Step 2: Implement /invoke and /stream endpoints**

**Step 3: Write tests**

**Step 4: Commit**
```bash
git add ai-service/routers/ ai-service/models/
git commit -m "feat: add agent invoke + SSE streaming endpoints"
```

### Task 19: AI Service — Data Quality Scoring

**Files:**
- Modify: `ai-service/agents/discovery.py`
- Create: `ai-service/tools/quality_scoring.py`

**4 checks** (from UI/UX spec section 7.1):

| Check | Query | Deduction |
|-------|-------|-----------|
| Duplicate PKs in dimension tables | `SELECT pk, COUNT(*) ... HAVING COUNT(*) > 1` | -15/table |
| Orphaned FKs | `LEFT JOIN ... WHERE dim.pk IS NULL` | -10/relationship |
| Numeric as VARCHAR | Profile VARCHAR cols, >90% numeric | -5/column |
| Missing descriptions | COMMENT in INFORMATION_SCHEMA | -2/table |

Start 100, floor 0. Store in `data_quality_checks` table. Emit `artifact` event with type `data_quality_report`.

**Threshold gating:**
- 70-100 (green): auto-pass
- 40-69 (gold): emit `approval_request` requiring acknowledge
- 0-39 (red): emit `error` blocking progression

**Step 1: Implement quality scoring tool**

**Step 2: Integrate into Discovery agent flow**

**Step 3: Write tests**

**Step 4: Commit**
```bash
git add ai-service/tools/quality_scoring.py ai-service/agents/discovery.py
git commit -m "feat: add data quality scoring (4 checks, 3 thresholds)"
```

---

## Phase 5: Snowflake Stored Procedures

### Task 20: Stored Procedures

**Files:**
- Create: `stored_procedures/discover_schema.py`
- Create: `stored_procedures/profile_table.py`
- Create: `stored_procedures/validate_semantic_view.py`
- Create: `stored_procedures/publish_cortex_agent.py`

| Procedure | Execute As | Input | Returns |
|-----------|-----------|-------|---------|
| `discover_schema` | RESTRICTED CALLER | db_name, schema_names[] | VARIANT: tables, columns, constraints |
| `profile_table` | RESTRICTED CALLER | fqn | VARIANT: row_count, nulls, cardinality (HLL), min/max, patterns, samples |
| `validate_semantic_view` | RESTRICTED CALLER | yaml_string | VARIANT: pass/fail, issues[] |
| `publish_cortex_agent` | OWNER | yaml, agent_name, target_schema | VARIANT: agent_fqn, url, grants |

**Key:** `profile_table` must use `HLL_ACCUMULATE`/`HLL_ESTIMATE` for cardinality, `TABLESAMPLE` for sample values. Must handle 1M+ rows in <30s.

**Key:** `publish_cortex_agent` uses `EXECUTE AS OWNER` because it needs elevated privileges to CREATE objects. It grants back to caller's role.

**Step 1: Write each procedure as Python file**

**Step 2: Write SQL to register them as stored procedures**

**Step 3: Test against Snowflake account**

**Step 4: Commit**
```bash
git add stored_procedures/
git commit -m "feat: add 4 Snowflake stored procedures (RCR)"
```

---

## Phase 6: React Frontend

### Task 21: Frontend Scaffold + MUI Theme

**Files:**
- Create: `frontend/` (via create-next-app)
- Create: `frontend/src/theme/darkTheme.ts`
- Create: `frontend/src/theme/lightTheme.ts`
- Create: `frontend/src/theme/ThemeRegistry.tsx`

**Step 1: Create Next.js project**

```bash
npx create-next-app@15 frontend --typescript --app --src-dir --no-tailwind
```

**Step 2: Install MUI + deps**

```bash
cd frontend
npm install @mui/material@7 @emotion/react @emotion/styled @mui/icons-material@7 \
  @mui/material-nextjs @tanstack/react-query@6 zustand@5 \
  recharts reactflow react-syntax-highlighter
npm install -D @types/react-syntax-highlighter vitest @testing-library/react
```

**Step 3: Create MUI themes**

Use `createTheme` with `cssVariables: true` (MUI v7 pattern). Dark theme (from CLAUDE.md):
- bg: `#1A1A1E`, sidebar: `#131316`, card: `#252528`, border: `#3A3A3E`
- primary: `#D4A843` (gold), text: `#F5F5F5`, secondary text: `#9E9E9E`
- success: `#4CAF50`

Light theme (from UI/UX spec section 11):
- bg: `#FFFFFF`, sidebar: `#F5F5F5`, card: `#FAFAFA`, border: `#E0E0E0`
- primary: `#D4A843` (same), text: `#1A1A1E`, secondary text: `#666666`

**Step 4: Create ThemeRegistry.tsx**

`'use client'` component wrapping `AppRouterCacheProvider` + `ThemeProvider`. Use `@mui/material-nextjs` for SSR integration.

**Step 5: Update layout.tsx**

Wrap with `ThemeRegistry`, `QueryClientProvider`.

**Step 6: Verify**

Run: `cd frontend && npm run build && npm run dev`
Expected: Blank page with correct dark background at localhost:3000.

**Step 7: Commit**
```bash
git add frontend/
git commit -m "feat: scaffold Next.js 15 + MUI v7 with ekai brand theme"
```

### Task 22: Frontend — Global Layout + Sidebar

**Files:**
- Create: `frontend/src/components/layout/AppSidebar.tsx`
- Create: `frontend/src/components/layout/NavItem.tsx`
- Create: `frontend/src/components/layout/UserFooter.tsx`
- Modify: `frontend/src/app/layout.tsx`

**AppSidebar** (from UI/UX spec section 1):
- MUI `Drawer` (permanent variant), 240px width, `#131316` bg
- Logo: "ekai" text + gold hexagon, top-left
- 3 nav items: Data Products (StorageRounded), User Management (PeopleRounded), LLM Configuration (SettingsRounded)
- Active: gold text `#D4A843` + 3px gold left border
- UserFooter: gold Avatar with initials, email (truncated), IconButton (MoreVert) with Menu dropdown

**Reference:** `v0/components/app-sidebar.tsx` for structure (convert shadcn→MUI).

**Step 1: Implement AppSidebar with MUI Drawer**

**Step 2: Implement NavItem with active state**

**Step 3: Implement UserFooter with Menu**

**Step 4: Verify visually at localhost:3000**

**Step 5: Commit**
```bash
git add frontend/src/components/layout/
git commit -m "feat: add app sidebar with navigation + user footer"
```

### Task 23: Frontend — Dashboard Page

**Files:**
- Create: `frontend/src/app/data-products/page.tsx`
- Create: `frontend/src/components/dashboard/DataProductTable.tsx`
- Create: `frontend/src/components/dashboard/StatusBadge.tsx`
- Create: `frontend/src/components/dashboard/CollaboratorAvatars.tsx`
- Create: `frontend/src/components/dashboard/HealthDot.tsx`
- Create: `frontend/src/hooks/useDataProducts.ts`

**DataProductTable** (from UI/UX spec section 2):
- MUI Table: columns = Name, Database (monospace), Status (Chip pill), Last Updated, Owner, Collaborators, Health dot, Actions (IconButton + Menu)
- StatusBadge: MUI Chip outlined. Published=green, Draft/Discovery/In Progress=gold
- CollaboratorAvatars: Overlapping MUI Avatars (28px, gold border, max 3 + "+N")
- HealthDot: green (70+), gold (40-69), red (<40), none (no discovery)
- Pagination: "Rows per page" Select + "1-5 of 5" + prev/next IconButtons
- Row click → navigate to `/data-products/:id`
- Header: "Manage Data Products" h1 + Search TextField + "Create Data Product" Button (gold)

**useDataProducts:** TanStack Query hook calling `GET /data-products` with pagination.

**Reference:** `v0/components/dashboard.tsx` for structure.

**Step 1: Create page.tsx as client component**

**Step 2: Implement DataProductTable**

**Step 3: Implement StatusBadge, CollaboratorAvatars, HealthDot**

**Step 4: Implement useDataProducts hook**

**Step 5: Verify visually (with mock data initially)**

**Step 6: Commit**
```bash
git add frontend/src/app/data-products/ frontend/src/components/dashboard/ frontend/src/hooks/
git commit -m "feat: add dashboard with data product table, badges, pagination"
```

### Task 24: Frontend — Create Data Product Modal

**Files:**
- Create: `frontend/src/components/dashboard/CreateDataProductModal.tsx`
- Create: `frontend/src/hooks/useDatabases.ts`

**2-step MUI Dialog** (~500px, from UI/UX spec section 3):

**Step 1 — Name & Description:**
- Step indicator: 2 dots (gold=active, gray=inactive)
- Title: "Create Data Product", subtitle: "Start building your semantic model"
- Name TextField (required *), Description TextField multiline
- Cancel (outlined) + Next (gold, disabled until name)

**Step 2 — Select Data Source:**
- Title: "Select Data Source"
- Database Select (options from `GET /databases`)
- Schema Checkboxes (appear after DB select, pre-select non-PUBLIC, gold checkmarks)
- Back (outlined) + Create (gold, disabled until db + schema selected)
- On create: `POST /data-products`, navigate to `/data-products/:id`

**Reference:** `v0/components/create-data-product-modal.tsx`

**Step 1: Implement 2-step modal**

**Step 2: Implement useDatabases hook**

**Step 3: Verify**

**Step 4: Commit**
```bash
git add frontend/src/components/dashboard/CreateDataProductModal.tsx frontend/src/hooks/useDatabases.ts
git commit -m "feat: add 2-step create data product modal"
```

### Task 25: Frontend — Chat Workspace

**Files:**
- Create: `frontend/src/app/data-products/[id]/page.tsx`
- Create: `frontend/src/components/chat/ChatWorkspace.tsx`
- Create: `frontend/src/components/chat/PhaseStepper.tsx`
- Create: `frontend/src/components/chat/MessageThread.tsx`
- Create: `frontend/src/components/chat/AgentMessage.tsx`
- Create: `frontend/src/components/chat/UserMessage.tsx`
- Create: `frontend/src/components/chat/ArtifactCard.tsx`
- Create: `frontend/src/components/chat/ChatInput.tsx`
- Create: `frontend/src/components/chat/StreamingResponse.tsx`

**Layout** (from UI/UX spec section 4):
- Breadcrumb: "Data Products" (gold link) > "{name}" (white) + Artifacts button (right)
- PhaseStepper: 5 steps (Discovery→Publishing), custom circles+lines (NOT MUI Stepper). Gold=completed/current, gray=future.
- MessageThread: scrollable, auto-scroll on new messages
- AgentMessage: left-aligned, "ekaiX" gold label, dark bubble `#252528`, ~70% max-width, markdown support, inline ArtifactCards
- UserMessage: right-aligned, dark bubble
- ArtifactCard: gold 3px left border, icon + title + description, clickable
- ChatInput: paperclip IconButton + TextField "Ask ekaiX anything..." + Send IconButton (gold)
- StreamingResponse: token-by-token with blinking cursor

**Reference:** `v0/components/chat-workspace.tsx`

**Step 1: Create ChatWorkspace layout**

**Step 2: Create PhaseStepper (custom component)**

**Step 3: Create message components (Agent, User, Artifact)**

**Step 4: Create ChatInput with file upload trigger**

**Step 5: Verify visually with mock conversation**

**Step 6: Commit**
```bash
git add frontend/src/app/data-products/[id]/ frontend/src/components/chat/
git commit -m "feat: add chat workspace with messages, stepper, input"
```

### Task 26: Frontend — SSE Integration (useAgent Hook)

**Files:**
- Create: `frontend/src/hooks/useAgent.ts`
- Create: `frontend/src/lib/sse.ts`
- Create: `frontend/src/lib/api.ts`
- Create: `frontend/src/stores/chatStore.ts`
- Create: `frontend/src/stores/authStore.ts`
- Create: `frontend/src/stores/themeStore.ts`

**useAgent hook:**
- Connect to `/agent/stream/:sessionId` via EventSource
- Parse SSE events:
  - `token` → accumulate into streaming message in chatStore
  - `tool_call` / `tool_result` → show in agent message
  - `phase_change` → update PhaseStepper
  - `artifact` → add to chatStore.artifacts + show ArtifactCard
  - `approval_request` → show confirmation dialog
  - `error` → show error message
  - `done` → finalize message
- Reconnection with exponential backoff
- Cleanup on unmount

**api.ts:** Fetch wrapper with `Sf-Context-Current-User` header (or `X-Dev-User` for local dev).

**Zustand stores:**
- `chatStore`: messages[], isStreaming, currentPhase, sessionId, artifacts[]
- `authStore`: user, snowflakeRole, account
- `themeStore`: mode ('dark'|'light'), toggle()

**Step 1: Create api.ts and sse.ts**

**Step 2: Create Zustand stores**

**Step 3: Implement useAgent hook**

**Step 4: Wire into ChatWorkspace**

**Step 5: Test with running backend + AI service**

**Step 6: Commit**
```bash
git add frontend/src/hooks/ frontend/src/lib/ frontend/src/stores/
git commit -m "feat: add SSE integration, Zustand stores, useAgent hook"
```

### Task 27: Frontend — Artifact Panels

**Files:**
- Create: `frontend/src/components/panels/ArtifactsPanel.tsx`
- Create: `frontend/src/components/panels/ERDDiagramPanel.tsx`
- Create: `frontend/src/components/panels/DataQualityReport.tsx`
- Create: `frontend/src/components/panels/YAMLViewer.tsx`
- Create: `frontend/src/components/panels/DataPreview.tsx`

All panels: MUI `Drawer` (anchor=right, temporary variant).

**ArtifactsPanel** (~380px, from UI/UX spec section 5):
- Header: "Artifacts" + "N generated" + Close IconButton
- Cards grouped by phase: DISCOVERY (ERD, Quality), REQUIREMENTS (BRD), GENERATION (YAML), VALIDATION (Preview)
- Each card: gold left border, icon + name + description + timestamp

**ERDDiagramPanel** (~500px, from UI/UX spec section 6):
- Legend: Fact (gold border) / Dimension (green border)
- Fact tables: gold left border cards, full width stacked
- Dimension tables: green left border, 2-column grid
- Footer: "N relationships detected"
- Use ReactFlow for interactive graph (stretch goal)

**DataQualityReport** (~500px, from UI/UX spec section 7.5):
- Donut chart (recharts): color-coded ring, score in center
- Summary: "N of M tables meet threshold"
- Collapsible sections by check type
- Per-table summary table (color-coded scores)

**YAMLViewer:** react-syntax-highlighter with copy button.

**DataPreview:** MUI Table with sample results + export button.

**Reference:** `v0/components/artifact-panel.tsx`

**Step 1: Implement each panel**

**Step 2: Wire panel opens to ArtifactCard clicks**

**Step 3: Verify visually**

**Step 4: Commit**
```bash
git add frontend/src/components/panels/
git commit -m "feat: add artifact panels (artifacts, ERD, quality, YAML, preview)"
```

### Task 28: Frontend — Data Quality Modal

**Files:**
- Create: `frontend/src/components/panels/DataQualityModal.tsx`

**MUI Dialog** (from UI/UX spec section 7.3):
- Donut chart with large score in center
- Top 3 issues in **plain English** (not technical jargon)
- **Score 70+:** No checkbox, Continue enabled
- **Score 40-69:** Checkbox "I understand these issues may affect accuracy..." required. POST `/data-products/:id/health-check/:checkId/acknowledge` on proceed.
- **Score 0-39:** No Continue button, only "Go Back" + "Contact your data team"
- "View Full Report" button → opens DataQualityReport panel
- If 40-69 acknowledged: persistent gold Banner at top of chat workspace

**UI integration points** (from spec section 7.6):
1. Dashboard: HealthDot on each row (already in Task 23)
2. Chat: gold warning banner if 40-69 acknowledged
3. Publishing: disclaimer appended to Cortex Agent system prompt

**Step 1: Implement modal with 3 threshold behaviors**

**Step 2: Wire to `phase_change` and `approval_request` SSE events**

**Step 3: Verify visually**

**Step 4: Commit**
```bash
git add frontend/src/components/panels/DataQualityModal.tsx
git commit -m "feat: add data quality modal with threshold gating"
```

### Task 29: Frontend — User Management + LLM Configuration

**Files:**
- Create: `frontend/src/app/user-management/page.tsx`
- Create: `frontend/src/components/user-management/ProfileCard.tsx`
- Create: `frontend/src/components/user-management/PreferencesCard.tsx`
- Create: `frontend/src/app/llm-configuration/page.tsx`
- Create: `frontend/src/components/llm-configuration/ProviderRadioCards.tsx`

**User Management** (from UI/UX spec section 8):
- ProfileCard: gold Avatar (56px) + Name + Email + Snowflake role/account Chips
- PreferencesCard: Appearance Switch (dark/light), Email Notifications Switch, Default Rows Select (5/10/25), Save Button (gold)

**LLM Configuration** (from UI/UX spec section 9):
- RadioGroup with 3 Cards (gold border on selected):
  1. Snowflake Cortex — "RECOMMENDED" gold Chip, green status
  2. Enterprise Cloud — GCP Vertex AI, Azure OpenAI
  3. Public APIs — OpenAI, Anthropic (show API key TextField when selected)

**Reference:** `v0/components/user-management.tsx`, `v0/components/llm-configuration.tsx`

**Step 1: Implement both pages**

**Step 2: Wire theme toggle to themeStore**

**Step 3: Verify visually**

**Step 4: Commit**
```bash
git add frontend/src/app/user-management/ frontend/src/app/llm-configuration/ \
  frontend/src/components/user-management/ frontend/src/components/llm-configuration/
git commit -m "feat: add user management + LLM configuration pages"
```

---

## Phase 7: End-to-End Integration

### Task 30: Integration Testing

**Files:**
- Create: `tests/e2e/discovery.spec.ts`
- Create: `tests/e2e/full-workflow.spec.ts`

**Test the complete flow** (from UI/UX spec section 10, 23 steps):

1. Dashboard → Create Data Product modal → name + database + schemas
2. Chat workspace loads → agent welcome message
3. Discovery: profiling → ERD artifact → data quality report artifact
4. Data quality modal → acknowledge (or block)
5. Requirements: 15 turns max → BRD artifact
6. Generation: → YAML artifact
7. Validation: → preview artifact
8. Publishing: approval_request → confirm → success with FQN + URL

**Additional tests:**
- RCR: 2 Snowflake roles see different data
- Workspace isolation: User A can't see User B's products
- Error: Cortex unavailable → graceful degradation
- Error: network interruption → SSE reconnect
- Share/unshare data product
- Delete data product (soft delete)
- Document upload + extraction

**Step 1: Write Playwright E2E specs**

**Step 2: Run against local stack**

Run: `npx playwright test`

**Step 3: Fix failures**

**Step 4: Commit**
```bash
git add tests/
git commit -m "feat: add E2E integration tests for full workflow"
```

---

## Phase 8: SPCS Packaging

### Task 31: Dockerfiles

**Files:**
- Create: `spcs_services/frontend/Dockerfile`
- Create: `spcs_services/backend/Dockerfile`
- Create: `spcs_services/ai-service/Dockerfile`
- Create: `spcs_services/postgresql/Dockerfile`
- Create: `spcs_services/neo4j/Dockerfile`
- Create: `spcs_services/redis/Dockerfile`
- Create: `spcs_services/minio/Dockerfile`

All: multi-stage builds, non-root USER, HEALTHCHECK, SIGTERM handling, no secrets in layers.

| Service | Base | Build | Target Size |
|---------|------|-------|-------------|
| frontend | node:24-alpine | `npm run build` → standalone | <200MB |
| backend | node:24-alpine | `npm run build` → compiled JS | <150MB |
| ai-service | python:3.12-slim | pip install + copy | <500MB |
| postgresql | postgres:18 | + init scripts | stock |
| neo4j | neo4j:2025-community | + APOC + init | stock |
| redis | redis:8 | + config | stock |
| minio | minio/minio:latest | + init script | stock |

**Step 1: Write each Dockerfile**

**Step 2: Build and test locally**

Run: `docker build -t ekaix-frontend spcs_services/frontend/`
Run: `docker build -t ekaix-backend spcs_services/backend/`

**Step 3: Commit**
```bash
git add spcs_services/
git commit -m "feat: add SPCS Dockerfiles for all 7 services"
```

### Task 32: SPCS Service Specs + Application Package

**Files:**
- Create: `spcs_services/{service}/spec.yaml` (7 files)
- Create: `manifest.yml`
- Create: `setup_script.sql`

**spec.yaml** for each service: image path (`/ekaix_db/ekaix_schema/ekaix_repo/{service}:latest`), env vars, resource requests/limits, readiness probe, endpoints.

**manifest.yml:** version, required privileges, setup_script reference, REFERENCES callback.

**setup_script.sql:** CREATE APPLICATION ROLE, CREATE SCHEMA, stored procedure definitions, SPCS service creation, GRANT statements.

**Step 1: Write all spec.yaml files**

**Step 2: Write manifest.yml + setup_script.sql**

**Step 3: Validate by deploying to test Snowflake account**

**Step 4: Commit**
```bash
git add spcs_services/ manifest.yml setup_script.sql
git commit -m "feat: add SPCS service specs + application package"
```

---

## Phase 9-11: Documentation, Production, Verification

### Task 33: Documentation

**Files:**
- Create: `README.md`
- Create: `docs/user-guide.md`
- Create: `docs/admin-guide.md`

- README: quick start, architecture diagram, dev setup
- User guide: installation, first data product, publishing
- Admin guide: deployment, config, monitoring

### Task 34: Production Hardening

- Input validation on ALL endpoints (Zod + Pydantic)
- SQL injection: parameterized queries only
- XSS: React escapes by default; sanitize with DOMPurify if rendering raw HTML
- Rate limiting: configured in middleware
- Security headers
- Dependency vulnerability scanning
- Connection pooling optimized
- Circuit breaker for Snowflake/Cortex
- Graceful shutdown in all services
- Structured JSON logging with request ID correlation

### Task 35: Final Verification

Run full checklist from PRD Phase 11:
- [ ] Install from Snowflake Marketplace
- [ ] Full workflow <35 min
- [ ] UI loads <2s, agent first token <3s
- [ ] Discovery <10 min for 100 tables
- [ ] 50 concurrent users
- [ ] Workspace isolation
- [ ] RCR across roles
- [ ] Data quality gate enforced

---

## Dependency Graph

```
Task 1 (Docker) ──┬── Task 3 (PG Schema) ──┬── Task 5 (Backend Scaffold) ──→ Tasks 6-11
                   ├── Task 4 (Neo4j/Redis) ┘
                   └── Task 2 (PM2)

Task 5 ──→ Task 6 (DB Services) ──→ Task 7 (Middleware) ──→ Task 8 (Health/Auth/DB)
        ──→ Task 9 (Data Products) ──→ Task 10 (Agent SSE) ──→ Task 11 (Artifacts/Docs)

Task 1 ──→ Task 12 (AI Scaffold) ──→ Task 13 (DB Connections) ──→ Tasks 14-15 (Tools)
        ──→ Task 16 (Backends) ──→ Task 17 (Agents) ──→ Task 18 (SSE) ──→ Task 19 (Quality)

Task 20 (Stored Procs) ── parallel with Tasks 12-19

Task 1 ──→ Task 21 (Frontend Scaffold) ──→ Task 22 (Layout) ──→ Tasks 23-29 (Pages)

Task 26 (SSE Hook) depends on Tasks 10, 18 (backend+AI SSE endpoints)

Task 30 (Integration) depends on all of Tasks 1-29
Task 31-32 (SPCS) depends on Tasks 1-29
Tasks 33-35 (Docs/Prod/Verify) depend on Tasks 1-32
```

**Parallelizable groups:**
- Backend (Tasks 5-11) and AI Service (Tasks 12-19) can build in parallel
- Frontend (Tasks 21-29) can start once scaffolds exist, but SSE integration (Task 26) needs backend+AI
- Stored Procedures (Task 20) are independent of app services
