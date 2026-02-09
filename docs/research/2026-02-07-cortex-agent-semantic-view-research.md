# Snowflake Cortex Agent & Semantic View Research
**Date:** February 7, 2026
**Status:** All information sourced from live web research (docs.snowflake.com, quickstarts, GitHub)

---

## 1. Cortex Agent Architecture

### What Is a Cortex Agent?

Cortex Agents are intelligent orchestration systems that plan tasks, select appropriate tools, execute those tools, and generate responses based on data analysis. They reached **General Availability on November 4, 2025**.

**Source:** [Cortex Agents GA Release](https://docs.snowflake.com/en/release-notes/2025/other/2025-11-04-cortex-agents)

### Core Architecture (4-Phase Workflow)

1. **Planning** - Parses user requests, creates execution plans, explores ambiguous queries, splits complex requests into subtasks
2. **Tool Use** - Deploys specialized tools (Cortex Analyst, Cortex Search, Custom Tools) to retrieve and process data
3. **Reflection** - Evaluates results after each tool execution, decides next steps or generates final response
4. **Monitor and Iterate** - Tracks performance metrics, collects user feedback

**Source:** [Cortex Agents Overview](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents)

### Relationship to Cortex Analyst

Cortex Analyst is a **tool** within a Cortex Agent. The Agent orchestrates across multiple tools, while Cortex Analyst specifically handles structured data by converting natural language to SQL using semantic views/models. Cortex Agents combine Cortex Analyst (structured data, ~90%+ SQL accuracy) with Cortex Search (unstructured data) and custom tools.

### What Objects Does a Cortex Agent Need?

| Object | Purpose | Required? |
|--------|---------|-----------|
| **Semantic View** | Structured data querying via Cortex Analyst | At least one tool required |
| **Cortex Search Service** | Unstructured data search | Optional |
| **Custom Tools (SP/UDF)** | Business logic, backend integration | Optional |
| **Thread** | Multi-turn conversation state | Optional (for stateful conversations) |

### CREATE AGENT DDL

```sql
CREATE [ OR REPLACE ] AGENT [ IF NOT EXISTS ] <name>
  [ COMMENT = '<comment>' ]
  [ PROFILE = '<profile_object>' ]
  FROM SPECIFICATION
  $$
  <specification_object>
  $$;
```

**Profile** (JSON string):
```json
{"display_name": "My Agent", "avatar": "icon.png", "color": "blue"}
```

**Specification** (YAML inside $$):
```yaml
models:
  orchestration: claude-4-sonnet    # or "auto" for automatic selection

orchestration:
  budget:
    seconds: 30
    tokens: 16000

instructions:
  response: "Respond in friendly but concise manner"
  orchestration: "For revenue use Analyst; for policy use Search"
  system: "You are a friendly agent that helps with business questions"
  sample_questions:
    - question: "What was our revenue last quarter?"
      answer: "I'll analyze the revenue data using our financial database."

tools:
  - tool_spec:
      type: "cortex_analyst_text_to_sql"
      name: "Analyst1"
      description: "Converts natural language to SQL for financial analysis"
  - tool_spec:
      type: "cortex_search"
      name: "Search1"
      description: "Searches company policy and documentation"

tool_resources:
  Analyst1:
    semantic_view: "db.schema.semantic_view_name"
  Search1:
    name: "db.schema.search_service_name"
    max_results: "5"
    title_column: "title"
    id_column: "doc_id"
```

**Max specification size:** 100,000 bytes

**Tool Types:**
- `cortex_analyst_text_to_sql` - Structured data via semantic views
- `cortex_search` - Unstructured data search
- `data_to_chart` - Vega-Lite visualization generation (new)
- Custom tools via stored procedures or UDFs

**Source:** [CREATE AGENT DDL](https://docs.snowflake.com/en/sql-reference/sql/create-agent)

### Access Control

- `SNOWFLAKE.CORTEX_USER` role (all AI features) OR `SNOWFLAKE.CORTEX_AGENT_USER` (agents only)
- `CREATE AGENT` privilege on schema
- `USAGE` on Cortex Search services
- `USAGE` on database, schema, tables referenced in semantic views
- `GRANT USAGE ON AGENT <name> TO ROLE <role>` for consumers

### REST API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v2/databases/{db}/schemas/{schema}/agents` | Create agent |
| GET | `/api/v2/databases/{db}/schemas/{schema}/agents/{name}` | Describe agent |
| PUT | `/api/v2/databases/{db}/schemas/{schema}/agents/{name}` | Update agent |
| DELETE | `/api/v2/databases/{db}/schemas/{schema}/agents/{name}` | Delete agent |
| GET | `/api/v2/databases/{db}/schemas/{schema}/agents` | List agents |
| POST | `/api/v2/databases/{db}/schemas/{schema}/agents/{name}:run` | Run agent (SSE) |
| POST | `/api/v2/cortex/agent:run` | Run without agent object (SSE) |

**Run API timeout:** 15 minutes

**SSE Event Types:**
- `response` - Final aggregated response
- `response.text.delta` - Individual text tokens
- `response.thinking.delta` - Reasoning tokens
- `response.tool_use` - Tool invocation
- `response.tool_result` - Tool execution result
- `response.status` - Execution status
- `response.chart` - Vega-Lite visualization
- `response.table` - SQL result data
- `error` - Fatal errors
- `metadata` - Thread message IDs

**Source:** [Cortex Agents REST API](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-rest-api), [Cortex Agents Run API](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-run)

### Thread Management

Threads maintain conversation context across turns. Create via `POST /cortex/threads`. Include `thread_id` and `parent_message_id` (start at 0) in run requests.

### Snowflake-Managed MCP Server

Snowflake provides a built-in MCP server exposing 5 tool types:
- `CORTEX_SEARCH_SERVICE_QUERY` - Unstructured search
- `CORTEX_ANALYST_MESSAGE` - SQL generation from semantic views
- `SYSTEM_EXECUTE_SQL` - Direct SQL execution
- `CORTEX_AGENT_RUN` - Nested agent invocation
- `GENERIC` - Custom UDFs/stored procedures

**Source:** [Snowflake MCP Server](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-mcp)

---

## 2. Semantic View Creation Methods

### Three Methods Available

| Method | Recommended For | Notes |
|--------|----------------|-------|
| **Snowsight UI (Autopilot)** | Initial setup, exploration | Auto-generates synonyms, sample values, descriptions. Accepts Tableau files as input |
| **SQL DDL** | Programmatic creation, CI/CD | `CREATE SEMANTIC VIEW` via JDBC/ODBC/SQL API |
| **YAML via stored procedure** | Migration from legacy YAML models | `SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML()` |

**CRITICAL:** Cannot add/alter tables, columns, or metadata within existing views. Must recreate using `CREATE OR REPLACE`. SQL updates overwrite manual Snowsight edits.

**Sources:**
- [Best Practices](https://docs.snowflake.com/en/user-guide/views-semantic/best-practices-dev)
- [Snowsight Creation](https://docs.snowflake.com/en/user-guide/views-semantic/ui)
- [SQL Commands](https://docs.snowflake.com/en/user-guide/views-semantic/sql)
- [Semantic View Autopilot](https://docs.snowflake.com/en/user-guide/views-semantic/autopilot)

### Semantic View Autopilot (Snowsight)

An AI-assisted generator that accepts 3 input types:
1. **Query History** - Examines past SQL patterns
2. **Table Metadata** - Extracts schema descriptions, key constraints, cardinality
3. **Context** (recommended) - Tableau files (TWB, TWBX, TDS <50MB) or example SQL queries with NL questions

Best practices: Start with 3-5 focused tables, use business terminology, review AI-generated descriptions.

**Source:** [Semantic View Autopilot](https://docs.snowflake.com/en/user-guide/views-semantic/autopilot)

### Can You Combine Methods?

No dual-preservation. If you edit in Snowsight and then deploy via SQL, the SQL version overwrites. Pick one workflow:
- **Snowsight-first:** Edit interactively, export to YAML for version control
- **Code-first:** Manage in Git, deploy via `SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML`

### Size Guidelines

Target **50-100 columns maximum** across all tables for optimal performance. Exceeding this may degrade latency due to AI context window constraints.

---

## 3. Complete Semantic View YAML Specification

### Root-Level Structure

```yaml
name: <string>                    # Required: semantic view name (becomes the object name)
description: <string>             # Optional: business description
comments: <string>                # Optional: additional comments

tables: [...]                     # Required: logical table definitions
relationships: [...]              # Optional: table relationships
metrics: [...]                    # Optional: view-level (derived) metrics
verified_queries: [...]           # Optional: example Q&A pairs for accuracy
```

### Tables

```yaml
tables:
  - name: <string>                # Logical table name (alias)
    description: <string>         # Business description
    base_table:
      database: <string>
      schema: <string>
      table: <string>

    primary_key:                   # Physical base table columns only
      - <column_name>

    foreign_keys:                  # NOT in semantic views YAML — use relationships instead
      # (This was in legacy format only)

    dimensions: [...]
    time_dimensions: [...]
    facts: [...]
    metrics: [...]                 # Table-scoped metrics
    filters: [...]
```

### Dimensions

```yaml
dimensions:
  - name: <string>                 # Business name
    synonyms: [<string>, ...]      # Alternative names for NL matching
    description: <string>          # Business description
    expr: <SQL expression>         # Row-level, unaggregated (e.g., "c_name", "LEFT(c_phone, 2)")
    data_type: <string>            # Snowflake data type (VARCHAR, NUMBER, etc.)
    unique: <boolean>              # Is this column unique?
    is_enum: <boolean>             # Is this an enumerated set of values?
    sample_values: [<any>, ...]    # Representative values (not real data if sensitive)
    cortex_search_service:         # Link to search service for literal matching
      service: <string>
      literal_column: <string>     # Optional
      database: <string>           # Optional
      schema: <string>             # Optional
```

### Time Dimensions

```yaml
time_dimensions:
  - name: <string>
    synonyms: [<string>, ...]
    description: <string>
    expr: <SQL expression>         # e.g., "o_orderdate"
    data_type: <string>            # DATE, TIMESTAMP, etc.
    unique: <boolean>
    sample_values: [<any>, ...]
```

### Facts (Row-Level, Unaggregated)

```yaml
facts:
  - name: <string>
    synonyms: [<string>, ...]
    description: <string>
    expr: <SQL expression>         # Row-level: "l_extendedprice * (1 - l_discount)"
    data_type: <string>
    access_modifier: public_access | private_access  # Default: public_access
```

**Facts vs Measures:** In the legacy Cortex Analyst YAML, these were called "measures" with `default_aggregation`. In semantic views, they are called "facts" and are strictly row-level (unaggregated). Aggregation is done in metrics. **"Facts" are backward compatible with "measures"** — Snowflake accepts both terms.

### Table-Scoped Metrics (Aggregated)

```yaml
metrics:
  - name: <string>
    synonyms: [<string>, ...]
    description: <string>
    expr: <SQL expression>         # Must contain aggregation: "SUM(l_extendedprice)"
    access_modifier: public_access | private_access  # Default: public_access
```

### Derived Metrics (View-Level, Cross-Table)

Defined at the root `metrics:` level (not inside a table). Added **September 30, 2025**.

```yaml
metrics:
  - name: <string>
    synonyms: [<string>, ...]
    description: <string>
    expr: <SQL expression>         # Can combine metrics from different tables
    access_modifier: public_access | private_access
```

Example: `profit_margin AS orders.total_revenue / orders.total_cost`

**Source:** [Derived Metrics Release](https://docs.snowflake.com/en/release-notes/2025/other/2025-09-30-semantic-view-derived-metrics)

### Relationships

```yaml
relationships:
  - name: <string>
    left_table: <logical table name>
    right_table: <logical table name>
    relationship_columns:
      - left_column: <column name>
        right_column: <column name>
```

**IMPORTANT:** `join_type` and `relationship_type` are NOT used in semantic views. Join types are **automatically inferred** by Snowflake.

**ASOF joins** are supported for time-range relationships:
```sql
RELATIONSHIPS (
  orders (order_date) REFERENCES prices (ASOF price_date)
)
```

**Source:** [YAML Specification](https://docs.snowflake.com/en/user-guide/views-semantic/semantic-view-yaml-spec)

### Filters

```yaml
filters:
  - name: <string>
    synonyms: [<string>, ...]
    description: <string>
    expr: <SQL expression>         # e.g., "o_orderstatus = 'F'"
```

### Verified Queries

```yaml
verified_queries:
  - name: <string>
    question: <string>             # Natural language question
    sql: <string>                  # Correct SQL answer
    verified_by: <string>          # Optional: person who verified
    verified_at: <integer>         # Optional: UNIX timestamp
    use_as_onboarding_question: <boolean>  # Show to new users as sample question
```

### Custom Instructions (SQL DDL Only, Not in YAML)

Set via `CREATE SEMANTIC VIEW` DDL clauses (NOT in YAML):

```sql
CREATE SEMANTIC VIEW my_view
  TABLES (...)
  ...
  AI_SQL_GENERATION 'Ensure all numeric columns are rounded to 2 decimal points'
  AI_QUESTION_CATEGORIZATION 'Reject all questions about employee salaries. Ask users to contact HR.'
```

**Module-level custom instructions** (in legacy YAML):
```yaml
module_custom_instructions:
  sql_generation: |
    Ensure all numeric columns are rounded to 2 decimal points.
  question_categorization: |
    Reject all questions about employee salaries.
```

**Source:** [Custom Instructions](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/custom-instructions)

### Access Modifiers

- `public_access` (default) - Visible in queries
- `private_access` - Hidden from query results but usable in calculations

Applied to **facts and metrics only**. Dimensions are always public.

---

## 4. Expression Syntax and Rules

### Expression Types

| Type | Aggregated? | Examples |
|------|-------------|---------|
| **Dimensions** | No (row-level) | `c_name`, `LEFT(c_phone, 2)`, `YEAR(o_orderdate)` |
| **Time Dimensions** | No (row-level) | `o_orderdate` |
| **Facts** | No (row-level) | `l_extendedprice * (1 - l_discount)`, `CONCAT(l_orderkey, '-', l_linenumber)` |
| **Metrics** | Yes (aggregated) | `SUM(l_extendedprice)`, `AVG(o_totalprice)`, `COUNT(o_orderkey)` |
| **Derived Metrics** | Yes (from other metrics) | `orders.total_revenue / orders.total_cost` |

### Validation Rules Snowflake Enforces

**Source:** [Validation Rules](https://docs.snowflake.com/en/user-guide/views-semantic/validation-rules)

#### General Rules
1. Must define at least one dimension or metric
2. Primary/foreign keys must use physical base table columns (or direct column references)
3. All table references must use defined aliases
4. Every semantic expression must be associated with a table
5. No circular references between expressions
6. No circular references between logical tables
7. Table functions are NOT allowed
8. Scalar functions (YEAR, MONTH, QUARTER, DAY, WEEK, LEFT, CONCAT, etc.) are allowed

#### Cross-Table Reference Rules
- Expressions CANNOT reference base table columns from other tables directly
- Must establish a relationship first, then reference via the relationship
- Process: define relationship -> create fact on source -> reference from connected table

#### Row-Level Expression Rules (Dimensions/Facts)
- Can directly reference columns from own table
- Can reference other row-level expressions at **equal or lower granularity** (many-to-one direction)
- Must use **aggregation** when referencing **higher granularity** expressions
  - Example: `customer.total_orders` must use `COUNT(orders.o_orderkey)`
- Cannot reference metrics at same granularity
- Can reference metrics at lower granularity tables

#### Metric Rules
- Non-derived metrics **must** use an aggregate function
- Metrics at equal/lower granularity use **single aggregate**: `SUM(line_items.discounted_price)`
- Metrics at higher granularity require **nested aggregation**: `AVG(SUM(orders.o_totalprice))`
- Metric-to-metric references at equal/lower granularity: direct (no aggregation needed)
  - Example: `orders.profit_margin = orders.total_revenue / orders.total_cost`
- Metric-to-metric at higher granularity: must aggregate

#### Window Function Metric Rules
- Cannot be used by row-level calculations
- Cannot be used in definitions of other metrics
- Syntax:
```sql
PRIVATE orders.running_total AS
  SUM(total_revenue) OVER (
    ORDER BY order_date ASC
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
  )
```

#### Relationship Rules
- No circular relationships (even through transitive paths)
- No self-references
- Snowflake automatically derives transitive relationships
- Multi-path joins: create separate logical tables for different join paths
- One-to-one chains remain one-to-one
- One-to-one + many-to-one = transitive many-to-one

#### Name Resolution
- If semantic expression and column have same name, references resolve to the expression
- Exception: self-referential definitions resolve to the base column

### Can Metrics Reference Other Metrics?

**YES** - at equal or lower granularity, metrics can directly reference other metrics without additional aggregation. At higher granularity, aggregation is required.

### Can Facts Reference Other Facts?

**YES** - facts can reference other facts at equal or lower granularity. Facts at higher granularity require aggregation (which would make them metrics).

---

## 5. Validation Before Deployment

### SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML with verify_only=TRUE

```sql
CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML(
  'my_database.my_schema',
  $$
  name: my_semantic_view
  tables:
    - name: orders
      base_table:
        database: my_database
        schema: my_schema
        table: ORDERS
      dimensions:
        - name: order_status
          expr: O_ORDERSTATUS
          data_type: VARCHAR
      metrics:
        - name: order_count
          expr: COUNT(O_ORDERKEY)
  $$,
  TRUE    -- verify_only: validates without creating
);
```

**Success returns:** `"YAML file is valid for creating a semantic view. No object has been created yet."`

**On failure:** Throws an exception with detailed error message (doesn't return error as string).

**What it validates:**
- YAML syntax correctness
- Table existence in Snowflake
- Column existence in referenced tables
- Expression syntax validity
- Relationship consistency
- All validation rules from Section 4

**Source:** [SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML](https://docs.snowflake.com/en/sql-reference/stored-procedures/system_create_semantic_view_from_yaml)

### Other Validation Methods

1. **SQL DDL with dry run:** Not directly supported, but you can create in a dev schema and test
2. **Cortex Analyst REST API inline:** Can test with semantic model inline (not requiring a persisted object)
3. **DESCRIBE SEMANTIC VIEW:** After creation, inspect the structure
4. **SHOW SEMANTIC DIMENSIONS/FACTS/METRICS:** List all elements for inspection
5. **GET_DDL:** Retrieve creation statement for review
6. **SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW:** Export back to YAML for review

### Export for Version Control

```sql
SELECT SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW('my_database.my_schema.my_semantic_view');
```

---

## 6. Expression Failure Prevention Strategies

### Snowflake's Semantic Model Generator (Deprecated)

The open-source [semantic-model-generator](https://github.com/Snowflake-Labs/semantic-model-generator) (Apache 2.0, 201 stars) was a Streamlit app that:
1. Analyzed database tables via `DESCRIBE TABLE`
2. Examined first 5 rows as sample data
3. Used Cortex LLM to classify columns as Facts, Dimensions, Metrics
4. Auto-generated descriptions and synonyms
5. Produced YAML output

**Status:** "The Semantic Model Generator in Streamlit has been replaced by the native Cortex Analyst Semantic View Generator in Snowsight." No longer the recommended approach.

**Source:** [GitHub - semantic-model-generator](https://github.com/Snowflake-Labs/semantic-model-generator)

### AI Auto-Generation Approach (Community)

A stored procedure approach using `SNOWFLAKE.CORTEX.COMPLETE()` with Claude Sonnet:
1. Retrieve column info via `DESCRIBE TABLE`
2. Inspect first 5 rows as JSON
3. Send to LLM with strict rules: "Use ONLY column names from the above list"
4. LLM generates SQL DDL for `CREATE SEMANTIC VIEW`
5. Execute generated SQL

**Key constraint enforcement:** Column name whitelist prevents referencing non-existent columns.

**Source:** [Dev.to - Auto-Generate Semantic Views](https://dev.to/tsubasa_tech/auto-generate-snowflake-semantic-views-with-ai-a-developers-fast-track-to-cortex-analyst-44bp)

### Best Practices for Programmatic Generation

1. **Use column whitelists** - Extract exact column names from `INFORMATION_SCHEMA.COLUMNS` or `DESCRIBE TABLE`, and constrain the LLM to only use those names
2. **Template expressions, don't free-form generate them:**
   - Dimensions: Direct column reference `col_name` or simple transforms `YEAR(date_col)`, `LEFT(col, N)`
   - Facts: Direct column reference or simple arithmetic between columns of same table
   - Metrics: Standard aggregations `SUM(fact)`, `AVG(fact)`, `COUNT(dimension)`, `COUNT(DISTINCT dim)`
3. **Validate immediately** - Call `SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML(..., TRUE)` after generation
4. **Start small** - 3-5 tables, 50-100 columns max
5. **Use verified queries** - Add Q&A examples to improve Cortex Analyst accuracy
6. **Export and version control** - Store YAML in Git, deploy via CI/CD pipeline
7. **Schema cloning for promotion** - Clone schemas across dev/test/prod environments

### Template-Based Expression Patterns

| Element | Safe Expression Pattern | Example |
|---------|------------------------|---------|
| Dimension (direct) | `column_name` | `C_NAME` |
| Dimension (transform) | `FUNC(column_name)` | `YEAR(O_ORDERDATE)` |
| Dimension (concat) | `CONCAT(col1, '-', col2)` | `CONCAT(L_ORDERKEY, '-', L_LINENUMBER)` |
| Dimension (substring) | `LEFT(column, N)` | `LEFT(C_PHONE, 2)` |
| Time dimension | `column_name` | `O_ORDERDATE` |
| Fact (direct) | `column_name` | `L_EXTENDEDPRICE` |
| Fact (calculated) | `col1 * col2` or `col1 * (1 - col2)` | `L_QUANTITY * L_EXTENDEDPRICE` |
| Fact (aggregated = helper) | `COUNT(other_table.col)` | `COUNT(orders.O_ORDERKEY)` |
| Metric (sum) | `SUM(fact_name)` | `SUM(revenue)` |
| Metric (avg) | `AVG(column)` | `AVG(O_TOTALPRICE)` |
| Metric (count) | `COUNT(column)` | `COUNT(O_ORDERKEY)` |
| Metric (count distinct) | `COUNT(DISTINCT col)` | `COUNT(DISTINCT C_CUSTKEY)` |
| Metric (nested) | `AVG(SUM(col))` | `AVG(SUM(O_TOTALPRICE))` |
| Derived metric | `table1.metric / table2.metric` | `orders.total_revenue / returns.total_returns` |
| Filter | `column = 'value'` | `O_ORDERSTATUS = 'F'` |

---

## 7. What's New in Late 2025 / Early 2026

### Timeline of Key Releases

| Date | Feature | Status |
|------|---------|--------|
| Jan 15, 2025 | Custom instructions in Cortex Analyst | Preview |
| Mar 26, 2025 | Multiple semantic models in single query | GA |
| Apr 17, 2025 | Semantic views | Preview |
| Jun 02-05, 2025 | Semantic views (creation) | GA (Summit) |
| Jun 02-05, 2025 | Semantic views (querying) | Preview (Summit) |
| Jul 29, 2025 | Cortex Agents for Microsoft Teams & Copilot | Preview |
| Aug 26, 2025 | Snowsight semantic view editor | Preview |
| Sep 02, 2025 | Cortex Agents Admin REST API | Preview |
| Sep 30, 2025 | Derived metrics in semantic views | GA |
| Oct 02, 2025 | Snowsight semantic view editor | GA |
| Nov 04, 2025 | **Cortex Agents** | **GA** |
| Nov 04, 2025 | **Snowflake Intelligence** | **GA** |
| Dec 02, 2025 | Optimize semantic views with verified queries | Preview |

### Key New Features

**Multiple Semantic Models:** A single Cortex Analyst query can reference multiple semantic views/models. Cortex Analyst automatically selects the most appropriate one. Response includes `semantic_model_selection` field indicating which was chosen.

**Source:** [Multiple Models GA](https://docs.snowflake.com/en/release-notes/2025/other/2025-03-26-multiple-models-cortex-analyst)

**Derived Metrics:** View-level metrics that combine data from multiple logical tables. Not scoped to a single table. Enabled September 30, 2025.

**Source:** [Derived Metrics Release](https://docs.snowflake.com/en/release-notes/2025/other/2025-09-30-semantic-view-derived-metrics)

**Optimization with Verified Queries (Dec 2025, Preview):** Analyzes verified queries to extract patterns and enhance the semantic model, enabling Cortex Analyst to answer a broader range of questions beyond just those matching existing verified queries.

**Source:** [Verified Query Optimization](https://docs.snowflake.com/en/release-notes/2025/other/2025-12-02-cortex-analyst-optimization)

**Data to Chart Tool:** Cortex Agents can now generate Vega-Lite visualizations from query results.

**Source:** [Configure Agents](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-manage)

**MCP Server:** Snowflake-managed Model Context Protocol server for external AI agent integration.

**Source:** [MCP Server](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-mcp)

**Microsoft Teams Integration:** Cortex Agents can be deployed to MS Teams and M365 Copilot (Preview since Jul 2025).

**Source:** [Teams Integration](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-teams-integration)

### Semantic Views vs Legacy Semantic Models

| Feature | Legacy YAML Model | Semantic View |
|---------|-------------------|---------------|
| Storage | Stage file (YAML on @stage) | Native Snowflake object (schema-level) |
| RBAC | Manual | Full Snowflake privilege system |
| Sharing | Not supported | Supported (data sharing, marketplace) |
| Measures/Facts | "measures" with `default_aggregation` | "facts" (row-level) + "metrics" (aggregated) |
| Derived Metrics | Not supported | Supported (Sep 2025) |
| Access Modifiers | Not supported | `public_access` / `private_access` |
| Custom Instructions | In YAML (`custom_instructions` / `module_custom_instructions`) | Via SQL DDL clauses (`AI_SQL_GENERATION`, `AI_QUESTION_CATEGORIZATION`) |
| Join types | Explicit (`join_type`, `relationship_type`) | **Auto-inferred** (removed from spec) |
| Querying | Via Cortex Analyst API only | Direct SQL + SEMANTIC_VIEW clause + Cortex Analyst |
| Version control | Native (YAML file in Git) | Export via `SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW` |
| Cloning | Not supported | Schema-level cloning |
| Replication | Not supported | Not supported |

**IMPORTANT:** Semantic views are the recommended path forward. Legacy YAML models on stages are still supported but semantic views are the native, governed approach.

---

## Complete End-to-End Example

### Step 1: Create Semantic View

```sql
CREATE OR REPLACE SEMANTIC VIEW my_db.my_schema.sales_analysis
  TABLES (
    customers AS my_db.gold.CUSTOMERS PRIMARY KEY (CUSTOMER_ID)
      WITH SYNONYMS = ('clients', 'buyers')
      COMMENT = 'Customer dimension table',
    orders AS my_db.gold.ORDERS PRIMARY KEY (ORDER_ID)
      WITH SYNONYMS = ('purchases', 'transactions')
      COMMENT = 'Sales order fact table',
    products AS my_db.gold.PRODUCTS PRIMARY KEY (PRODUCT_ID)
      WITH SYNONYMS = ('items', 'skus')
      COMMENT = 'Product catalog'
  )

  RELATIONSHIPS (
    orders (CUSTOMER_ID) REFERENCES customers,
    orders (PRODUCT_ID) REFERENCES products
  )

  FACTS (
    orders.order_amount AS ORDER_AMOUNT,
    orders.quantity AS QUANTITY,
    orders.discount_amount AS DISCOUNT_AMOUNT,
    PRIVATE orders.net_amount AS ORDER_AMOUNT - DISCOUNT_AMOUNT
  )

  DIMENSIONS (
    customers.customer_name AS CUSTOMER_NAME,
    customers.region AS REGION,
    customers.segment AS CUSTOMER_SEGMENT,
    products.product_name AS PRODUCT_NAME,
    products.category AS PRODUCT_CATEGORY,
    orders.order_date AS ORDER_DATE,
    orders.order_status AS ORDER_STATUS
  )

  METRICS (
    orders.total_revenue AS SUM(orders.net_amount),
    orders.order_count AS COUNT(ORDER_ID),
    orders.average_order_value AS AVG(orders.net_amount),
    customers.customer_count AS COUNT(CUSTOMER_ID),
    products.products_sold AS COUNT(DISTINCT orders.PRODUCT_ID)
  )

  AI_SQL_GENERATION 'Always round currency values to 2 decimal places. When asked about revenue, use net_amount (after discounts).'
  AI_QUESTION_CATEGORIZATION 'If the user asks about employee data or internal HR, reject the question and ask them to contact HR.'
;
```

### Step 2: Validate (if using YAML path)

```sql
CALL SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML(
  'my_db.my_schema',
  $$
  name: sales_analysis
  tables:
    - name: customers
      base_table:
        database: my_db
        schema: gold
        table: CUSTOMERS
      dimensions:
        - name: customer_name
          expr: CUSTOMER_NAME
          data_type: VARCHAR
          description: Full customer name
          synonyms:
            - client name
            - buyer name
      ...
  $$,
  TRUE    -- Validate only, don't create
);
```

### Step 3: Create Cortex Agent

```sql
CREATE OR REPLACE AGENT my_db.my_schema.sales_agent
  COMMENT = 'Sales intelligence agent'
  PROFILE = '{"display_name": "Sales Assistant", "color": "blue"}'
  FROM SPECIFICATION
  $$
  models:
    orchestration: auto

  orchestration:
    budget:
      seconds: 30
      tokens: 16000

  instructions:
    response: "Respond professionally. Format currency with $ and 2 decimal places."
    orchestration: "Use the sales analyst tool for all revenue and order questions."
    system: "You are a sales intelligence assistant that helps analyze sales performance."
    sample_questions:
      - question: "What was total revenue last quarter?"

  tools:
    - tool_spec:
        type: "cortex_analyst_text_to_sql"
        name: "SalesAnalyst"
        description: "Analyzes sales data including revenue, orders, customers, and products"

  tool_resources:
    SalesAnalyst:
      semantic_view: "my_db.my_schema.sales_analysis"
  $$;
```

### Step 4: Grant Access

```sql
GRANT USAGE ON AGENT my_db.my_schema.sales_agent TO ROLE analyst_role;
GRANT SELECT ON SEMANTIC VIEW my_db.my_schema.sales_analysis TO ROLE analyst_role;
GRANT SELECT ON ALL TABLES IN SCHEMA my_db.gold TO ROLE analyst_role;
```

### Step 5: Interact via REST API

```
POST /api/v2/databases/my_db/schemas/my_schema/agents/sales_agent:run
Content-Type: application/json
Authorization: Bearer <token>

{
  "messages": [
    {"role": "user", "content": "What was our total revenue by region last month?"}
  ]
}
```

---

## Key Implications for ekaiX

1. **Generation Agent should output SQL DDL** (not just YAML) since `CREATE SEMANTIC VIEW` is the native format and supports custom instructions directly
2. **YAML path is also viable** via `SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML` -- good for validation-first workflow
3. **Always validate before deploying** using `verify_only=TRUE`
4. **Facts are row-level, Metrics are aggregated** -- this is different from legacy "measures" which had `default_aggregation`
5. **Join types are auto-inferred** -- do NOT specify `join_type` or `relationship_type` in semantic views
6. **Keep models under 50-100 columns** for optimal Cortex Analyst performance
7. **Publishing Agent needs to CREATE AGENT** with the semantic view as a tool_resource
8. **Multiple semantic views** can be attached to a single agent
9. **Verified queries** dramatically improve accuracy -- include them
10. **Custom instructions** for SQL generation and question categorization are powerful guardrails
