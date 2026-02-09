# Snowflake Cortex Agent & Semantic View — Technical Reference

**Date:** 2026-02-08
**Purpose:** Reference document for ekaiX Generation and Publishing agents. All information sourced from live web research of docs.snowflake.com (Feb 2026).

---

## Table of Contents

1. [Cortex Agent Architecture](#1-cortex-agent-architecture)
2. [CREATE AGENT DDL](#2-create-agent-ddl)
3. [Cortex Agent REST API & Threads](#3-cortex-agent-rest-api--threads)
4. [Semantic Views Overview](#4-semantic-views-overview)
5. [CREATE SEMANTIC VIEW DDL](#5-create-semantic-view-ddl)
6. [Semantic View YAML (SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML)](#6-semantic-view-yaml)
7. [Semantic View vs Legacy Semantic Model](#7-semantic-view-vs-legacy-semantic-model)
8. [Expression Syntax & Rules](#8-expression-syntax--rules)
9. [Custom Instructions (AI_SQL_GENERATION / AI_QUESTION_CATEGORIZATION)](#9-custom-instructions)
10. [Verified Query Repository](#10-verified-query-repository)
11. [Semantic View Autopilot](#11-semantic-view-autopilot)
12. [Validation Rules](#12-validation-rules)
13. [Querying Semantic Views](#13-querying-semantic-views)
14. [Snowflake-Managed MCP Server](#14-snowflake-managed-mcp-server)
15. [Agent Run API Request/Response](#15-agent-run-api-requestresponse)
16. [Expression Failure Prevention Strategy for ekaiX](#16-expression-failure-prevention-strategy-for-ekaix)
17. [Multi-Target Publishing Implications](#17-multi-target-publishing-implications)

---

## 1. Cortex Agent Architecture

**GA: November 4, 2025** ([Release Notes](https://docs.snowflake.com/en/release-notes/2025/other/2025-11-04-cortex-agents))

Cortex Agents are intelligent orchestration systems with a 4-phase workflow:

1. **Planning** — Parses user requests, creates execution plans, splits complex requests into subtasks
2. **Tool Use** — Deploys Cortex Analyst, Cortex Search, or custom tools to retrieve/process data
3. **Reflection** — Evaluates results after each tool execution, decides next steps
4. **Monitor & Iterate** — Tracks performance metrics, collects user feedback

### Relationship: Agent vs Analyst

| | Cortex Agent | Cortex Analyst |
|---|---|---|
| **Role** | Orchestrator | Tool (within an Agent) |
| **Scope** | Structured + unstructured data | Structured data only (text-to-SQL) |
| **Uses** | Analyst, Search, custom tools | Semantic views/models |
| **Output** | Natural language + SQL + search results | SQL queries + results |

**Key insight:** Cortex Analyst is a **component tool** within a Cortex Agent. The Agent orchestrates across tools; Analyst specifically handles structured data via semantic views.

### Objects a Cortex Agent Needs

| Object | Purpose | Required? |
|--------|---------|-----------|
| **Semantic View** | Structured data querying via Cortex Analyst | At least one tool required |
| **Cortex Search Service** | Unstructured data search | Optional |
| **Custom Tools (SP/UDF)** | Business logic, backend integration | Optional |
| **Thread** | Multi-turn conversation state | Optional |

**Source:** [Cortex Agents Overview](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents)

---

## 2. CREATE AGENT DDL

```sql
CREATE [ OR REPLACE ] AGENT [ IF NOT EXISTS ] <name>
  [ COMMENT = '<comment>' ]
  [ PROFILE = '<profile_object>' ]
  FROM SPECIFICATION
  $$
  <specification_yaml>
  $$;
```

### Profile Parameter

```json
'{"display_name": "My Agent", "avatar": "snowflake", "color": "#D4A843"}'
```

### Specification YAML Structure

Max length: 100,000 bytes.

```yaml
models:
  orchestration: <model_name>          # e.g., "claude-3-5-sonnet"

orchestration:
  budget:
    seconds: <number>                  # Max execution time
    tokens: <number>                   # Max token budget

instructions:
  response: '<response_instructions>'          # How to format responses
  orchestration: '<orchestration_instructions>' # How to plan/select tools
  system: '<system_instructions>'              # System-level context

sample_questions:
  - question: '<sample_question>'
    answer: '<sample_answer>'

tools:
  - tool_spec:
      type: 'cortex_analyst_text_to_sql'  # OR 'cortex_search' OR 'custom_tool'
      name: '<tool_name>'
      description: '<tool_description>'
    tool_resources:
      <tool_name>:
        semantic_view: '<fully_qualified_semantic_view>'   # For cortex_analyst
        warehouse: '<warehouse>'
        # OR for cortex_search:
        # cortex_search_service: '<service_name>'
        # max_results: 10
```

### Tool Types in Agent Specification

| Type | Key | Purpose |
|------|-----|---------|
| `cortex_analyst_text_to_sql` | `semantic_view` or `semantic_model_file` | Text-to-SQL via semantic view |
| `cortex_search` | `cortex_search_service` | Document/unstructured search |
| `custom_tool` (generic) | Stored procedure or UDF | Custom business logic |

**Note:** For `cortex_analyst_text_to_sql`, exactly ONE of `semantic_model_file` or `semantic_view` must be provided. Snowflake recommends `semantic_view` for new implementations.

### Example: Complete Agent with Analyst + Search

```sql
CREATE OR REPLACE AGENT sales_agent
  COMMENT = 'Sales data analysis agent'
  PROFILE = '{"display_name": "Sales Analyst", "color": "#D4A843"}'
  FROM SPECIFICATION
  $$
  models:
    orchestration: claude-3-5-sonnet
  orchestration:
    budget:
      seconds: 120
      tokens: 10000
  instructions:
    response: 'Always provide data-driven answers with specific numbers.'
    orchestration: 'Use Analyst for structured data, Search for policy docs.'
  tools:
    - tool_spec:
        type: cortex_analyst_text_to_sql
        name: SalesAnalyst
        description: 'Analyze sales, revenue, and customer data'
      tool_resources:
        SalesAnalyst:
          semantic_view: 'DB.SCHEMA.SALES_SEMANTIC_VIEW'
          warehouse: 'COMPUTE_WH'
    - tool_spec:
        type: cortex_search
        name: PolicySearch
        description: 'Search company policy documents'
      tool_resources:
        PolicySearch:
          cortex_search_service: 'DB.SCHEMA.POLICY_SEARCH'
          max_results: 5
  $$;
```

**Source:** [CREATE AGENT](https://docs.snowflake.com/en/sql-reference/sql/create-agent)

---

## 3. Cortex Agent REST API & Threads

### Run API

**Endpoint:** `POST <account_url>/api/v2/cortex/agent:run`

**Authentication:** PATs, JWT (key pair), or OAuth.

**Request body includes:** model name, response instructions, tools list, tool resources, tool selection config, message history.

**Response:** Streaming — server streams back events token-by-token.

### Threads (Multi-Turn Conversations)

Threads persist conversation context server-side so the client doesn't need to send full history each turn.

**Workflow:**
1. Create a thread → get Thread ID
2. Pass Thread ID in `agent:run` request
3. Read message IDs from thread
4. Continue from any assistant message using `parent_message_id`

**Forking:** Pass any earlier assistant message ID as `parent_message_id` to fork the conversation from that point.

**Context in memory:** Threads can be configured to maintain context in memory, eliminating the need to send context at every turn.

**Sources:**
- [Cortex Agents REST API](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-rest-api)
- [Cortex Agents Run API](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-run)
- [Threads](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-threads)
- [Threads API](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-threads-rest-api)

---

## 4. Semantic Views Overview

Semantic Views are **native Snowflake schema-level objects** that define a semantic layer over physical tables. They are the recommended replacement for legacy YAML-file-based semantic models.

**Key benefits over legacy semantic models:**
- Native Snowflake integration with full RBAC, sharing, and catalog support
- Advanced features: derived metrics, access modifiers (PRIVATE/PUBLIC)
- Better governance integrated with privilege and sharing systems
- Simplified management — no YAML files on stages
- Relationship types auto-inferred from data and PKs

**Two ways to create:**
1. **DDL** — `CREATE SEMANTIC VIEW` SQL command
2. **YAML** — `SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML()` stored procedure

**Other management tools:**
- `SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW()` — Export existing view as YAML
- Semantic View Autopilot — AI-assisted generator in Snowsight
- Semantic View Editor — Visual editor in Snowsight

**DDL vs YAML — When to use which:**

| Approach | Best for | Pros | Cons |
|----------|----------|------|------|
| **DDL** (`CREATE SEMANTIC VIEW`) | Production deployments, CI/CD, version control | Full SQL control, scriptable, integrates with Snowflake RBAC | More verbose syntax |
| **YAML** (`SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML`) | Migration from legacy semantic models, programmatic generation | Familiar format for existing Cortex Analyst users, easier to generate from code | Must call stored procedure, less native |

**ekaiX recommendation:** Generate DDL for production deployment (CREATE SEMANTIC VIEW). Use YAML + `verify_only=TRUE` during the validation step before deploying.

**Source:** [Overview of Semantic Views](https://docs.snowflake.com/en/user-guide/views-semantic/overview)

---

## 5. CREATE SEMANTIC VIEW DDL

### Full Syntax

```sql
CREATE [ OR REPLACE ] SEMANTIC VIEW [ IF NOT EXISTS ] <name>
  [ COMMENT = '<comment>' ]
  [ COPY GRANTS ]
  TABLES (
    <table_alias> AS <fully_qualified_table_name>
      [ PRIMARY KEY ( <column> [, ...] ) ]
      [ COMMENT = '<comment>' ]
    [, ...]
  )
  [ RELATIONSHIPS (
    <relationship_name> AS
      <from_table_alias> ( <from_column> [, ...] )
        REFERENCES <to_table_alias> ( <to_column> [, ...] )
      [ COMMENT = '<comment>' ]
    [, ...]
  ) ]
  [ FACTS (
    [ PRIVATE | PUBLIC ] <table_alias>.<fact_name> AS <sql_expr>
      [ WITH SYNONYMS = ('<synonym>' [, ...]) ]
      [ COMMENT = '<comment>' ]
    [, ...]
  ) ]
  [ DIMENSIONS (
    [ PRIVATE | PUBLIC ] <table_alias>.<dim_name> AS <sql_expr>
      [ WITH SYNONYMS = ('<synonym>' [, ...]) ]
      [ COMMENT = '<comment>' ]
    [, ...]
  ) ]
  [ METRICS (
    [ PRIVATE | PUBLIC ] <table_alias>.<metric_name> AS <aggregate_expr>
      [ WITH SYNONYMS = ('<synonym>' [, ...]) ]
      [ COMMENT = '<comment>' ]
    [, ...]
    -- Window function metrics:
    [ PRIVATE | PUBLIC ] <table_alias>.<metric_name> AS
      <window_function>(<metric>) OVER (
        [PARTITION BY ...] [ORDER BY ...] [<windowFrameClause>]
      )
      [ WITH SYNONYMS = ('<synonym>' [, ...]) ]
      [ COMMENT = '<comment>' ]
  ) ]
  [ AI_SQL_GENERATION = '<instructions>' ]
  [ AI_QUESTION_CATEGORIZATION = '<instructions>' ]
;
```

### Clause Rules

| Rule | Detail |
|------|--------|
| **Clause order** | FACTS must come before DIMENSIONS |
| **Minimum requirement** | At least one dimension or metric |
| **Forward references** | Expressions can reference items defined in later clauses |
| **Access modifiers** | Facts and metrics can be PRIVATE; dimensions cannot |
| **Cross-table metrics** | Derived metrics (view-level) can combine metrics from multiple tables |

### Complete Example (TPC-H style)

```sql
CREATE OR REPLACE SEMANTIC VIEW sales_analysis
  COMMENT = 'Sales data analysis for Cortex Analyst'
  TABLES (
    customers AS db.schema.customers
      PRIMARY KEY (customer_id)
      COMMENT = 'Customer master data',
    orders AS db.schema.orders
      PRIMARY KEY (order_id)
      COMMENT = 'Sales orders',
    products AS db.schema.products
      PRIMARY KEY (product_id)
      COMMENT = 'Product catalog'
  )
  RELATIONSHIPS (
    orders_to_customers AS
      orders (customer_id) REFERENCES customers (customer_id)
      COMMENT = 'Each order belongs to one customer',
    orders_to_products AS
      orders (product_id) REFERENCES products (product_id)
      COMMENT = 'Each order line references a product'
  )
  FACTS (
    orders.unit_price AS unit_price
      WITH SYNONYMS = ('price', 'item price')
      COMMENT = 'Price per unit at time of sale',
    orders.quantity AS quantity
      WITH SYNONYMS = ('qty', 'units sold')
      COMMENT = 'Number of units in the order',
    orders.discount_amount AS discount_amount
      COMMENT = 'Discount applied to the order'
  )
  DIMENSIONS (
    customers.customer_name AS customer_name
      WITH SYNONYMS = ('client', 'buyer')
      COMMENT = 'Full name of the customer',
    customers.region AS region
      WITH SYNONYMS = ('area', 'territory')
      COMMENT = 'Geographic region of the customer',
    products.category AS product_category
      WITH SYNONYMS = ('product type', 'category')
      COMMENT = 'Product category classification',
    orders.order_date AS order_date
      WITH SYNONYMS = ('sale date', 'transaction date')
      COMMENT = 'Date the order was placed'
  )
  METRICS (
    orders.total_revenue AS SUM(orders.unit_price * orders.quantity)
      WITH SYNONYMS = ('revenue', 'sales', 'total sales')
      COMMENT = 'Total revenue from sales',
    orders.order_count AS COUNT(orders.order_id)
      WITH SYNONYMS = ('number of orders', 'total orders')
      COMMENT = 'Total number of orders',
    orders.avg_order_value AS AVG(orders.unit_price * orders.quantity)
      WITH SYNONYMS = ('average order', 'AOV')
      COMMENT = 'Average value per order',
    orders.total_discount AS SUM(orders.discount_amount)
      COMMENT = 'Total discounts given'
  )
  AI_SQL_GENERATION = 'Round all monetary values to 2 decimal places. When filtering by date, use DATE_TRUNC for period comparisons.'
  AI_QUESTION_CATEGORIZATION = 'If the question is about employee salaries or HR data, respond: This semantic view covers sales data only. Please contact HR for employee-related questions.'
;
```

**Sources:**
- [CREATE SEMANTIC VIEW](https://docs.snowflake.com/en/sql-reference/sql/create-semantic-view)
- [Example](https://docs.snowflake.com/en/user-guide/views-semantic/example)

---

## 6. Semantic View YAML

### SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML

```sql
CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML(
  '<fully_qualified_schema_name>',   -- e.g., 'DB.SCHEMA'
  '<yaml_specification>',
  [ <verify_only> ]                  -- TRUE = validate only, don't create
);
```

**verify_only = TRUE response:** `"YAML file is valid for creating a semantic view. No object has been created yet."`

**verify_only = FALSE (or omitted) success:** `"Semantic view was successfully created."`

**On failure:** Throws an exception with details.

### YAML Format

```yaml
name: sales_analysis
comment: "Sales data analysis for Cortex Analyst"

tables:
  - name: customers
    base_table:
      database: DB
      schema: SCHEMA
      table: CUSTOMERS
    primary_key:
      - customer_id
    columns:
      - name: customer_id
        data_type: NUMBER
      - name: customer_name
        data_type: VARCHAR
        synonyms:
          - "client"
          - "buyer"
        description: "Full name of the customer"

  - name: orders
    base_table:
      database: DB
      schema: SCHEMA
      table: ORDERS
    primary_key:
      - order_id
    columns:
      - name: order_id
        data_type: NUMBER
      - name: customer_id
        data_type: NUMBER
      - name: unit_price
        data_type: NUMBER
        description: "Price per unit at time of sale"
      - name: quantity
        data_type: NUMBER

relationships:
  - name: orders_to_customers
    from:
      table: orders
      columns:
        - customer_id
    to:
      table: customers
      columns:
        - customer_id

facts:
  - name: unit_price
    table: orders
    expr: unit_price
    synonyms:
      - "price"
    description: "Price per unit"

  - name: quantity
    table: orders
    expr: quantity
    description: "Units in order"

dimensions:
  - name: customer_name
    table: customers
    expr: customer_name
    synonyms:
      - "client name"
    description: "Full customer name"

  - name: order_date
    table: orders
    expr: order_date
    description: "Date of order"

metrics:
  - name: total_revenue
    table: orders
    expr: SUM(unit_price * quantity)
    synonyms:
      - "revenue"
      - "total sales"
    description: "Total revenue from sales"

  - name: order_count
    table: orders
    expr: COUNT(order_id)
    description: "Number of orders"
```

### Reading YAML from Existing Semantic View

```sql
SELECT SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW('DB.SCHEMA.SALES_ANALYSIS');
```

**Sources:**
- [SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML](https://docs.snowflake.com/en/sql-reference/stored-procedures/system_create_semantic_view_from_yaml)
- [YAML Specification](https://docs.snowflake.com/en/user-guide/views-semantic/semantic-view-yaml-spec)
- [SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW](https://docs.snowflake.com/en/sql-reference/functions/system_read_yaml_from_semantic_view)

---

## 7. Semantic View vs Legacy Semantic Model

| Aspect | Legacy Semantic Model | Semantic View |
|--------|----------------------|---------------|
| **Storage** | YAML file on Snowflake stage | Native Snowflake DB object |
| **Governance** | Manual file management | Full RBAC, sharing, catalog |
| **Expression types** | `measures` with `default_aggregation` | `facts` (row-level) + `metrics` (aggregated) |
| **Relationships** | Explicit `join_type` + `relationship_type` | Auto-inferred from data & PKs |
| **Access control** | None | PRIVATE/PUBLIC modifiers |
| **Derived metrics** | Not supported | View-level metrics across tables |
| **Custom instructions** | `module_custom_instructions` in YAML | `AI_SQL_GENERATION` / `AI_QUESTION_CATEGORIZATION` clauses |
| **Verified queries** | In YAML file | VQR integration |
| **Backward compat** | Still works with Cortex Analyst | N/A |
| **Recommendation** | Legacy — for existing implementations | **Recommended** for new work |

### Facts vs Measures

**Legacy (measures):**
```yaml
measures:
  - name: total_revenue
    expr: revenue
    default_aggregation: sum
    description: "Total revenue"
```

**Semantic View (facts + metrics):**
```sql
-- Facts are ROW-LEVEL (unaggregated)
FACTS (
  orders.revenue AS revenue COMMENT = 'Revenue per order line'
)
-- Metrics are AGGREGATED
METRICS (
  orders.total_revenue AS SUM(orders.revenue) COMMENT = 'Total revenue'
)
```

**Key difference:** Semantic views separate the raw row-level data (facts) from the aggregation logic (metrics). Legacy models conflated both into `measures` with `default_aggregation`.

---

## 8. Expression Syntax & Rules

### Facts & Dimensions (Row-Level)

- Must be **unaggregated** — no SUM(), COUNT(), AVG() etc.
- Can be simple column references: `column_name`
- Can be SQL expressions: `unit_price * quantity`
- Can use SQL functions: `UPPER(customer_name)`, `DATE_TRUNC('month', order_date)`
- Can use CASE expressions: `CASE WHEN status = 'ACTIVE' THEN 1 ELSE 0 END`
- **Cannot** use: aggregates, window functions, subqueries

### Metrics (Aggregate-Level)

- **Must** include an aggregate function: SUM, COUNT, AVG, MIN, MAX, COUNT DISTINCT
- Can reference facts: `SUM(orders.revenue)`
- Can use compound expressions: `SUM(orders.unit_price * orders.quantity)`
- Can be derived (view-level): combine metrics from multiple tables

### Window Function Metrics

```sql
<table_alias>.<metric> AS <window_function>(<metric>) OVER (
  [PARTITION BY <dimension>]
  [ORDER BY <dimension>]
  [<windowFrameClause>]
)
```

### Best Practices (from Snowflake docs)

- Keep semantic views to **50-100 columns** max for best performance
- Use **business terminology** in all names and descriptions
- Make descriptions **detailed enough for non-technical users**
- Add **synonyms** for common alternative terms
- Mark helper expressions as **PRIVATE** if they shouldn't appear in query results

**Source:** [Best Practices for Semantic Views](https://docs.snowflake.com/en/user-guide/views-semantic/best-practices-dev)

---

## 9. Custom Instructions

### AI_SQL_GENERATION

Tells Cortex Analyst how to generate SQL statements.

```sql
AI_SQL_GENERATION = 'Ensure all numeric columns are rounded to 2 decimal points. When comparing dates, always use DATE_TRUNC for period-level comparisons. Use ILIKE for case-insensitive string matching.'
```

### AI_QUESTION_CATEGORIZATION

Tells Cortex Analyst how to classify/reject questions.

```sql
AI_QUESTION_CATEGORIZATION = 'If the question is about employee compensation, reject it and say: This data covers sales only. Contact HR for compensation questions.'
```

### Legacy Equivalent (module_custom_instructions)

In legacy YAML semantic models:
```yaml
module_custom_instructions:
  question_categorization: 'Block questions about...'
  sql_generation: 'Always round to 2 decimal places...'
```

**Source:** [Custom Instructions in Cortex Analyst](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/custom-instructions)

---

## 10. Verified Query Repository

The VQR provides pre-verified question-SQL pairs that Cortex Analyst uses as examples for similar questions.

### Format (in YAML)

```yaml
verified_queries:
  - name: monthly_revenue
    question: "What was the total revenue last month?"
    verified_at: "2026-02-01"
    sql: |
      SELECT
        DATE_TRUNC('month', orders.order_date) AS month,
        SUM(orders.unit_price * orders.quantity) AS total_revenue
      FROM orders
      WHERE orders.order_date >= DATEADD('month', -1, CURRENT_DATE())
      GROUP BY 1

  - name: top_customers
    question: "Who are our top 10 customers by revenue?"
    sql: |
      SELECT
        customers.customer_name,
        SUM(orders.unit_price * orders.quantity) AS total_revenue
      FROM orders
      JOIN customers ON orders.customer_id = customers.customer_id
      GROUP BY 1
      ORDER BY 2 DESC
      LIMIT 10
```

### Rules

- SQL must use **logical table and column names** from the semantic view, not physical table names
- Queries must be syntactically correct and actually answer the question
- **Max ~20 verified queries** recommended (optimization takes longer with more)
- Snowflake's optimization feature automatically analyzes VQR to improve broader query handling

### Optimization

Snowflake can analyze verified queries to find useful patterns to improve responses for similar (not identical) questions. Available via Snowsight or programmatically.

**Sources:**
- [Verified Query Repository](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/verified-query-repository)
- [Optimization with Verified Queries](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/analyst-optimization)

---

## 11. Semantic View Autopilot

Snowflake's AI-assisted generator (replaces the deprecated `semantic-model-generator` GitHub project).

### Inputs

| Source | What it provides |
|--------|-----------------|
| **Table metadata** | Column names, types, descriptions |
| **Query history** | Common query patterns, join paths |
| **Example SQL queries** | Custom context for relationships |
| **Tableau files (.TWB/.TDS)** | Existing business logic, calculated fields |

### How it works

1. Navigate to Snowsight → AI & ML → Cortex Analyst → Create New → Create New Semantic View
2. Select tables
3. Autopilot analyzes metadata + query history + optional context
4. Generates relationships, facts, dimensions, metrics, verified query suggestions
5. Human reviews and edits in the Semantic View Editor

### Limitations

- Only available via Snowsight UI (no programmatic API)
- Generates column-reference expressions primarily (not complex calculated expressions)
- Requires sufficient query history for best results

**Source:** [Semantic View Autopilot](https://docs.snowflake.com/en/user-guide/views-semantic/autopilot)

---

## 12. Validation Rules

Snowflake validates semantic views at creation time. Key rules to know:

### Structural Rules

- Must define **at least one dimension or metric**
- If no alias for a logical table, must use the logical table name in expressions
- **No circular relationships** — even through transitive paths
- **No self-referencing tables** (e.g., employee → manager hierarchy)

### Relationship Rules

- Many-to-one / one-to-one relationships work like FK constraints
- When `table_2 (col_2) REFERENCES table_1 (col_1)`: col_1 must be a PK, col_2 is the FK
- Relationship type is auto-inferred from data and PK definitions

### Expression Granularity Rules

- **Row-level expressions** (facts, dimensions) can refer to other row-level expressions at the **same or lower granularity**
- **Aggregate expressions** (metrics) must refer to row-level expressions using a **single aggregate**
- Cannot mix granularity levels in a single expression

### What Causes Validation Failures

| Error Type | Example | Fix |
|-----------|---------|-----|
| Column doesn't exist | `expr: nonexistent_col` | Validate against metadata before generating |
| Aggregate in fact/dimension | `FACTS (t.x AS SUM(col))` | Move to METRICS |
| Window function in fact | `FACTS (t.x AS ROW_NUMBER() OVER(...))` | Move to window metric |
| Circular relationship | A→B→C→A | Remove one link |
| Self-reference | employee→employee | Not supported |
| PK violation in relationship | FK references non-PK column | Ensure PK is defined |

**Source:** [How Snowflake Validates Semantic Views](https://docs.snowflake.com/en/user-guide/views-semantic/validation-rules)

---

## 13. Querying Semantic Views

Semantic views can be queried using the `SEMANTIC_VIEW()` construct:

```sql
SELECT * FROM SEMANTIC_VIEW(
  my_semantic_view
  DIMENSIONS customers.region, orders.order_date
  METRICS orders.total_revenue, orders.order_count
  WHERE customers.region = 'US'
);
```

### Rules

| Rule | Detail |
|------|--------|
| **Cannot mix FACTS + METRICS** | In the same `SEMANTIC_VIEW()` call |
| **FACTS + DIMENSIONS** | Only if dimensions can uniquely determine the facts, and all are from the same logical table |
| **METRICS + DIMENSIONS** | Dimension's table must be related to metric's table, with equal or lower granularity |
| **WHERE clause** | Can only refer to dimensions, facts, and expressions using them. Applied **before** metrics are computed |
| **Clause order** | METRICS/DIMENSIONS/FACTS appear in the result in the order specified |
| **Boolean expressions** | WHERE supports logical operators, built-in functions, and UDFs |

### Cortex Analyst Does This Automatically

When a user asks Cortex Analyst a question, it generates the `SEMANTIC_VIEW()` query automatically based on the semantic view definition. The user never writes these queries directly.

**Source:** [Querying Semantic Views](https://docs.snowflake.com/en/user-guide/views-semantic/querying), [SEMANTIC_VIEW Construct](https://docs.snowflake.com/en/sql-reference/constructs/semantic_view)

---

## 14. Snowflake-Managed MCP Server

**GA: November 4, 2025** — Snowflake now provides a managed MCP (Model Context Protocol) server that lets AI agents securely access Snowflake data.

### What It Does

- Serves Cortex Analyst, Cortex Search, and custom tools via the MCP standard interface
- Standardized tool discovery and invocation
- Built-in OAuth authentication
- RBAC governance for tools

### CREATE MCP SERVER

```sql
CREATE MCP SERVER my_mcp_server
  -- Configuration for tools, authentication, and access control
;
```

### Relevance to ekaiX

ekaiX currently runs its own agent orchestration (Deep Agents + LangChain) and connects to Snowflake directly. The Snowflake-managed MCP server is an **alternative architecture** where:

- Instead of ekaiX calling Snowflake APIs directly, ekaiX could register tools with the MCP server
- External agents (Cortex Code CLI, third-party) could then use ekaiX-generated semantic views via MCP
- Potential future integration point for Phase 8+ (SPCS deployment)

**Sources:**
- [Snowflake-Managed MCP Server](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-mcp)
- [CREATE MCP SERVER](https://docs.snowflake.com/en/sql-reference/sql/create-mcp-server)

---

## 15. Agent Run API Request/Response

### Request Body

```json
{
  "thread_id": "<thread_id>",
  "parent_message_id": "<msg_id>",
  "messages": [
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "What is the total revenue for 2023?"
        }
      ]
    }
  ]
}
```

### Key Fields

| Field | Required | Description |
|-------|----------|-------------|
| `thread_id` | Optional | Thread for multi-turn context |
| `parent_message_id` | Optional | Continue from specific message (must be assistant message) |
| `messages` | Required | Array of message objects with role + content |

### Inline Configuration

When calling `agent:run` **without** a pre-created Agent object, you can provide the full configuration inline:

```json
{
  "model": "<model_name>",
  "instructions": {
    "response": "...",
    "orchestration": "..."
  },
  "tools": [...],
  "tool_resources": {...},
  "messages": [...]
}
```

**Note:** You **cannot** override `models`, `instructions`, or `orchestration` when calling a pre-created Agent object.

### Response (Streaming)

The response is a server-sent events (SSE) stream with events delivered token-by-token.

**Source:** [Cortex Agents Run API](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-run)

---

## 16. Expression Failure Prevention Strategy for ekaiX

This section documents the strategy for ekaiX's Generation Agent to produce reliable semantic view YAML/DDL without expression failures.

### The Problem

LLMs writing free-form SQL expressions frequently produce:
- Invalid column references (hallucinated column names)
- Wrong SQL syntax (non-Snowflake functions)
- Type mismatches (aggregating VARCHAR, comparing NUMBER to DATE)
- Circular references between expressions

Snowflake's own Autopilot avoids this by NOT letting the LLM write free-form expressions — it generates column-reference-based expressions from metadata.

### Strategy: Template-Based Constrained Generation

**Principle:** The LLM selects a pattern + columns; code assembles the expression.

#### Step 1: Expression Templates

```python
FACT_TEMPLATES = {
    "column_ref":    "{column}",                          # Simple column reference
    "calculated":    "{col1} * {col2}",                   # Arithmetic
    "case_binary":   "CASE WHEN {col} {op} {val} THEN 1 ELSE 0 END",
    "date_trunc":    "DATE_TRUNC('{granularity}', {col})",
    "coalesce":      "COALESCE({col}, {default})",
    "cast":          "CAST({col} AS {type})",
    "concat":        "{col1} || ' ' || {col2}",
}

METRIC_TEMPLATES = {
    "sum":           "SUM({fact})",
    "count":         "COUNT({fact})",
    "count_distinct": "COUNT(DISTINCT {fact})",
    "avg":           "AVG({fact})",
    "min":           "MIN({fact})",
    "max":           "MAX({fact})",
    "sum_product":   "SUM({fact1} * {fact2})",
    "ratio":         "SUM({fact1}) / NULLIF(SUM({fact2}), 0)",
}
```

#### Step 2: Two-Pass Pipeline

**Pass 1 — Structure (LLM):**
- Given: table metadata (columns, types, PKs, FKs, descriptions)
- Output: JSON with semantic view structure:
  - Which columns are facts, dimensions, metrics
  - For each: template name + column bindings + synonyms + description
  - Relationships between tables

**Pass 2 — Assembly (Code):**
- Code takes the JSON and assembles expressions from templates
- Validates column names exist in metadata
- Validates types match template requirements
- Generates DDL or YAML

#### Step 3: Per-Expression Validation

For each generated expression, verify by running:
```sql
SELECT <expression> FROM <table> LIMIT 1;
```

If it fails, the code can:
1. Fall back to simple column reference
2. Log the failure for review
3. Skip the expression (mark as PRIVATE placeholder)

#### Step 4: Whole-Model Validation

```sql
CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML(
  'DB.SCHEMA',
  '<generated_yaml>',
  TRUE  -- verify_only
);
```

If validation fails, parse the error, fix the specific expression, retry (max 3 attempts).

### What This Covers

| Expression Type | Template Coverage | Notes |
|-----------------|-------------------|-------|
| Simple column refs | 100% | Most facts and dimensions |
| Arithmetic (a*b) | ~95% | Common calculated facts |
| Aggregates (SUM, COUNT, AVG) | ~95% | Standard metrics |
| Ratios | ~80% | Needs NULLIF guard |
| CASE expressions | ~70% | Limited to binary/ternary |
| Date functions | ~80% | DATE_TRUNC, DATEADD |
| Complex custom logic | ~30% | Falls back to VQR |

**For the ~5-10% of cases where templates don't fit:** Use Verified Queries instead. Complex business logic is better expressed as verified queries (full SQL) than as metric expressions.

### Comparison with Snowflake's Own Approach

| | Snowflake Autopilot | ekaiX Generation Agent |
|---|---|---|
| Expression source | Column references from metadata | Template-based from LLM-selected patterns |
| Complex logic | Not supported | Verified Query Repository |
| Validation | Built-in | Per-expression SQL + verify_only |
| Input context | Query history + Tableau files | BRD + discovery metadata + data quality |

---

## 17. Multi-Target Publishing Implications

ekaiX publishes to Cortex as P0 but also exports to other BI tools (see PRD). The semantic view definition serves as the universal source:

### What's Cortex-Only vs Universal

| Artifact | Cortex-Only? | Universal? |
|----------|-------------|------------|
| Semantic View (DDL/YAML) | Yes — native Snowflake object | No |
| CREATE AGENT | Yes — Snowflake Intelligence | No |
| Verified Query Repository | Yes — Cortex Analyst | No |
| Generated SQL views (CREATE VIEW) | No | Yes — any BI tool can query |
| Column metadata (names, types, descriptions) | Universal | Exported to PowerBI, Tableau, Looker |
| Metric definitions (SUM, AVG, etc.) | Cortex format | Adapted per target |

### Export Strategy

The same internal semantic definition can be exported as:

1. **Cortex Semantic View** — `CREATE SEMANTIC VIEW` DDL
2. **Cortex Agent** — `CREATE AGENT` with semantic view reference
3. **SQL Views** — `CREATE VIEW` for each logical table (universal access)
4. **PowerBI** — `.bim` tabular model (measures, columns, relationships)
5. **Tableau** — `.tds` data source (calculated fields, joins)
6. **LookML** — `.lkml` view + model files
7. **dbt** — `semantic_model` YAML in dbt-snowflake format

The Generation Agent produces the universal semantic definition; the Publishing Agent handles format-specific export.

---

## Appendix: Key Documentation URLs

| Topic | URL |
|-------|-----|
| Cortex Agents Overview | https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents |
| CREATE AGENT | https://docs.snowflake.com/en/sql-reference/sql/create-agent |
| Agent REST API | https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-rest-api |
| Agent Run API | https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-run |
| Agent Threads | https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-threads |
| Threads API | https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-threads-rest-api |
| Semantic Views Overview | https://docs.snowflake.com/en/user-guide/views-semantic/overview |
| CREATE SEMANTIC VIEW | https://docs.snowflake.com/en/sql-reference/sql/create-semantic-view |
| Semantic View Example | https://docs.snowflake.com/en/user-guide/views-semantic/example |
| YAML Specification | https://docs.snowflake.com/en/user-guide/views-semantic/semantic-view-yaml-spec |
| SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML | https://docs.snowflake.com/en/sql-reference/stored-procedures/system_create_semantic_view_from_yaml |
| SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW | https://docs.snowflake.com/en/sql-reference/functions/system_read_yaml_from_semantic_view |
| Best Practices | https://docs.snowflake.com/en/user-guide/views-semantic/best-practices-dev |
| Semantic View Autopilot | https://docs.snowflake.com/en/user-guide/views-semantic/autopilot |
| Custom Instructions | https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/custom-instructions |
| Verified Query Repository | https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/verified-query-repository |
| VQR Optimization | https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/analyst-optimization |
| Validation Rules | https://docs.snowflake.com/en/user-guide/views-semantic/validation-rules |
| Querying Semantic Views | https://docs.snowflake.com/en/user-guide/views-semantic/querying |
| SEMANTIC_VIEW Construct | https://docs.snowflake.com/en/sql-reference/constructs/semantic_view |
| Snowflake-Managed MCP Server | https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-mcp |
| CREATE MCP SERVER | https://docs.snowflake.com/en/sql-reference/sql/create-mcp-server |
| Cortex Analyst | https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst |
