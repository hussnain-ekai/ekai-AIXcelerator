# ekaiX - Technical Product Requirements Document

**Product:** ekai AIXelerator (ekaiX)  
**Version:** 1.0  
**Date:** February 2026  
**Target:** AI Coding Agent (Claude Code) Implementation

---

## Product Overview

### What We're Building

Snowflake Native App enabling business users to create semantic models and Cortex Agents through continuous AI conversation. Self-service data product creation from gold layer databases without SQL/DBT knowledge.

### Core Value Proposition

Business users interact with AI agent to:
1. Analyze existing gold layer databases (automatic discovery)
2. Capture business requirements through natural conversation
3. Generate semantic views (YAML) referencing gold tables directly
4. Publish as Snowflake Cortex Agents accessible via Snowflake Intelligence

**Key Constraint:** Gold layer only - no transformations, no DBT generation. Direct semantic layer on clean data.

### Product Differentiation

This is **NOT** the full ekai platform. This is a focused entry point for:
- Enterprises with mature medallion architecture
- Business analysts (not data engineers)
- Fast path to Snowflake Intelligence (hours not days)
- Simplified workflow (conversation not multi-step wizards)

---

## Technical Architecture

### Deployment Model

**Snowflake Native App via SPCS (Snowpark Container Services)**

All components run in consumer's Snowflake account as containerized services. No external infrastructure.

### Security Architecture

**Restricted Caller's Rights (RCR) - Critical Requirement**

Every stored procedure and query executes with `EXECUTE AS CALLER`:
- Uses logged-in user's actual role permissions
- User A (Sales schema access) sees only Sales data
- User B (all schemas access) sees all data
- Zero privilege elevation - pure permission delegation

**Authentication:**
- Snowflake native only (no separate login)
- User identity from SPCS header: `Sf-Context-Current-User`
- Workspace isolation per Snowflake user

**Data Access:**
- Consumer admin grants once: `GRANT CALLER SELECT ON DATABASE gold_db TO APPLICATION ekaiX`
- User grants database REFERENCES via Python Permission SDK
- No connection credentials - leverages existing Snowflake permissions

### Container Stack (All in SPCS)

**Frontend Service:**
- React (Next.js) application
- Public endpoint for UI access
- Communicates with backend via SPCS service endpoints
- NGINX router for CORS handling

**Backend Service:**
- Node.js API server
- Handles HTTP requests from frontend
- Orchestrates calls to FastAPI services
- Manages session state

**AI Service:**
- FastAPI Python microservice
- LangChain Deep Agents orchestration
- Snowflake Cortex LLM calls (Claude Sonnet 4 primary)
- MCP server integrations

**Data Services (containerized):**
- PostgreSQL - application database
- Neo4j - ERD graph database
- Redis - agent state and memory
- MinIO - object storage for artifacts

### Network Architecture

**SPCS Service Communication:**
- Internal service-to-service communication within SPCS
- Frontend → Backend → FastAPI → Snowflake/Data Services
- All services in same SPCS compute pool
- No external network calls except Snowflake Cortex APIs

**Snowflake Integration:**
- Stored procedures (Python) with `EXECUTE AS CALLER`
- INFORMATION_SCHEMA queries via RCR
- Cortex AI functions (COMPLETE, SEARCH, ANALYST)
- Semantic view creation
- Cortex Agent deployment

---

## Component Specifications

### 1. React Frontend Application

**Technology:**
- Next.js (latest stable 2026 version)
- TypeScript
- Component library matching existing ekai design system
- Real-time streaming updates from backend

**Core UI Components:**

**Chat Interface (Primary Screen):**
- Left panel: Conversation thread with agent
- Right panel: Live artifacts (ERD visualization, data previews, YAML, etc.)
- Bottom: Input with support for text, file upload, voice (future)
- Streaming response rendering (agent "thinking" visible)

**Database Selection:**
- Query available databases user can access
- Multi-select for schemas within database
- Show metadata counts (tables, columns, etc.)

**ERD Visualization:**
- Interactive graph rendering (use D3.js or similar)
- Node = table, Edge = relationship
- Click table → show columns, stats, sample data
- Highlight active entities in current conversation

**Data Preview:**
- Table rendering of query results
- Export to CSV functionality
- Quality check indicators (nulls, cardinality, etc.)

**Workspace Management:**
- List user's data products
- Share data product with other users
- Clone/duplicate data products
- Delete data products

**Settings:**
- MCP server connections (Google Drive, Confluence, etc.)
- User preferences
- Database reference management

**Key Requirements:**
- Mobile responsive (not primary but should work)
- Accessibility (WCAG AA minimum)
- Real-time updates (WebSocket or SSE for streaming)
- Keyboard shortcuts for power users
- Dark mode support

### 2. Node.js Backend API

**Technology:**
- Node.js (latest LTS 2026)
- TypeScript
- Express or Fastify framework
- REST API design

**Core Endpoints:**

**Authentication & Session:**
- `GET /auth/user` - Get current user from SPCS header
- `POST /session/create` - Initialize workspace session
- `GET /session/:id` - Retrieve session state

**Database Discovery:**
- `GET /databases` - List databases user can access (via RCR)
- `POST /databases/:id/reference` - Create REFERENCE and GRANT CALLER
- `GET /databases/:id/schemas` - List schemas
- `GET /schemas/:id/tables` - List tables in schema

**Data Products:**
- `GET /data-products` - List user's data products (workspace isolation)
- `POST /data-products` - Create new data product
- `GET /data-products/:id` - Get data product details
- `PUT /data-products/:id` - Update data product
- `DELETE /data-products/:id` - Delete data product
- `POST /data-products/:id/share` - Share with another user
- `POST /data-products/:id/publish` - Publish to Snowflake Intelligence

**Agent Conversation:**
- `POST /agent/message` - Send message to Deep Agents orchestrator
- `GET /agent/stream/:session_id` - SSE stream for agent responses
- `POST /agent/interrupt` - Interrupt current agent operation
- `POST /agent/rollback/:checkpoint_id` - Roll back to previous state

**Artifacts:**
- `GET /artifacts/:id` - Retrieve ERD, YAML, BRD, etc.
- `POST /artifacts/:id/export` - Export to file
- `PUT /artifacts/:id` - Update artifact

**Documents:**
- `POST /documents/upload` - Upload PDF/DOCX to MinIO
- `POST /documents/extract` - Trigger Cortex Document AI extraction
- `GET /documents/:id/content` - Retrieved extracted content

**Key Requirements:**
- Request validation (Zod or similar)
- Error handling with structured responses
- Logging (structured JSON logs)
- Rate limiting per user
- Request timeout handling
- CORS configuration for SPCS

### 3. FastAPI AI Service

**Technology:**
- Python 3.11+
- FastAPI framework
- LangChain Deep Agents (latest 2026 version)
- Snowflake Python connector
- Neo4j Python driver
- PostgreSQL async driver (asyncpg)
- Redis client (redis-py)

**Core Modules:**

**Deep Agents Orchestrator:**
- Main ekaiX agent (orchestrator)
- Specialized subagents:
  - Discovery Agent (schema profiling, ERD inference)
  - Requirements Agent (BRD capture through conversation)
  - Generation Agent (semantic view YAML creation)
  - Validation Agent (test query execution)
  - Publishing Agent (Cortex Agent deployment)
  - Explorer Agent (ad-hoc user queries)

**LLM Integration:**
- Snowflake Cortex AI primary (Claude Sonnet 4)
- Multi-model support (GPT-4, Gemini for cross-validation)
- Structured output parsing
- Token usage tracking

**Tools (LangChain Tools):**

**Snowflake Tools (all via RCR stored procedures):**
- `query_information_schema` - Schema metadata discovery
- `profile_table` - Statistical profiling of tables
- `execute_rrc_query` - Run query with user's permissions
- `create_semantic_view` - Deploy semantic view YAML
- `create_cortex_agent` - Deploy Cortex Agent
- `grant_agent_access` - Grant access to user's role
- `validate_sql` - Dry-run SQL compilation

**Neo4j Tools:**
- `query_erd_graph` - Query ERD nodes/relationships
- `update_erd` - Add/modify ERD elements
- `get_relationship_path` - Find join paths between tables
- `classify_entity` - Get fact/dimension classification

**PostgreSQL Tools:**
- `save_workspace_state` - Persist workspace
- `load_workspace_state` - Retrieve workspace
- `save_brd` - Store business requirements
- `save_semantic_view` - Store generated YAML
- `log_agent_action` - Audit trail

**MinIO Tools:**
- `upload_artifact` - Store YAML, documents, exports
- `retrieve_artifact` - Fetch artifact
- `list_artifacts` - List artifacts for data product

**MCP Integration:**
- Google Drive search/fetch
- Confluence search/fetch
- Slack search
- SharePoint search/fetch
- Configure via MCP server URLs in Deep Agents

**Backend Configuration (Deep Agents):**
- CompositeBackend:
  - StateBackend (Redis) - active conversation state
  - StoreBackend (PostgreSQL) - cross-session persistence
  - FilesystemBackend (MinIO) - artifact storage
  - Neo4jBackend (custom) - ERD graph operations

**Key Requirements:**
- Async operations (FastAPI async endpoints)
- Streaming responses (SSE for chat)
- Error handling with recovery paths
- LangSmith observability integration
- Token budget management
- Retry logic with exponential backoff

### 4. PostgreSQL Database

**Purpose:** Application state, workspaces, metadata

**Core Schema:**

**Workspaces:**
```sql
workspaces (
  id UUID PRIMARY KEY,
  snowflake_user STRING UNIQUE NOT NULL,
  created_at TIMESTAMP,
  updated_at TIMESTAMP
)
```

**Data Products:**
```sql
data_products (
  id UUID PRIMARY KEY,
  workspace_id UUID REFERENCES workspaces(id),
  name STRING NOT NULL,
  database_reference STRING NOT NULL,
  schemas STRING[] NOT NULL,
  state JSONB NOT NULL, -- Full Deep Agents state
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  published_at TIMESTAMP,
  published_agent_fqn STRING
)
```

**Sharing:**
```sql
data_product_shares (
  id UUID PRIMARY KEY,
  data_product_id UUID REFERENCES data_products(id),
  shared_with_user STRING NOT NULL,
  permission STRING NOT NULL, -- 'view' or 'edit'
  created_at TIMESTAMP
)
```

**Business Requirements (BRD):**
```sql
business_requirements (
  id UUID PRIMARY KEY,
  data_product_id UUID REFERENCES data_products(id),
  brd_json JSONB NOT NULL,
  version INTEGER NOT NULL,
  created_at TIMESTAMP
)
```

**Semantic Views:**
```sql
semantic_views (
  id UUID PRIMARY KEY,
  data_product_id UUID REFERENCES data_products(id),
  yaml_content TEXT NOT NULL,
  validation_status STRING NOT NULL,
  validation_errors JSONB,
  created_at TIMESTAMP
)
```

**Artifacts:**
```sql
artifacts (
  id UUID PRIMARY KEY,
  data_product_id UUID REFERENCES data_products(id),
  artifact_type STRING NOT NULL, -- 'erd', 'yaml', 'brd', 'document'
  minio_path STRING NOT NULL,
  metadata JSONB,
  created_at TIMESTAMP
)
```

**Audit Log:**
```sql
audit_logs (
  id UUID PRIMARY KEY,
  workspace_id UUID REFERENCES workspaces(id),
  data_product_id UUID REFERENCES data_products(id),
  action_type STRING NOT NULL,
  action_details JSONB NOT NULL,
  user_name STRING NOT NULL,
  timestamp TIMESTAMP
)
```

**Documents:**
```sql
uploaded_documents (
  id UUID PRIMARY KEY,
  data_product_id UUID REFERENCES data_products(id),
  filename STRING NOT NULL,
  minio_path STRING NOT NULL,
  extracted_content TEXT,
  extraction_status STRING,
  created_at TIMESTAMP
)
```

**Key Requirements:**
- Row-level security policies (workspace isolation)
- Indexes on foreign keys, user fields
- JSONB indexes for state queries
- Partitioning for audit_logs (by timestamp)
- Regular vacuum/analyze
- Connection pooling

### 5. Neo4j Graph Database

**Purpose:** ERD storage, relationship inference, lineage

**Core Node Types:**

**Database:**
```
(:Database {
  fqn: STRING,
  name: STRING,
  data_product_id: UUID
})
```

**Schema:**
```
(:Schema {
  fqn: STRING,
  name: STRING,
  database_fqn: STRING
})
```

**Table:**
```
(:Table {
  fqn: STRING,
  name: STRING,
  schema_fqn: STRING,
  table_type: STRING, -- 'fact' or 'dimension'
  row_count: INTEGER,
  business_description: STRING,
  business_concept: STRING,
  domain: STRING
})
```

**Column:**
```
(:Column {
  fqn: STRING,
  name: STRING,
  table_fqn: STRING,
  data_type: STRING,
  is_nullable: BOOLEAN,
  is_primary_key: BOOLEAN,
  uniqueness: FLOAT,
  nullability_pct: FLOAT,
  pattern: STRING,
  sample_values: STRING[]
})
```

**Core Relationship Types:**

**HAS_SCHEMA:**
```
(:Database)-[:HAS_SCHEMA]->(:Schema)
```

**HAS_TABLE:**
```
(:Schema)-[:HAS_TABLE]->(:Table)
```

**HAS_COLUMN:**
```
(:Table)-[:HAS_COLUMN]->(:Column)
```

**FOREIGN_KEY:**
```
(:Column)-[:FOREIGN_KEY {
  confidence: FLOAT,
  inferred: BOOLEAN,
  cardinality: STRING -- '1:1', '1:N', 'N:1', 'N:M'
}]->(:Column)
```

**REFERENCES (for semantic view usage):**
```
(:SemanticView)-[:REFERENCES]->(:Table)
```

**Key Requirements:**
- Cypher query optimization
- Index on fqn properties
- Index on data_product_id for workspace filtering
- Apoc procedures for graph algorithms
- Backup/restore procedures

### 6. Redis

**Purpose:** Deep Agents state, LangGraph checkpoints, cache

**Key Structures:**

**Agent State:**
```
agent:session:{session_id} -> HASH
  - messages (list of message dicts)
  - current_step (string)
  - todo_list (JSON array)
  - context (JSON object)
```

**Checkpoints (LangGraph):**
```
checkpoint:{session_id}:{checkpoint_id} -> STRING (serialized state)
```

**User Sessions:**
```
session:{user}:active -> STRING (session_id)
session:{session_id}:metadata -> HASH
```

**Cache:**
```
cache:erd:{connection_id} -> STRING (JSON)
cache:profile:{table_fqn} -> STRING (JSON)
```

**Key Requirements:**
- TTL on cache entries (1 hour)
- Persistence for checkpoints (RDB + AOF)
- Memory limits with eviction policy (LRU)
- Pub/sub for real-time updates

### 7. MinIO Object Storage

**Purpose:** Artifact storage (YAML files, documents, exports)

**Bucket Structure:**

**artifacts:**
```
{data_product_id}/erd/{timestamp}.json
{data_product_id}/yaml/{version}.yaml
{data_product_id}/brd/{version}.json
{data_product_id}/exports/{filename}.csv
```

**documents:**
```
{data_product_id}/uploads/{document_id}/{filename}
{data_product_id}/extracted/{document_id}.txt
```

**workspace:**
```
{workspace_id}/state/{timestamp}.json
{workspace_id}/exports/{filename}
```

**Key Requirements:**
- Versioning enabled
- Lifecycle policies (old artifacts archived)
- Access logging
- Encryption at rest

---

## Snowflake Integration Specifications

### Stored Procedures (Python with RCR)

**Discovery Procedure:**
```sql
CREATE PROCEDURE ekaiX.discover_schema(
  ref_name STRING,
  schemas STRING[]
)
RETURNS VARIANT
LANGUAGE PYTHON
EXECUTE AS CALLER
RUNTIME_VERSION = '3.11'
PACKAGES = ('snowflake-snowpark-python')
```

**Functionality:**
- Query INFORMATION_SCHEMA.TABLES, COLUMNS, CONSTRAINTS
- Use caller's permissions (only sees tables they can access)
- Return metadata as VARIANT

**Profiling Procedure:**
```sql
CREATE PROCEDURE ekaiX.profile_table(
  table_fqn STRING,
  sample_size INTEGER DEFAULT 10000
)
RETURNS VARIANT
LANGUAGE PYTHON
EXECUTE AS CALLER
```

**Functionality:**
- Statistical profiling: uniqueness, nullability, patterns
- HyperLogLog sketches for cardinality
- Sample values extraction
- Cross-table correlation for FK inference
- Use caller's SELECT permissions

**Validation Procedure:**
```sql
CREATE PROCEDURE ekaiX.validate_semantic_view(
  yaml_content STRING
)
RETURNS VARIANT
LANGUAGE PYTHON
EXECUTE AS CALLER
```

**Functionality:**
- Parse YAML
- Verify column existence
- Test SQL compilation (EXPLAIN without execution)
- Cardinality checks
- Return validation results

**Publishing Procedure:**
```sql
CREATE PROCEDURE ekaiX.publish_cortex_agent(
  semantic_view_yaml STRING,
  agent_config VARIANT
)
RETURNS VARIANT
LANGUAGE PYTHON
EXECUTE AS OWNER
```

**Functionality:**
- Create semantic view in ekaiX_semantic_views schema
- Create Cortex Agent with semantic view reference
- Grant access to caller's role
- Return agent FQN and Intelligence URL

### Cortex AI Integration

**Primary LLM:**
```sql
SELECT SNOWFLAKE.CORTEX.COMPLETE(
  'claude-sonnet-4',
  [messages],
  {
    'temperature': 0.1,
    'max_tokens': 4000
  }
)
```

**Document Extraction:**
```sql
SELECT SNOWFLAKE.CORTEX.PARSE_DOCUMENT(
  @documents/{filename},
  {'mode': 'LAYOUT'}
)
```

**Key Requirements:**
- Token budget management
- Retry logic for rate limits
- Error handling for Cortex API failures
- Model fallback (GPT-4 if Claude unavailable)

### Semantic View Format

**Snowflake Semantic View YAML Specification:**

Must generate valid semantic view YAML that directly references gold layer tables (no transformations).

**Required Elements:**
- `name` - semantic view identifier
- `description` - business description
- `tables` - array of table references with FQN identifiers
- `measures` - aggregation expressions
- `dimensions` - grouping expressions
- `time_dimensions` - temporal expressions with granularities
- `joins` - explicit join conditions
- `filters` - WHERE clause expressions

**Example Structure (do not include in code):**
```yaml
name: example_view
description: Business description
tables:
  - name: fact_table
    identifier: database.schema.fact_table
    measures:
      - name: metric_name
        expr: sum(column_name)
  - name: dim_table
    identifier: database.schema.dim_table
    dimensions:
      - name: dimension_name
        expr: column_name
joins:
  - join_type: left
    left_table: fact_table
    right_table: dim_table
    sql_on: "fact_table.key = dim_table.key"
time_dimensions:
  - name: time_column
    expr: date_column
    type: time
    granularities: [day, month, quarter, year]
filters:
  - expr: "status != 'CANCELLED'"
```

### Cortex Agent Configuration

**Created via:**
```sql
CREATE CORTEX AGENT schema.agent_name
WITH
  SEMANTIC_VIEW = 'schema.semantic_view_name'
  DESCRIPTION = 'Business description'
  INSTRUCTIONS = 'Agent behavior instructions'
  MODEL = 'claude-sonnet-4'
```

**Agent inherits RCR permissions** - different users see different results based on their data access.

---

## Deep Agents Configuration

### Agent System Prompts

**Main ekaiX Orchestrator:**

```
You are ekaiX, an AI data modeling assistant that helps business users create semantic models from their Snowflake gold layer data.

Your role:
- Guide users through data product creation via natural conversation
- Coordinate specialized subagents for discovery, requirements, generation, validation
- Maintain context throughout the entire workflow
- Handle user interruptions, questions, and course corrections gracefully
- Ground all claims in actual schema metadata and data

Your capabilities:
- Discover and profile database schemas
- Capture business requirements through interactive interview
- Generate semantic view YAML specifications
- Validate generated models with test queries
- Publish Cortex Agents to Snowflake Intelligence
- Query external systems (Google Drive, Confluence, etc.) for business context

Key principles:
- Always verify facts against actual data (no hallucination)
- Show reasoning transparently
- Offer specific choices from discovered data
- Bias toward false positives in relationship detection (humans correct visible errors)
- Never assume - clarify ambiguities through questions
- Adapt communication style to user's technical level

When uncertain, ask. When confident, explain why.
```

**Discovery Agent:**

```
You are the Discovery Agent, responsible for analyzing database schemas and building ERD graphs.

Your tasks:
1. Query INFORMATION_SCHEMA for tables, columns, constraints
2. Profile tables statistically (uniqueness, nullability, patterns)
3. Detect primary keys (>98% uniqueness, 0% nulls)
4. Infer foreign key relationships using:
   - Column name similarity
   - Data type compatibility
   - Value overlap analysis
   - Statistical correlation
5. Classify entities as FACT or DIMENSION
6. Generate business-friendly descriptions

Bias: Prefer false positives (show uncertain relationships). Users correct visible errors more easily than they find missing relationships.

Always cite your reasoning: "Based on 99.2% uniqueness and '_id' suffix, this appears to be a primary key."
```

**Requirements Agent:**

```
You are the Requirements Agent, responsible for capturing business requirements through conversation.

Your tasks:
1. Understand the user's analytical objective
2. Identify required metrics, dimensions, filters
3. Resolve ambiguities by offering specific choices from discovered data
4. Validate business logic (calculations, exclusions, time ranges)
5. Build complete, unambiguous BRD (Business Requirements Document)

Guidelines:
- Show actual data examples when explaining choices
- Provide business context (record counts, distributions)
- Progressive disclosure (don't overwhelm with all questions upfront)
- Max 15 conversation turns (prevent infinite loops)
- Offer recommendations based on common patterns

Never assume. If multiple interpretations exist, present options with data.
```

**Generation Agent:**

```
You are the Generation Agent, responsible for creating semantic view YAML from business requirements.

Your tasks:
1. Select required tables from ERD based on BRD
2. Validate join paths (detect many-to-many issues)
3. Generate Snowflake semantic view YAML
4. Reference gold layer tables directly (no transformations)
5. Apply business rules from BRD (filters, calculations)

CRITICAL: Generated YAML must:
- Use fully qualified table names
- Include explicit join conditions
- Reference only columns that exist
- Compile to valid SQL
- Match business requirements exactly

Format: Snowflake semantic view YAML specification only. No explanations unless requested.
```

**Validation Agent:**

```
You are the Validation Agent, responsible for testing generated semantic views.

Your tasks:
1. Execute test queries against actual data (via RCR)
2. Verify cardinality (detect row inflation)
3. Check for unexpected nulls
4. Validate data ranges (min/max within expected bounds)
5. Test business rules application
6. Generate preview with sample results

Tests to run:
- Basic aggregation (10-100 sample rows)
- Cardinality check (row count vs distinct keys)
- Null percentage check (flag if >5%)
- Data range validation (outlier detection)
- Filter effectiveness check

Flag issues clearly: "⚠ Warning: 15% null values in revenue field. Expected?"
```

**Explorer Agent:**

```
You are the Explorer Agent, responsible for answering ad-hoc questions during the workflow.

Your tasks:
1. Execute queries when user asks for data exploration
2. Explain schema structure
3. Show sample data
4. Compare alternatives
5. Investigate anomalies

Always execute queries via RCR (user's permissions). Never make up data.

Examples:
- "Show me top 10 customers in January 2024"
- "What's the difference between these two revenue fields?"
- "How many null values in segment_code?"

Respond with: actual query results + brief explanation. No speculation.
```

### Tool Grounding Rules

**Every agent must:**

1. **Verify before claiming** - Query ERD graph or execute data query before making factual statements
2. **Cite sources** - "Based on INFORMATION_SCHEMA query..." or "Profiling shows..."
3. **Show confidence** - "98% confidence this is PK" vs "Uncertain, needs review"
4. **Transparent reasoning** - Explain why a conclusion was reached
5. **Fallback on uncertainty** - Ask user if unsure rather than guess

**Forbidden:**
- Inventing table/column names not in schema
- Making up relationships not inferred or explicit
- Claiming capabilities not available via tools
- Speculating about data without querying

### Subagent Orchestration

**Main agent delegates to subagents based on conversation phase:**

```
User: "Analyze GOLD_SALES_DB"
→ Orchestrator calls Discovery Agent
→ Discovery Agent uses tools to profile database
→ Discovery Agent returns ERD to Orchestrator
→ Orchestrator presents findings to user

User: "I want monthly revenue by segment"
→ Orchestrator calls Requirements Agent
→ Requirements Agent interviews user
→ Requirements Agent returns BRD to Orchestrator
→ Orchestrator calls Generation Agent with BRD
→ Generation Agent creates YAML
→ Orchestrator calls Validation Agent with YAML
→ Validation Agent tests and returns preview
→ Orchestrator shows preview to user

User: "What's the difference between ORDER_TOTAL and NET_REVENUE?"
→ Orchestrator calls Explorer Agent
→ Explorer Agent queries sample data
→ Explorer Agent returns comparison to Orchestrator
→ Orchestrator presents answer to user
```

**State shared across all agents:**
- ERD graph
- BRD (evolving)
- Conversation history
- User preferences
- Workspace context

---

## Quality Requirements

### Functional Requirements

**FR1: Database Discovery**
- User can select database and schemas
- System discovers all tables user can access (via RCR)
- Profiling completes within 10 minutes for 100-table schema
- Primary key detection accuracy >95%
- Foreign key inference accuracy >90%

**FR2: Business Requirements Capture**
- Agent conducts interactive interview
- User can provide requirements via text, documents, or external system references
- Agent clarifies ambiguities with specific data-driven choices
- BRD captures: objectives, metrics, dimensions, filters, business rules
- User can modify requirements at any point

**FR3: Semantic View Generation**
- YAML generated from BRD
- References gold tables directly (no transformations)
- Validates: syntax, column existence, SQL compilation
- Cross-validation with multiple LLMs (optional)
- Generation completes in <3 minutes

**FR4: Test & Preview**
- Executes test queries via RCR
- Preview shows sample results (10-100 rows)
- Quality checks: cardinality, nulls, ranges
- User must approve before publish
- Export preview to CSV

**FR5: Publishing**
- Deploys semantic view to consumer account
- Creates Cortex Agent with instructions
- Grants access to user's role
- Returns Snowflake Intelligence URL
- Agent inherits RCR permissions

**FR6: Workspace Management**
- User has isolated workspace
- Can create multiple data products
- Share data products with other users (view/edit)
- Delete data products
- View audit trail

**FR7: Document Integration**
- Upload PDF/DOCX in conversation
- Extract text via Cortex Document AI
- Use extracted business rules in modeling
- Store documents in MinIO

**FR8: External System Integration**
- Connect to Google Drive, Confluence, Slack, etc. via MCP
- Search external systems for business context
- Respect user's permissions in external systems
- OAuth authentication for MCP servers

### Non-Functional Requirements

**NFR1: Performance**
- UI initial load <2 seconds
- Agent response start (first token) <3 seconds
- Database discovery <10 minutes (100 tables)
- Semantic view generation <3 minutes
- Test query execution <30 seconds
- Support 50 concurrent users per customer account

**NFR2: Scalability**
- Support schemas up to 500 tables
- Handle 1M+ rows for profiling
- Store 1000+ data products per account
- 100 concurrent conversations
- SPCS compute pool autoscaling

**NFR3: Security**
- RCR enforced on all data access
- Workspace isolation (user cannot see others' data products unless shared)
- MCP OAuth integration
- Audit logging for all actions
- RBAC for sharing permissions
- No data leaves Snowflake account

**NFR4: Reliability**
- 99.5% uptime (depends on Snowflake SLA)
- Graceful degradation if Cortex AI unavailable
- Auto-retry on transient failures (3 attempts)
- Checkpoint/resume for long operations
- State recovery from Redis/PostgreSQL

**NFR5: Observability**
- Structured logging (JSON)
- LangSmith traces for agent operations
- Metrics: response times, token usage, error rates
- Audit trail for compliance
- User activity analytics

**NFR6: Usability**
- Mobile responsive UI
- Keyboard navigation support
- Accessibility (WCAG AA)
- Real-time streaming responses
- Conversation export

**NFR7: Data Quality Gates**
- Gold layer quality check (>60% or block)
- BRD completeness validation
- YAML syntax validation
- SQL compilation validation
- User approval required for publish

---

## Development Environment Setup

### Prerequisites

**Required Software:**
- Docker Desktop (latest 2026 version)
- Node.js 20+ LTS
- Python 3.11+
- Git
- Visual Studio Code or JetBrains IDE

**Snowflake Account:**
- Access to Snowflake account with ACCOUNTADMIN role
- SPCS enabled (ORGADMIN required to enable)
- Cortex AI features enabled
- Trial account acceptable for development

**Third-Party Services (for MCP integration testing):**
- Google Workspace account (for Google Drive MCP)
- Atlassian account (for Confluence MCP)
- Slack workspace (for Slack MCP)

### Local Development Architecture

**Not containerized locally - run services directly:**

**Frontend:**
- Next.js dev server on localhost:3000
- Hot reload enabled
- Mock SPCS headers for auth

**Backend:**
- Node.js API on localhost:8000
- TypeScript watch mode
- Connects to local PostgreSQL, Redis, Neo4j, MinIO

**AI Service:**
- FastAPI on localhost:8001
- Python virtual environment
- uvicorn with reload

**Data Services (via Docker Compose):**
- PostgreSQL on localhost:5432
- Neo4j on localhost:7474 (UI), 7687 (Bolt)
- Redis on localhost:6379
- MinIO on localhost:9000 (API), 9001 (Console)

**Snowflake Connection:**
- Connect to actual Snowflake account (no local mock)
- Use service account credentials
- Create test database/schema
- Use sample gold layer data

### Environment Variables

**Frontend (.env.local):**
```
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_WS_URL=ws://localhost:8000
```

**Backend (.env):**
```
DATABASE_URL=postgresql://user:pass@localhost:5432/ekaix
REDIS_URL=redis://localhost:6379
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
FASTAPI_URL=http://localhost:8001
SNOWFLAKE_ACCOUNT=<account>
SNOWFLAKE_USER=<user>
SNOWFLAKE_PASSWORD=<password>
SNOWFLAKE_WAREHOUSE=<warehouse>
SNOWFLAKE_DATABASE=<database>
SNOWFLAKE_SCHEMA=<schema>
SNOWFLAKE_ROLE=<role>
```

**AI Service (.env):**
```
DATABASE_URL=postgresql://user:pass@localhost:5432/ekaix
REDIS_URL=redis://localhost:6379
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
SNOWFLAKE_ACCOUNT=<account>
SNOWFLAKE_USER=<user>
SNOWFLAKE_PASSWORD=<password>
SNOWFLAKE_WAREHOUSE=<warehouse>
SNOWFLAKE_DATABASE=<database>
SNOWFLAKE_SCHEMA=<schema>
SNOWFLAKE_ROLE=<role>
ANTHROPIC_API_KEY=<key>
OPENAI_API_KEY=<key>
GOOGLE_API_KEY=<key>
LANGSMITH_API_KEY=<key>
LANGSMITH_PROJECT=ekaix-dev
```

### Docker Compose for Data Services

Create `docker-compose.yml` for local data services:

**PostgreSQL:**
- Image: postgres:16-alpine
- Port: 5432
- Volume: ./data/postgres

**Neo4j:**
- Image: neo4j:5.x
- Ports: 7474, 7687
- Volume: ./data/neo4j
- Plugins: apoc

**Redis:**
- Image: redis:7-alpine
- Port: 6379
- Volume: ./data/redis
- Config: appendonly yes

**MinIO:**
- Image: minio/minio:latest
- Ports: 9000, 9001
- Volume: ./data/minio
- Command: server /data --console-address ":9001"

### Development Workflow

**First-time Setup:**
1. Clone repository
2. Run `docker-compose up -d` (data services)
3. Create Python venv, install dependencies
4. Run `npm install` in frontend and backend
5. Initialize databases (run migration scripts)
6. Set environment variables
7. Create Snowflake test database with sample data

**Daily Development:**
1. Start data services: `docker-compose start`
2. Start AI service: `uvicorn main:app --reload --port 8001`
3. Start backend: `npm run dev` (in backend folder)
4. Start frontend: `npm run dev` (in frontend folder)
5. Access UI at http://localhost:3000

**Hot Reload:**
- Frontend: Next.js auto-reloads on file changes
- Backend: nodemon restarts on file changes
- AI Service: uvicorn reloads on file changes

**Testing:**
- Unit tests: `npm test` (frontend/backend), `pytest` (AI service)
- Integration tests: Against actual Snowflake account
- E2E tests: Playwright for full user workflows

---

## Integration Requirements

### Snowflake Native App Packaging

**Application Package Structure:**

```
/manifest.yml                 - App manifest
/setup_script.sql            - Installation script
/spcs_services/
  /frontend/
    /Dockerfile
    /nginx.conf
    /spec.yaml            - SPCS service spec
  /backend/
    /Dockerfile
    /spec.yaml
  /ai-service/
    /Dockerfile
    /spec.yaml
  /postgresql/
    /Dockerfile
    /init.sql
    /spec.yaml
  /neo4j/
    /Dockerfile
    /spec.yaml
  /redis/
    /Dockerfile
    /spec.yaml
  /minio/
    /Dockerfile
    /spec.yaml
/stored_procedures/
  /discovery.py
  /profiling.py
  /validation.py
  /publishing.py
/README.md
```

**Manifest Requirements:**

```yaml
manifest_version: 1
version:
  name: "1.0"
  label: "v1.0"
  comment: "Initial release"

artifacts:
  setup_script: setup_script.sql
  readme: README.md
  
configuration:
  log_level: INFO
  trace_level: OFF

privileges:
  - CREATE DATABASE:
      description: "Create schemas for semantic views and agents"
  - CREATE CORTEX AGENT:
      description: "Deploy Cortex Agents to Snowflake Intelligence"
  - USAGE ON SNOWFLAKE.CORTEX:
      description: "Access Cortex AI features"
  - CREATE COMPUTE POOL:
      description: "Run SPCS services"

references:
  - database:
      label: "Gold Layer Database"
      description: "Reference to customer's gold layer database"
      privileges: ["SELECT"]
      register_callback: app.callback.register_database
```

**Setup Script (`setup_script.sql`):**

1. Create application database
2. Create schemas: ekaiX_app, ekaiX_semantic_views, ekaiX_agents
3. Create compute pool for SPCS
4. Deploy SPCS services (frontend, backend, ai-service, data services)
5. Create stored procedures (discovery, profiling, validation, publishing)
6. Create application roles (ekaiX_user, ekaiX_admin)
7. Grant minimal privileges
8. Create callback procedures for REFERENCES

**SPCS Service Specifications:**

Each service needs spec.yaml:

```yaml
spec:
  containers:
    - name: service-name
      image: /ekaix_db/ekaix_schema/ekaix_repo/service-name:latest
      env:
        DATABASE_URL: <connection_string>
        # Other env vars
      resources:
        requests:
          cpu: "1"
          memory: "2Gi"
        limits:
          cpu: "2"
          memory: "4Gi"
      readinessProbe:
        port: 8080
        path: /health
  endpoints:
    - name: api
      port: 8080
      public: true  # Only for frontend
```

**Dockerfile Requirements:**

- Multi-stage builds (reduce image size)
- Security scanning passing
- Non-root user
- Health check endpoints
- Graceful shutdown handling
- Signal handling (SIGTERM)

### MCP Server Configuration

**Supported MCP Servers:**

1. **Google Drive** (via Snowflake managed or community MCP)
2. **Confluence** (via community MCP)
3. **Slack** (via community MCP)
4. **SharePoint** (via community MCP)
5. **Notion** (via community MCP)

**OAuth Configuration:**

User must authenticate once via OAuth flow:
1. User clicks "Connect Google Drive" in Settings
2. Frontend redirects to OAuth consent screen
3. User grants permissions
4. Backend stores OAuth tokens securely
5. MCP server uses stored tokens for API calls

**MCP Server URLs (must search for latest 2026 versions):**

Configure in Deep Agents via `mcp_servers` parameter. Use langchain-mcp-adapters for integration.

**Required MCP Capabilities:**
- Search/query functionality
- Document fetch
- Respect user permissions
- Token refresh handling

---

## AI Agent Instructions (Critical for Claude Code)

### General Instructions

**You are building ekaiX using the latest 2026 technologies. Your training data is from 2024, so you MUST search for updated documentation before implementing any component.**

**Before coding any module, ALWAYS:**

1. **Search for latest package versions:**
   - `npm info <package> version` for Node.js packages
   - `pip index versions <package>` for Python packages
   - Check GitHub releases for breaking changes

2. **Search for latest documentation:**
   - Snowflake documentation (docs.snowflake.com)
   - LangChain/LangGraph documentation (docs.langchain.com)
   - Deep Agents documentation
   - Next.js documentation
   - FastAPI documentation
   - Neo4j documentation

3. **Search for 2026 best practices:**
   - React patterns (Server Components, Suspense, etc.)
   - TypeScript strict mode
   - Python type hints
   - Async patterns
   - Security best practices

**Technology Stack Priority:**

1. **Use latest stable versions** (not beta/RC unless specified)
2. **Prefer native solutions** over third-party when available
3. **TypeScript strict mode** for all frontend/backend code
4. **Python type hints** for all Python code
5. **Async by default** where applicable

### Specific Component Instructions

**React Frontend:**

```bash
# Search for latest Next.js version and features
# As of your knowledge, Next.js 14 exists, but search for 15+ in 2026

# Required features:
- Server Components where applicable
- Client Components for interactivity
- Streaming for agent responses
- Suspense boundaries for loading states
- Error boundaries for error handling

# UI Library - search for latest:
- shadcn/ui or similar component library
- Tailwind CSS latest version
- Radix UI primitives

# State Management - search for latest patterns:
- Zustand or React Context (avoid Redux unless needed)
- TanStack Query for server state
- WebSocket/SSE for real-time updates

# Visualization:
- D3.js or Recharts for ERD graphs
- React Flow or Cytoscape for interactive graphs
```

**Node.js Backend:**

```bash
# Search for latest Node.js LTS version (20+)

# Framework - search for latest:
- Fastify (preferred) or Express
- Latest TypeScript version
- Latest @types/* packages

# Validation:
- Zod latest version for schema validation

# Database clients:
- pg for PostgreSQL (latest)
- ioredis for Redis (latest)
- minio for MinIO (latest)

# WebSocket/SSE:
- Search for latest real-time communication patterns
- ws library or native Fastify WebSocket support
```

**FastAPI AI Service:**

```bash
# Search for latest Python 3.11+ or 3.12 features

# Core framework:
- FastAPI latest version
- Pydantic v2 (search for latest)
- uvicorn latest with async workers

# LangChain/Deep Agents:
- CRITICAL: Search for "deepagents python 2026 latest"
- CRITICAL: Search for "langgraph 2026 latest version"
- CRITICAL: Search for "langchain-mcp-adapters 2026"
- Read updated documentation for API changes

# Snowflake:
- snowflake-connector-python latest
- snowflake-snowpark-python latest

# Neo4j:
- neo4j latest Python driver

# Redis:
- redis-py latest with async support

# Important: Deep Agents API may have changed since 2024
# Search for:
- create_deep_agent parameters
- Backend configuration
- Subagent configuration
- MCP integration patterns
```

**PostgreSQL Schema:**

```bash
# Search for PostgreSQL 16 new features
# JSONB improvements, performance enhancements

# Use:
- UUID primary keys
- JSONB for flexible schema
- Partitioning for large tables
- Row-level security policies
- Proper indexes
```

**Neo4j Queries:**

```bash
# Search for Neo4j 5.x Cypher syntax
# APOC procedures latest version

# Use:
- MERGE for upsert operations
- Proper indexes on node properties
- Efficient relationship queries
- APOC for complex operations
```

**Snowflake Stored Procedures:**

```bash
# CRITICAL: Search for "Snowflake Python stored procedures 2026"
# Search for "Snowflake EXECUTE AS CALLER examples"

# Required:
- Python 3.11 runtime
- EXECUTE AS CALLER for RCR
- Proper error handling
- Return VARIANT for complex data

# Search for latest Snowpark API:
- Session object methods
- DataFrame operations
- UDF creation patterns
```

**Docker/SPCS:**

```bash
# Search for "Snowflake SPCS 2026 best practices"
# Search for "SPCS service specification yaml"

# Required:
- Multi-stage Dockerfiles
- Security scanning
- Health check endpoints
- Graceful shutdown
- Resource limits
```

### Critical Don'ts

**DO NOT:**
1. Use deprecated APIs from 2024
2. Assume package APIs are same as your training data
3. Use mock data or simulations (production-ready only)
4. Hardcode any values (use environment variables)
5. Skip error handling
6. Ignore TypeScript/Python type errors
7. Use `any` type in TypeScript
8. Skip input validation
9. Implement without searching for latest docs first
10. Copy-paste old patterns without verifying they're current

**ALWAYS:**
1. Search for latest documentation
2. Use strict typing
3. Implement proper error handling
4. Add logging for debugging
5. Consider edge cases
6. Write production-ready code
7. Follow security best practices
8. Use async where applicable
9. Add comprehensive comments
10. Test against actual Snowflake account

### Error Handling Patterns

**Every component must:**

1. **Graceful degradation** - If Cortex AI unavailable, queue request and retry
2. **User-friendly errors** - Never show stack traces to users
3. **Retry logic** - Exponential backoff for transient failures
4. **Circuit breaker** - Prevent cascade failures
5. **Logging** - Structured JSON logs with context
6. **Monitoring** - Emit metrics for observability

**Example pattern:**

```typescript
// Frontend
try {
  const response = await api.call()
  return response.data
} catch (error) {
  if (error.status === 429) {
    // Rate limit - show friendly message
    showNotification('Please wait a moment and try again')
  } else if (error.status >= 500) {
    // Server error - log and show generic message
    logError(error)
    showNotification('Something went wrong. Our team has been notified.')
  } else {
    // Client error - show specific message
    showNotification(error.message)
  }
}
```

### Testing Requirements

**Unit Tests:**
- Jest/Vitest for frontend
- pytest for Python
- Mock external dependencies
- 80%+ coverage target

**Integration Tests:**
- Test against actual Snowflake account
- Test RCR enforcement
- Test MCP integrations
- Test Deep Agents workflows

**E2E Tests:**
- Playwright for full user journeys
- Test critical paths:
  - Database discovery
  - BRD capture
  - Semantic view generation
  - Publishing

**Performance Tests:**
- Load test with 50 concurrent users
- Test schema discovery with 500 tables
- Test profiling with 1M+ rows

---

## Production Deployment Checklist

### Pre-Deployment

**Code Quality:**
- [ ] All TypeScript strict mode errors resolved
- [ ] All Python type hints added
- [ ] No console.log or print statements in production code
- [ ] All TODOs resolved or documented
- [ ] Code review completed
- [ ] Security scan passing (no critical vulnerabilities)

**Testing:**
- [ ] All unit tests passing
- [ ] All integration tests passing
- [ ] E2E tests passing
- [ ] Performance tests passing
- [ ] Load tests passing (50 concurrent users)
- [ ] RCR enforcement verified

**Configuration:**
- [ ] Environment variables documented
- [ ] Secrets management configured
- [ ] SPCS compute pool sized appropriately
- [ ] Database connection pools configured
- [ ] Rate limits configured
- [ ] Timeout values set

**Documentation:**
- [ ] README.md complete
- [ ] API documentation generated
- [ ] User guide written
- [ ] Admin guide written
- [ ] Troubleshooting guide written

### Snowflake Native App Packaging

**Application Package:**
- [ ] manifest.yml validated
- [ ] setup_script.sql tested
- [ ] All SPCS service specs defined
- [ ] Dockerfiles optimized (multi-stage builds)
- [ ] Images scanned for vulnerabilities
- [ ] Images pushed to Snowflake registry
- [ ] Stored procedures tested
- [ ] Callback procedures implemented

**Privileges:**
- [ ] Minimum required privileges documented
- [ ] REFERENCES callback working
- [ ] RCR permissions tested
- [ ] Application roles defined
- [ ] Grant statements tested

**Testing in Consumer Account:**
- [ ] Install from package succeeds
- [ ] All services start successfully
- [ ] Health checks passing
- [ ] Can discover databases
- [ ] Can create data product end-to-end
- [ ] Can publish Cortex Agent
- [ ] RCR working (test with different roles)
- [ ] Workspace isolation working
- [ ] Sharing working

### Post-Deployment

**Monitoring:**
- [ ] Logging configured (structured JSON)
- [ ] Metrics collection configured
- [ ] Alerting rules defined
- [ ] Dashboard created
- [ ] LangSmith traces working

**Documentation:**
- [ ] Installation guide published
- [ ] User guide published
- [ ] API documentation published
- [ ] Troubleshooting guide published
- [ ] Release notes published

**Support:**
- [ ] Support channel defined
- [ ] Escalation process documented
- [ ] Known issues documented
- [ ] FAQ created

**Compliance:**
- [ ] Security review completed
- [ ] Privacy review completed
- [ ] Data retention policies defined
- [ ] Audit logging verified

---

## Complete Build Checklist

### Phase 1: Local Development Environment Setup

**Infrastructure Setup:**
- [ ] Docker Desktop installed and running
- [ ] docker-compose.yml created with:
  - [ ] PostgreSQL 16
  - [ ] Neo4j 5.x with APOC
  - [ ] Redis 7
  - [ ] MinIO latest
- [ ] All containers start successfully
- [ ] Can connect to all services locally

**Snowflake Setup:**
- [ ] Snowflake account with SPCS enabled
- [ ] Test database created
- [ ] Test schema created
- [ ] Sample gold layer data loaded (customers, orders, products minimum)
- [ ] Service account created with appropriate permissions
- [ ] Connection tested from local machine

**Development Tools:**
- [ ] Node.js 20+ LTS installed
- [ ] Python 3.11+ installed
- [ ] Git configured
- [ ] IDE configured (VSCode or JetBrains)
- [ ] Environment variables template created (.env.example)

### Phase 2: Backend Foundation

**Database Schema (PostgreSQL):**
- [ ] Migration framework setup (Alembic or similar)
- [ ] workspaces table created
- [ ] data_products table created
- [ ] data_product_shares table created
- [ ] business_requirements table created
- [ ] semantic_views table created
- [ ] artifacts table created
- [ ] audit_logs table created
- [ ] uploaded_documents table created
- [ ] Indexes created
- [ ] Row-level security policies created
- [ ] Test data seeded

**Neo4j Schema:**
- [ ] Database constraints created (unique FQNs)
- [ ] Indexes created on key properties
- [ ] APOC procedures installed
- [ ] Test ERD data loaded

**Redis Configuration:**
- [ ] Persistence enabled (RDB + AOF)
- [ ] Memory limits configured
- [ ] Eviction policy set
- [ ] Test key-value operations

**MinIO Configuration:**
- [ ] Buckets created (artifacts, documents, workspace)
- [ ] Versioning enabled
- [ ] Access policies configured
- [ ] Test file upload/download

### Phase 3: Node.js Backend API

**Project Setup:**
- [ ] Package.json created with dependencies
  - [ ] Latest Fastify or Express
  - [ ] Latest TypeScript
  - [ ] Latest Zod
  - [ ] Latest pg, ioredis, minio clients
  - [ ] Latest snowflake-sdk
- [ ] TypeScript strict mode configured
- [ ] ESLint and Prettier configured
- [ ] Project structure created

**Core Endpoints Implemented:**
- [ ] GET /health (health check)
- [ ] GET /auth/user (get current user from header)
- [ ] POST /session/create
- [ ] GET /databases (via Snowflake query)
- [ ] POST /databases/:id/reference
- [ ] GET /data-products (with workspace isolation)
- [ ] POST /data-products
- [ ] GET /data-products/:id
- [ ] PUT /data-products/:id
- [ ] DELETE /data-products/:id
- [ ] POST /data-products/:id/share
- [ ] POST /agent/message
- [ ] GET /agent/stream/:session_id (SSE)
- [ ] POST /documents/upload

**Middleware:**
- [ ] Request validation (Zod schemas)
- [ ] Error handling middleware
- [ ] Logging middleware (structured JSON)
- [ ] CORS configuration
- [ ] Rate limiting

**Database Integration:**
- [ ] PostgreSQL connection pool
- [ ] Neo4j connection
- [ ] Redis connection
- [ ] MinIO client
- [ ] Snowflake connection
- [ ] Connection health checks

**Testing:**
- [ ] Unit tests for core logic
- [ ] Integration tests for database operations
- [ ] API endpoint tests

### Phase 4: FastAPI AI Service

**Project Setup:**
- [ ] requirements.txt with latest versions:
  - [ ] CRITICAL: Search "deepagents python 2026" for latest
  - [ ] CRITICAL: Search "langgraph 2026" for latest
  - [ ] CRITICAL: Search "langchain-mcp-adapters 2026" for latest
  - [ ] Latest FastAPI
  - [ ] Latest Pydantic v2
  - [ ] Latest snowflake-connector-python
  - [ ] Latest neo4j driver
  - [ ] Latest redis-py async
- [ ] Virtual environment created
- [ ] Dependencies installed
- [ ] Type checking configured (mypy)
- [ ] Code formatting configured (black, isort)

**Deep Agents Setup:**
- [ ] Main orchestrator agent created
- [ ] Discovery subagent created
- [ ] Requirements subagent created
- [ ] Generation subagent created
- [ ] Validation subagent created
- [ ] Publishing subagent created
- [ ] Explorer subagent created
- [ ] System prompts defined for each agent
- [ ] Backend configuration (Composite: State, Store, Filesystem)

**Tools Implementation:**

**Snowflake Tools:**
- [ ] query_information_schema tool
- [ ] profile_table tool
- [ ] execute_rrc_query tool
- [ ] create_semantic_view tool
- [ ] create_cortex_agent tool
- [ ] grant_agent_access tool
- [ ] validate_sql tool

**Neo4j Tools:**
- [ ] query_erd_graph tool
- [ ] update_erd tool
- [ ] get_relationship_path tool
- [ ] classify_entity tool

**PostgreSQL Tools:**
- [ ] save_workspace_state tool
- [ ] load_workspace_state tool
- [ ] save_brd tool
- [ ] save_semantic_view tool
- [ ] log_agent_action tool

**MinIO Tools:**
- [ ] upload_artifact tool
- [ ] retrieve_artifact tool
- [ ] list_artifacts tool

**MCP Integration:**
- [ ] CRITICAL: Search "langchain-mcp-adapters 2026 documentation"
- [ ] Google Drive MCP configured
- [ ] Confluence MCP configured
- [ ] Slack MCP configured
- [ ] OAuth flow implemented
- [ ] Token storage secure

**Core Workflows:**
- [ ] Database discovery workflow
- [ ] Schema profiling workflow
- [ ] ERD inference workflow
- [ ] Business requirements capture workflow
- [ ] Semantic view generation workflow
- [ ] Validation workflow
- [ ] Publishing workflow

**LangSmith Integration:**
- [ ] Project created
- [ ] Tracing configured
- [ ] Can view traces in dashboard

**Testing:**
- [ ] Unit tests for tools
- [ ] Integration tests for workflows
- [ ] Test against actual Snowflake account

### Phase 5: Snowflake Stored Procedures

**Discovery Procedure:**
- [ ] CRITICAL: Search "Snowflake Python stored procedures 2026"
- [ ] Created with EXECUTE AS CALLER
- [ ] Queries INFORMATION_SCHEMA
- [ ] Returns table/column metadata as VARIANT
- [ ] Tested with different user roles (RCR verification)

**Profiling Procedure:**
- [ ] Created with EXECUTE AS CALLER
- [ ] Statistical profiling logic implemented
- [ ] HyperLogLog for cardinality
- [ ] Pattern detection
- [ ] Sample value extraction
- [ ] Tested with large tables (1M+ rows)

**Validation Procedure:**
- [ ] Created with EXECUTE AS CALLER
- [ ] YAML parsing
- [ ] Column existence check
- [ ] SQL compilation test (EXPLAIN)
- [ ] Returns validation results

**Publishing Procedure:**
- [ ] Created with EXECUTE AS OWNER
- [ ] Creates semantic view
- [ ] Creates Cortex Agent
- [ ] Grants access to caller's role
- [ ] Returns agent FQN and URL
- [ ] Tested end-to-end

**Callback Procedures:**
- [ ] REFERENCES callback implemented
- [ ] Tested with Python Permission SDK

### Phase 6: React Frontend

**Project Setup:**
- [ ] CRITICAL: Search "Next.js latest version 2026"
- [ ] Next.js project created
- [ ] TypeScript strict mode configured
- [ ] Tailwind CSS configured
- [ ] Component library installed (shadcn/ui or similar)
- [ ] Project structure created

**Core Pages:**
- [ ] / (Home - workspace dashboard)
- [ ] /data-products (list data products)
- [ ] /data-products/new (create new - chat interface)
- [ ] /data-products/:id (view/edit data product)
- [ ] /settings (user settings, MCP connections)

**Chat Interface Components:**
- [ ] ChatContainer (main layout)
- [ ] MessageThread (conversation history)
- [ ] MessageInput (user input with file upload)
- [ ] StreamingResponse (real-time agent responses)
- [ ] ArtifactPanel (right panel for ERD, previews, etc.)
- [ ] ERDVisualization (interactive graph)
- [ ] DataPreview (table with export)
- [ ] YAMLViewer (syntax highlighted)

**Workspace Components:**
- [ ] DataProductCard (list item)
- [ ] DataProductList (grid/list view)
- [ ] ShareDialog (share with users)
- [ ] DeleteConfirmation

**Database Selection Components:**
- [ ] DatabaseSelector (dropdown/search)
- [ ] SchemaSelector (multi-select)
- [ ] ConnectionStatus indicator

**State Management:**
- [ ] Zustand store or React Context setup
- [ ] TanStack Query for API calls
- [ ] WebSocket/SSE connection for streaming

**API Integration:**
- [ ] API client created (axios or fetch wrapper)
- [ ] All backend endpoints integrated
- [ ] Error handling
- [ ] Loading states
- [ ] Success/error notifications

**Real-time Features:**
- [ ] SSE connection for agent streaming
- [ ] Message buffering for smooth display
- [ ] Reconnection logic on disconnect

**Testing:**
- [ ] Component unit tests (Jest/Vitest)
- [ ] Integration tests
- [ ] E2E tests (Playwright)

### Phase 7: End-to-End Integration

**Local Environment Testing:**
- [ ] All services start successfully
- [ ] Frontend connects to backend
- [ ] Backend connects to AI service
- [ ] AI service connects to Snowflake
- [ ] Can authenticate (mock SPCS header)
- [ ] Can select database
- [ ] Can trigger discovery
- [ ] Can see ERD visualization
- [ ] Can conduct BRD interview
- [ ] Can generate semantic view
- [ ] Can see validation results
- [ ] Can publish (test mode)
- [ ] Can share data product
- [ ] Can delete data product

**RCR Verification:**
- [ ] Test with multiple Snowflake roles
- [ ] Verify role A sees only their data
- [ ] Verify role B sees different data
- [ ] Verify no privilege elevation

**Document Upload:**
- [ ] Can upload PDF
- [ ] Cortex Document AI extracts content
- [ ] Content used in conversation
- [ ] File stored in MinIO

**MCP Integration:**
- [ ] Can connect Google Drive
- [ ] Can search Confluence
- [ ] Agent uses external context
- [ ] OAuth refresh working

**Error Scenarios:**
- [ ] Cortex AI unavailable (graceful degradation)
- [ ] Database connection lost (reconnect)
- [ ] Invalid YAML (error shown, retry)
- [ ] User cancels operation (state preserved)
- [ ] Session timeout (can resume)

### Phase 8: Snowflake Native App Packaging

**SPCS Service Dockerfiles:**

**Frontend Dockerfile:**
- [ ] CRITICAL: Search "Next.js Docker production 2026"
- [ ] Multi-stage build
- [ ] Node.js base image
- [ ] Dependencies installed
- [ ] Production build
- [ ] Non-root user
- [ ] Health check endpoint
- [ ] Tested locally

**Backend Dockerfile:**
- [ ] Multi-stage build
- [ ] Node.js base image
- [ ] Dependencies installed
- [ ] TypeScript compiled
- [ ] Non-root user
- [ ] Health check endpoint
- [ ] Tested locally

**AI Service Dockerfile:**
- [ ] Multi-stage build
- [ ] Python base image
- [ ] Dependencies installed
- [ ] Non-root user
- [ ] Health check endpoint
- [ ] Tested locally

**PostgreSQL Dockerfile:**
- [ ] PostgreSQL base image
- [ ] Initialization scripts
- [ ] Extensions installed
- [ ] Tested locally

**Neo4j Dockerfile:**
- [ ] Neo4j base image
- [ ] APOC plugin
- [ ] Initialization scripts
- [ ] Tested locally

**Redis Dockerfile:**
- [ ] Redis base image
- [ ] Configuration file
- [ ] Persistence enabled
- [ ] Tested locally

**MinIO Dockerfile:**
- [ ] MinIO base image
- [ ] Initialization script
- [ ] Tested locally

**SPCS Service Specs:**
- [ ] frontend/spec.yaml created
- [ ] backend/spec.yaml created
- [ ] ai-service/spec.yaml created
- [ ] postgresql/spec.yaml created
- [ ] neo4j/spec.yaml created
- [ ] redis/spec.yaml created
- [ ] minio/spec.yaml created
- [ ] Resource limits defined
- [ ] Health checks configured
- [ ] Environment variables configured

**Application Package Files:**
- [ ] manifest.yml created and validated
- [ ] setup_script.sql created
- [ ] README.md created
- [ ] All stored procedures included
- [ ] Callback procedures included

**Image Registry:**
- [ ] CRITICAL: Search "Snowflake image registry 2026"
- [ ] Image repository created
- [ ] All images tagged correctly
- [ ] All images pushed to registry
- [ ] Vulnerability scans passing

**Application Package Testing:**
- [ ] Package builds successfully
- [ ] Can create application from package
- [ ] Setup script runs without errors
- [ ] All SPCS services start
- [ ] Health checks passing
- [ ] Can access frontend UI
- [ ] End-to-end workflow works

**Consumer Account Testing:**
- [ ] Install in test consumer account
- [ ] All privileges granted correctly
- [ ] Can grant database REFERENCES
- [ ] Can discover databases
- [ ] Can create data product
- [ ] Can publish Cortex Agent
- [ ] Agent accessible in Snowflake Intelligence
- [ ] RCR working correctly
- [ ] Workspace isolation working
- [ ] Sharing working

### Phase 9: Documentation

**Code Documentation:**
- [ ] All functions/classes have docstrings
- [ ] Complex logic commented
- [ ] TypeScript interfaces documented
- [ ] API endpoints documented (OpenAPI spec)

**User Documentation:**
- [ ] Installation guide
- [ ] Quick start guide
- [ ] User guide (with screenshots)
- [ ] Database requirements guide
- [ ] Troubleshooting guide
- [ ] FAQ

**Admin Documentation:**
- [ ] Deployment guide
- [ ] Configuration reference
- [ ] Monitoring guide
- [ ] Backup/restore procedures
- [ ] Security best practices

**Developer Documentation:**
- [ ] Architecture overview
- [ ] Component diagrams
- [ ] Database schema documentation
- [ ] API reference
- [ ] Development setup guide
- [ ] Contributing guide

### Phase 10: Production Readiness

**Security:**
- [ ] All secrets in environment variables (no hardcoded)
- [ ] Input validation on all endpoints
- [ ] SQL injection prevention verified
- [ ] XSS prevention verified
- [ ] CSRF protection configured
- [ ] Rate limiting configured
- [ ] Security headers configured
- [ ] Dependency vulnerability scan passing

**Performance:**
- [ ] Load testing completed (50 concurrent users)
- [ ] Database queries optimized (indexes, query plans)
- [ ] Connection pooling configured
- [ ] Caching implemented where appropriate
- [ ] Large file handling optimized
- [ ] Memory leaks checked

**Reliability:**
- [ ] Error handling comprehensive
- [ ] Retry logic implemented
- [ ] Circuit breakers implemented
- [ ] Graceful shutdown handling
- [ ] Health checks working
- [ ] Logging comprehensive

**Observability:**
- [ ] Structured logging (JSON)
- [ ] Log levels appropriate
- [ ] Metrics collection configured
- [ ] LangSmith traces working
- [ ] Dashboard created
- [ ] Alerts configured

**Data Quality:**
- [ ] Gold layer quality gate working (>60% threshold)
- [ ] BRD completeness validation working
- [ ] YAML syntax validation working
- [ ] SQL compilation validation working
- [ ] User approval gate working

**Compliance:**
- [ ] Audit logging complete
- [ ] Data retention policies defined
- [ ] Privacy policy reviewed
- [ ] Terms of service reviewed
- [ ] Security assessment completed

### Phase 11: Final Verification

**Functional Testing:**
- [ ] Can install from Snowflake Marketplace
- [ ] Can authenticate via Snowflake
- [ ] Can grant database access
- [ ] Can discover schemas (multiple databases)
- [ ] Can see ERD visualization
- [ ] Can upload documents (PDF, DOCX)
- [ ] Can connect MCP servers
- [ ] Can create data product through conversation
- [ ] Can modify requirements mid-conversation
- [ ] Can preview results
- [ ] Can export preview
- [ ] Can publish to Snowflake Intelligence
- [ ] Published agent works
- [ ] Can share data product
- [ ] Can delete data product
- [ ] Can view audit trail

**Non-Functional Testing:**
- [ ] Response times acceptable (<3s for agent start)
- [ ] Discovery completes in <10 min (100 tables)
- [ ] Generation completes in <3 min
- [ ] UI responsive on mobile
- [ ] Accessibility (screen reader, keyboard navigation)
- [ ] 50 concurrent users supported
- [ ] 500 table schemas supported
- [ ] 1M+ row profiling working

**Multi-User Testing:**
- [ ] User A cannot see User B's data products
- [ ] Sharing works correctly
- [ ] Edit permissions work
- [ ] View permissions work
- [ ] RCR enforced (different users see different data)

**Edge Cases:**
- [ ] Empty schemas handled gracefully
- [ ] Very large schemas (500+ tables) handled
- [ ] Network interruptions handled
- [ ] Long conversations (100+ messages) handled
- [ ] Complex ERDs (100+ relationships) handled
- [ ] Concurrent edits handled

**Documentation Complete:**
- [ ] All documentation written
- [ ] All screenshots updated
- [ ] All links working
- [ ] Version numbers correct
- [ ] Release notes complete

---

## Success Criteria

**Deployment Success:**
- Application installs successfully in consumer Snowflake account
- All SPCS services start and health checks pass
- User can authenticate via Snowflake native auth
- User can grant database access via REFERENCES
- RCR enforced correctly (test with multiple roles)

**Functional Success:**
- User can complete full workflow in <35 minutes:
  - Database selection: <2 min
  - Discovery: <10 min
  - BRD capture: <15 min
  - Generation + validation: <5 min
  - Publishing: <3 min
- Generated semantic views work correctly >90% of time
- Published Cortex Agents accessible via Snowflake Intelligence
- User approval required before publish

**Quality Success:**
- Gold layer quality gate catches poor data (<60% quality)
- No false positives in quality gate (>60% quality passes)
- Semantic view validation catches errors (YAML syntax, missing columns)
- Preview results match published agent results
- Audit trail captures all actions

**UX Success:**
- Conversation feels natural (like talking to expert)
- Agent shows reasoning transparently
- User can interrupt/redirect at any point
- Real-time streaming responses (<3s to first token)
- Error messages helpful (not technical stack traces)

**Security Success:**
- No privilege elevation (RCR strictly enforced)
- Workspace isolation working (users can't see others' data)
- Sharing permissions enforced
- Audit logging complete
- No secrets in logs or error messages

**Performance Success:**
- UI loads in <2 seconds
- Agent responds in <3 seconds (first token)
- Discovery completes in <10 minutes (100 tables)
- Can handle 50 concurrent users
- No memory leaks over 24 hour period

---

## Notes for AI Coding Agent

**This is a complex, production-grade system. Break it down into phases and complete each phase fully before moving to the next.**

**For each component you build:**

1. **Search first** - Find latest 2026 documentation and examples
2. **Implement incrementally** - Build small pieces, test, iterate
3. **Test thoroughly** - Unit, integration, and manual testing
4. **Document as you go** - Add comments, update README
5. **Commit frequently** - Small, focused commits with clear messages

**When stuck:**

1. Search for latest documentation
2. Search for GitHub issues or Stack Overflow
3. Read error messages carefully
4. Check logs for context
5. Test in isolation (minimal reproducible example)

**Key principles:**

- **Production quality** - No shortcuts, no mock data
- **Type safety** - TypeScript strict, Python type hints
- **Error handling** - Every failure path handled
- **Logging** - Every important action logged
- **Testing** - Every feature tested
- **Documentation** - Every component documented

**You've got this. Build something amazing.**

---

END OF PRD
