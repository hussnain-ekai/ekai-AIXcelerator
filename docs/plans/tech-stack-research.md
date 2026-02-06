# ekaiX Technology Stack Research (February 2026)

This document captures verified package versions, breaking changes, and API patterns for all ekaiX dependencies. All versions were researched on February 4, 2026.

---

## Quick Reference

| Layer | Package | Version | Key Note |
|-------|---------|---------|----------|
| **Frontend** | Next.js | **15.x (latest)** | MUI incompatible with 16 ([#47109](https://github.com/mui/material-ui/issues/47109)) |
| | React | **19.2.4** | Security patch (Jan 26, 2026) |
| | MUI Material | **7.3.7** | Emotion default; Pigment CSS still alpha |
| | @emotion/react | latest | Required by MUI v7 |
| | @mui/material-nextjs | latest | App Router SSR integration |
| **Backend** | Node.js | **24 LTS** (24.13.0) | V8 13.6, `using`/`await using`, npm 11 |
| | Fastify | **5.7.4** | Full JSON Schema required |
| | neo4j-driver | **6.0.1** | `executeRead`/`executeWrite` (old APIs removed) |
| | redis (node-redis) | **4.x+** | Officially recommended over ioredis |
| | minio | **8.0.6** | Or use `@aws-sdk/client-s3` |
| **AI Service** | Python | **3.11** (Snowpark target) | 3.10+ required by LangChain |
| | LangChain | **1.2.8** | `create_agent` replaces `create_react_agent` |
| | Deep Agents | **0.3.9** | `create_deep_agent` factory |
| | LangGraph | **1.0.7** | Context API replaces `config["configurable"]` |
| | langchain-mcp-adapters | **0.2.0** | Multimodal, elicitation support |
| | FastAPI | **0.128.0** | Python 3.9+, Pydantic v2 integrated |
| | snowflake-snowpark-python | **1.44.0** | `execute_as="restricted caller"` GA |
| | snowflake-connector-python | **4.2.0** | |
| **Data Services** | PostgreSQL | **18.1** | UUIDv7, virtual generated columns, AIO |
| | Neo4j | **2025.x** | CalVer; or 5.26 LTS |
| | Redis | **8.4.0** | 87% faster, built-in JSON/vectors |
| | MinIO | **RELEASE.2025-10-15** | Source-only; use Docker image |
| **Tooling** | Docker Compose | **v5.0.2** | No `version:` key needed |
| | PM2 | **6.0.14** | AGPL — dev only, not for SPCS |
| | TypeScript | **5.1+** | Required by Next.js 15 |

---

## 1. Frontend

### Next.js 15 (NOT 16)

**Why 15 over 16:** MUI's `AppRouterCacheProvider` does not support Next.js 16 yet. Tracked in [GitHub Issue #47109](https://github.com/mui/material-ui/issues/47109). Once MUI ships a compatible adapter, migration to 16 is straightforward via `npx @next/codemod@canary upgrade latest`.

**Install:**
```bash
npm install next@15 react@latest react-dom@latest
```

**Key patterns for ekaiX:**
- App Router is the only routing model (Pages Router receives no new features)
- `cookies()`, `headers()`, `params`, `searchParams` are async in v15 (must `await`)
- React 19 features available: `useActionState`, `useOptimistic`, `use`
- SSE streaming via `ReadableStream` for agent responses

### MUI v7.3.7

**Install:**
```bash
npm install @mui/material@latest @emotion/react @emotion/styled @mui/material-nextjs
```

**Theming setup (cssVariables mode):**
```typescript
'use client';
import { createTheme } from '@mui/material/styles';

const theme = createTheme({
  cssVariables: true,
  colorSchemes: { dark: true },
  palette: {
    primary: { main: '#D4A843' },  // ekai gold
  },
});
```

**Key changes from v5:**
- `createMuiTheme` removed → use `createTheme`
- `Grid2` renamed to `Grid` (old `Grid` → `GridLegacy`)
- `TransitionComponent`/`TransitionProps` → `slots`/`slotProps`
- `Hidden` component removed → use responsive CSS / `sx` prop
- Deep imports broken by `exports` field enforcement

**Pigment CSS status:** Alpha (v0.0.30). NOT production-ready. Use Emotion (default).

**Next.js App Router integration:**
- `AppRouterCacheProvider` wraps `<body>` in `layout.tsx`
- `ThemeProvider` in a separate `'use client'` component
- All MUI components ship with `"use client"` directive

### React 19.2.4

**Install:**
```bash
npm install react@latest react-dom@latest
npm install -D @types/react @types/react-dom
```

**Relevant new APIs:**

| Hook / API | Description |
|------------|-------------|
| `useActionState` | Form action lifecycle (pending state, result, errors) |
| `useOptimistic` | Instant UI feedback during async operations |
| `use` | Read resolved Promise/Context inside components |
| `useEffectEvent` | Callbacks that see latest props/state without re-triggering Effects |
| `<Activity>` | `mode="visible"/"hidden"` — pauses effects, defers updates, preserves state |

**Security note:** v19.2.4 includes DoS mitigations for Server Actions. All React 19.x users should upgrade.

### Combined Frontend Install

```bash
npm install next@15 react@latest react-dom@latest \
  @mui/material@latest @emotion/react @emotion/styled \
  @mui/material-nextjs @mui/icons-material
npm install -D @types/react @types/react-dom typescript
```

---

## 2. Backend (Node.js)

### Node.js 24 LTS

**Key features over 20/22:**
- V8 13.6 engine
- `using` / `await using` for automatic resource cleanup (DB connections, file handles)
- npm 11 with `--ignore-scripts` applying to all lifecycle scripts
- Stable Permission Model (`--permission`)
- `RegExp.escape()`, `Float16Array`, `Error.isError()`
- OpenSSL 3.5

### Fastify 5.7.4

**Install:**
```bash
npm install fastify@5
npm install -D typescript @types/node
npm install @fastify/type-provider-json-schema-to-ts
```

**Breaking changes from v4:**

| Change | Action Required |
|--------|----------------|
| Full JSON Schema required | Add `type` property to all route schemas |
| Logger split | Use `loggerInstance` for custom Pino logger |
| No callback+promise mixing | Async plugins must `return`, not call `done()` |
| `req.hostname` no longer includes port | Use `req.host` for hostname+port |
| Type provider split | `ValidatorSchema` and `SerializerSchema` are separate types |
| DELETE with empty body rejected | No `Content-Type: application/json` on empty DELETE |

**Pattern:**
```typescript
import Fastify from 'fastify';

const app = Fastify({
  loggerInstance: customPinoLogger,  // NOT logger
});

app.post('/data', {
  schema: {
    body: {
      type: 'object',  // REQUIRED in v5
      properties: { name: { type: 'string' } },
      required: ['name']
    }
  }
}, async (request, reply) => {
  return { ok: true };
});
```

### Neo4j Driver v6.0.1

**Install:**
```bash
npm install neo4j-driver@6
```

**Removed APIs:**
- `.readTransaction()` → use `.executeRead()`
- `.writeTransaction()` → use `.executeWrite()`
- `.lastBookmark()` → use `.lastBookmarks()`

**Pattern:**
```typescript
import neo4j from 'neo4j-driver';

const driver = neo4j.driver('neo4j://localhost:7687', neo4j.auth.basic('neo4j', 'password'));

// Preferred: driver-level query
const result = await driver.executeQuery(
  'MATCH (n:Table {name: $name}) RETURN n',
  { name: 'users' },
  { database: 'neo4j' }
);

// Session-based
const session = driver.session({ database: 'neo4j' });
const txResult = await session.executeRead(async (tx) => {
  return tx.run('MATCH (n) RETURN count(n) AS count');
});
await session.close();
```

### Redis Client (node-redis)

**Install:**
```bash
npm install redis  # node-redis — officially recommended
```

**Why node-redis over ioredis:**
- ioredis is now in maintenance-only mode
- node-redis supports Redis 8 features (JSON, vectors, time series)
- Performance comparable; slightly better for concurrent workloads
- If we need Cluster/Sentinel in SPCS, re-evaluate (ioredis has better support there)

**Pattern:**
```typescript
import { createClient } from 'redis';

const client = createClient({ url: 'redis://localhost:6379' });
await client.connect();

// Basic
await client.set('key', 'value', { EX: 3600 });

// JSON (Redis 8 built-in)
await client.json.set('user:1', '$', { name: 'test', role: 'admin' });
const user = await client.json.get('user:1');
```

### MinIO Client

**Install (pick one):**
```bash
npm install minio@8           # MinIO SDK
npm install @aws-sdk/client-s3  # AWS SDK (portable)
```

**MinIO SDK pattern:**
```typescript
import * as Minio from 'minio';

const client = new Minio.Client({
  endPoint: 'localhost', port: 9000, useSSL: false,
  accessKey: 'minioadmin', secretKey: 'minioadmin',
});

await client.putObject('artifacts', 'path/file.yaml', buffer);
const stream = await client.getObject('artifacts', 'path/file.yaml');
```

### Combined Backend Install

```bash
npm install fastify@5 neo4j-driver@6 redis minio@8 pino \
  @fastify/cors @fastify/websocket @fastify/multipart \
  @fastify/type-provider-json-schema-to-ts
npm install -D typescript @types/node
```

---

## 3. AI Service (Python)

### LangChain 1.2.8

**Breaking changes from 0.3:**

| Change | Details |
|--------|---------|
| Python 3.10+ required | 3.9 and below dropped |
| `create_agent` replaces `create_react_agent` | Import from `langchain.agents` |
| `prompt` → `system_prompt` parameter | In `create_agent` |
| State schemas: TypedDict only | Pydantic models and dataclasses not supported |
| LCEL pipe chains deprecated | Agents and structured tools preferred |
| `.text` is a property | Not a method (parentheses emit deprecation warning) |

### Deep Agents 0.3.9

**Pattern:**
```python
from deepagents import create_deep_agent, CompiledSubAgent

agent = create_deep_agent(
    model="anthropic:claude-sonnet-4-20250514",
    tools=[my_tool_1, my_tool_2],
    subagents=[
        {
            "name": "discovery-agent",
            "description": "Profiles schemas, detects PKs, infers FKs, builds ERD",
            "system_prompt": "You are a data discovery specialist...",
            "tools": [snowflake_tools, neo4j_tools],
        }
    ]
)
```

**Built-in tools:** `write_todos`, `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `task`.

**Custom subagents via `CompiledSubAgent`:**
```python
custom_graph = create_agent(model=your_model, tools=specialized_tools, prompt="...")
custom_subagent = CompiledSubAgent(name="validator", description="...", runnable=custom_graph)
agent = create_deep_agent(model="claude-sonnet-4-5-20250929", subagents=[custom_subagent])
```

**Default model:** `claude-sonnet-4-5-20250929`.

### LangGraph 1.0.7

**Key change: Context API replaces `config["configurable"]`:**

```python
from langgraph.graph import StateGraph
from langgraph.runtime import Runtime
from typing_extensions import TypedDict

class AgentState(TypedDict):
    messages: list

class AgentContext(TypedDict):
    snowflake_session: object
    workspace_id: str

graph = StateGraph(state_schema=AgentState, context_schema=AgentContext)

def discovery_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
    workspace_id = runtime.context["workspace_id"]
    return {"messages": [...]}

graph.add_node("discovery", discovery_node)
compiled = graph.compile()

result = compiled.invoke(
    {"messages": [...]},
    context={"snowflake_session": session, "workspace_id": "ws-123"}
)
```

**Checkpointing with PostgreSQL:**
```python
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

async with AsyncPostgresSaver.from_conn_string(DATABASE_URL) as checkpointer:
    compiled = graph.compile(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "session-abc"}}
    result = await compiled.ainvoke({"messages": [...]}, config=config)
```

**Known issue:** `RemoteGraph` in LangGraph 1.0 cannot use `context` and `config` together ([#6342](https://github.com/langchain-ai/langgraph/issues/6342)). Does not affect our architecture since we use local compilation.

### langchain-mcp-adapters 0.2.0

**Pattern:**
```python
from langchain_mcp_adapters.client import MultiServerMCPClient

async with MultiServerMCPClient({
    "google-drive": {"transport": "stdio", "command": "npx", "args": ["-y", "@anthropic/mcp-google-drive"]},
    "confluence": {"transport": "http", "url": "http://localhost:9000/mcp"},
}) as client:
    tools = client.get_tools()
    agent = create_agent(model="anthropic:claude-sonnet-4-20250514", tools=tools)
```

**Transport types:** stdio, SSE, Streamable HTTP, WebSocket.

### FastAPI 0.128.0

**Pattern (lifespan):**
```python
from fastapi import FastAPI
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize DB connections, Redis, Neo4j
    yield
    # Shutdown: cleanup

app = FastAPI(lifespan=lifespan)
```

**Key notes:** Python 3.9+ required (3.10+ recommended). Pydantic v2 fully integrated. Dependency with `yield` exit code runs after response is sent (important for StreamingResponse + DB sessions).

### Snowflake Cortex AI

**Model names (Cortex):**

| Model | Provider |
|-------|----------|
| `claude-4-sonnet` | Anthropic (recommended for ekaiX) |
| `claude-4-opus` | Anthropic |
| `claude-sonnet-4-5` | Anthropic (preview) |

**`AI_COMPLETE` (recommended) vs `COMPLETE` (legacy):**

```sql
-- New (recommended)
SELECT AI_COMPLETE(
    model => 'claude-4-sonnet',
    prompt => 'Analyze this schema...',
    guardrails => TRUE,
    response_format => OBJECT(result STRING, confidence FLOAT)
);

-- Legacy (still works)
SELECT SNOWFLAKE.CORTEX.COMPLETE('claude-4-sonnet', [messages], options);
```

### Snowpark Python 1.44.0

**RCR for Native Apps (GA since Aug 29, 2025):**

```python
@sproc(
    name="discovery_procedure",
    is_permanent=True,
    stage_location="@app_stage",
    execute_as="restricted caller",  # GA for Native Apps
    packages=["snowflake-snowpark-python"],
)
def discovery_procedure(session, database_name: str) -> dict:
    result = session.sql(f"""
        SELECT TABLE_CATALOG, TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE
        FROM {database_name}.INFORMATION_SCHEMA.COLUMNS
    """).collect()
    return {"columns": [row.as_dict() for row in result]}
```

### Combined AI Service Install

```bash
pip install \
    langchain==1.2.8 langchain-core langchain-anthropic \
    deepagents==0.3.9 \
    langgraph==1.0.7 langgraph-checkpoint==4.0.0 langgraph-checkpoint-postgres==3.0.3 \
    langchain-mcp-adapters==0.2.0 \
    "fastapi[standard]==0.128.0" uvicorn \
    snowflake-snowpark-python==1.44.0 snowflake-connector-python==4.2.0
```

---

## 4. Data Services (Docker)

### PostgreSQL 18.1

**Key features for ekaiX:**
- `uuidv7()` — timestamp-ordered UUIDs, no extension needed
- Virtual generated columns — derive fields from JSONB without disk storage
- Parallel GIN index builds — faster JSONB indexing
- `RETURNING OLD/NEW` — useful for audit logging
- AIO subsystem — up to 3x I/O performance

```bash
docker pull postgres:18
```

### Neo4j 2025.x

**Key changes:**
- CalVer versioning (YYYY.MM.PATCH)
- Native VECTOR type
- Cypher 25 language version
- JS driver v6: `executeRead`/`executeWrite` only

```bash
docker pull neo4j:2025
# OR LTS
docker pull neo4j:5.26
```

### Redis 8.4.0

**Key changes:**
- 87% faster commands, 2x ops/sec throughput
- 8 built-in data structures (JSON, vectors, Bloom filters, etc.)
- Redis Stack features merged into open source
- `FT.HYBRID` for combined search + vector similarity

```bash
docker pull redis:8
```

### MinIO

**Note:** Source-only distribution since Oct 2025. Use Docker image.

```bash
docker pull minio/minio
```

### Docker Compose v5.0.2

**Key points:**
- No `version:` key in compose files (Compose Specification format)
- `docker compose` (space, not hyphen)
- Functionally identical to v2 for CLI users

```yaml
# compose.yaml — no version: key needed
services:
  postgresql:
    image: postgres:18
    ports: ["5432:5432"]
    environment:
      POSTGRES_DB: ekaix
      POSTGRES_USER: ekaix
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ekaix"]
      interval: 10s
      timeout: 5s
      retries: 5

  neo4j:
    image: neo4j:2025
    ports: ["7474:7474", "7687:7687"]
    environment:
      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD}
    volumes:
      - neo4jdata:/data

  redis:
    image: redis:8
    ports: ["6379:6379"]
    volumes:
      - redisdata:/data
    command: redis-server --appendonly yes

  minio:
    image: minio/minio
    command: server /data --console-address ":9001"
    ports: ["9000:9000", "9001:9001"]
    environment:
      MINIO_ROOT_USER: ${MINIO_ACCESS_KEY}
      MINIO_ROOT_PASSWORD: ${MINIO_SECRET_KEY}
    volumes:
      - miniodata:/data

volumes:
  pgdata:
  neo4jdata:
  redisdata:
  miniodata:
```

---

## 5. Decision Log

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Next.js version | 15 (not 16) | MUI v7 incompatible with 16; migration path clear once resolved |
| Styling engine | Emotion (not Pigment CSS) | Pigment CSS still alpha v0.0.30; not production-ready |
| Redis client | node-redis (not ioredis) | Official recommendation; Redis 8 feature support; ioredis maintenance-only |
| MinIO SDK | `minio@8` (primary) | Direct SDK; can switch to `@aws-sdk/client-s3` for portability |
| LLM function | `AI_COMPLETE` (not `COMPLETE`) | New recommended API; native structured outputs |
| RCR mode | `EXECUTE AS RESTRICTED CALLER` | GA for Native Apps; correct security model |
| LangChain agent | `create_agent` (not `create_react_agent`) | v1.x API; old name deprecated |
| LangGraph context | `context_schema` + `Runtime[ContextT]` | Replaces `config["configurable"]`; cleaner typed access |
| Checkpointing | `AsyncPostgresSaver` | Production backend; aligns with CompositeBackend design |
| Cortex model | `claude-4-sonnet` | Recommended cost/performance balance |
| PM2 | Dev only | AGPL license; not suitable for SPCS containers |

---

## Sources

### Frontend
- [Next.js 16 Blog](https://nextjs.org/blog/next-16) | [Next.js npm](https://www.npmjs.com/package/next)
- [MUI v7 Announcement](https://mui.com/blog/material-ui-v7-is-here/) | [MUI v7 Upgrade Guide](https://mui.com/material-ui/migration/upgrade-to-v7/)
- [MUI Next.js 16 Issue #47109](https://github.com/mui/material-ui/issues/47109)
- [MUI Theming Docs](https://mui.com/material-ui/customization/theming/)
- [React 19.2 Blog](https://react.dev/blog/2025/10/01/react-19-2) | [React Versions](https://react.dev/versions)

### AI / Python
- [LangChain v1 Migration](https://docs.langchain.com/oss/python/migrate/langchain-v1) | [LangChain PyPI](https://pypi.org/project/langchain/)
- [Deep Agents Docs](https://docs.langchain.com/oss/python/deepagents/overview) | [Deep Agents PyPI](https://pypi.org/project/deepagents/)
- [LangGraph Context API Issue #5023](https://github.com/langchain-ai/langgraph/issues/5023) | [LangGraph PyPI](https://pypi.org/project/langgraph/)
- [langchain-mcp-adapters PyPI](https://pypi.org/project/langchain-mcp-adapters/)
- [FastAPI Release Notes](https://fastapi.tiangolo.com/release-notes/)
- [Snowflake AI_COMPLETE Docs](https://docs.snowflake.com/en/sql-reference/functions/ai_complete)
- [Snowflake RCR Docs](https://docs.snowflake.com/en/developer-guide/restricted-callers-rights)
- [Snowpark PyPI](https://pypi.org/project/snowflake-snowpark-python/)

### Backend / Infrastructure
- [Fastify v5 Migration](https://fastify.dev/docs/latest/Guides/Migration-Guide-V5/)
- [Neo4j CalVer Announcement](https://neo4j.com/blog/neo4j-calendar-versioning/)
- [Redis 8 Blog](https://redis.io/blog/redis-8-ga/)
- [node-redis Migration Guide](https://redis.io/docs/latest/develop/clients/nodejs/migration/)
- [Docker Compose v5 Release Notes](https://docs.docker.com/compose/releases/release-notes/)
- [PostgreSQL 18 Release Notes](https://www.postgresql.org/docs/18/release-18.html)
