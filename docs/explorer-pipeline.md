# Explorer Pipeline — Intended Behaviour, Known Issues, and Fixes

Last updated: 2026-02-26

## Overview

The explorer is the final phase of the ekaiX pipeline. After a data product is published (Cortex Agent + Semantic View deployed to Snowflake), the explorer answers user questions by querying structured data, searching uploaded documents, or combining both.

The explorer pipeline has four layers:
1. **Intent classifier** — deterministic token matching to classify the question
2. **Route planner** — assigns execution lanes based on intent + product type
3. **Orchestrator delegation** — passes the FQN and route plan to the explorer-agent
4. **Explorer-agent** — LLM with 10 tools that executes the lanes and synthesises an answer

---

## 1. Intent Classification

**File:** `ai-service/routers/agent.py` — `_classify_query_intent()`

Classifies every user question into one of five intents using substring token matching on the lowercased message.

### Signal token sets

| Signal | Tokens |
|--------|--------|
| `has_metric` | kpi, metric, measure, trend, growth, decline, count, average, sum, total, compare, comparison, "top ", "bottom ", how many, how much, percentage, "rate ", frequency, "number of ", most common, least common, highest, lowest, ranking |
| `has_transaction` | invoice, purchase order, "po ", "order id", transaction, receipt, "part ", spare, serial, sku, line item, how much, exact amount, exact value, exact number |
| `has_policy` | policy, manual, guideline, procedure, requirement, compliance, contract, overview report, outlook report, document says, report says, recommend, recommendation, advisory, bulletin, regulation, standard, guidance, best practice, safety management, corrective action |
| `has_document_signal` | document, pdf, file, report, notes, memo, investigation, finding, assessment, analysis report, ntsb, faa |

### Decision tree (first match wins)

```
1. has_metric AND (has_transaction OR has_policy OR has_document_signal) → "hybrid"
2. has_transaction → "transaction_lookup"
3. has_policy OR has_document_signal → "policy"
4. has_metric → "metric"
5. none matched → "unknown"
```

### Intended behaviour

A question like "How many fatal accidents in 2023, and what does the NTSB recommend?" should classify as **hybrid** because:
- "how many" triggers `has_metric`
- "recommend" triggers `has_policy`
- "ntsb" triggers `has_document_signal`

A pure metric question like "How many accidents in 2023?" classifies as **metric**.

---

## 2. Route Planning

**File:** `ai-service/routers/agent.py` — `_build_query_route_plan()`

Takes the intent class + product metadata and assigns execution **lanes** (ordered list of tools/strategies the explorer should use).

### Lane assignment by intent

| Intent | Lanes (published, has docs) | Lanes (published, no docs) | Lanes (unpublished) |
|--------|-----------------------------|---------------------------|---------------------|
| `hybrid` | document_facts, document_chunks, structured_agent | document_facts, document_chunks, structured_agent | document_facts, document_chunks, structured_sql |
| `metric` | structured_agent, document_chunks | structured_agent | structured_sql |
| `policy` | document_chunks | document_chunks | document_chunks |
| `transaction_lookup` | document_facts, structured_agent, structured_sql | document_facts, structured_agent, structured_sql | document_facts, structured_sql |
| `unknown` | structured_sql, document_chunks | structured_sql, document_chunks | structured_sql, document_chunks |

### Intended behaviour

- For products with uploaded documents, even a pure **metric** question includes `document_chunks` as a secondary lane. This ensures the explorer searches documents for context that the structured model may not cover (e.g., cause factors, recommendations, regulatory guidance).
- **Hybrid** questions always activate all three evidence sources: document facts, document chunks, and the published agent.
- The route plan is serialised into the supervisor contract as JSON so the explorer-agent can read which lanes to activate.

---

## 3. Orchestrator Delegation (Rule 24)

**File:** `ai-service/agents/prompts.py` — Rule 24

When the orchestrator receives a data question after publishing, it delegates to the explorer-agent with a task description that includes:
- The `published_agent_fqn` (e.g., `EKAIX.NTSB_INCIDENT_ANALYSIS_MARTS.ntsb_incident_analysis_agent`)
- The `query_route_plan` lanes (e.g., `["structured_agent", "document_chunks"]`)
- Instructions to fall back to direct SQL if `query_cortex_agent` returns a non-answer
- Instructions to call `search_document_chunks` if the lanes include `document_chunks`

### Intended behaviour

The orchestrator's task description should be self-contained: the explorer-agent should never need to guess the agent FQN or schema. When the FQN is provided, the explorer skips discovery (SHOW AGENTS) and goes straight to `query_cortex_agent`.

---

## 4. Explorer-Agent Execution

**File:** `ai-service/agents/prompts.py` — `EXPLORER_PROMPT`

The explorer-agent is an LLM (same model as orchestrator) with 10 tools:

| Tool | Purpose |
|------|---------|
| `query_cortex_agent` | Ask a question to the published Snowflake Cortex Agent |
| `execute_rcr_query` | Run read-only SQL against Snowflake (RCR mode) |
| `query_erd_graph` | Discover table/column names from the ERD graph |
| `search_document_chunks` | Semantic search over uploaded document chunks (PostgreSQL) |
| `query_document_facts` | Deterministic document-fact retrieval for exact values |
| `query_document_graph` | Query the document knowledge graph (Neo4j) |
| `find_facts_for_entity` | Find document facts for a specific entity |
| `profile_table` | Statistical profiling of a Snowflake table |
| `get_latest_semantic_view` | Load the current semantic model YAML |
| `get_latest_brd` | Load the current business requirements document |

### Intended execution flow

For a hybrid question on a published product with documents:

```
1. Read query_route_plan lanes from task description
2. Lane "structured_agent":
   a. Call query_cortex_agent(agent_fqn, question)
   b. If answer is substantive → use it
   c. If answer is a non-answer ("I do not have access to specific data") → fall back:
      i.  Call query_erd_graph to discover table/column names
      ii. Write a read-only SQL query
      iii. Call execute_rcr_query
3. Lane "document_chunks":
   a. Call search_document_chunks(data_product_id, query_text)
   b. Extract relevant citations
4. Synthesise: combine structured evidence + document evidence into a single answer
5. Include citations: [filename, page N, section] for document sources
```

### Cortex Agent discovery fallback

If the task description does NOT include a FQN, the explorer must discover the agent:
1. Published agents live in the **EKAIX** database, NOT the source database
2. The schema name is `EKAIX.{PRODUCT_NAME}_MARTS` (uppercased, spaces → underscores)
3. Run: `SHOW AGENTS IN SCHEMA EKAIX.<PRODUCT_NAME>_MARTS`
4. Use the discovered FQN with `query_cortex_agent`

---

## 5. Cortex Agent (Snowflake-side)

**File:** `ai-service/tools/snowflake_tools.py` — `create_cortex_agent()`

The published Cortex Agent is a Snowflake-managed AI agent with up to two tools:

| Tool | Type | When included |
|------|------|---------------|
| `Analyst` | `cortex_analyst_text_to_sql` | Always (when semantic view exists) |
| `DocumentSearch` | `cortex_search` | When Cortex Search Service exists in `_DOCS` schema |

The agent is deployed via `CREATE OR REPLACE AGENT` with a YAML spec. It has:
- Budget: 120 seconds, 10,000 tokens
- Model: claude-3-5-sonnet (default)
- Semantic view reference pointing to the published semantic model

### Intended behaviour

When queried via `SNOWFLAKE.CORTEX.AGENT_RUN()`, the Cortex Agent should:
1. Parse the question and route to the appropriate internal tool (Analyst or DocumentSearch)
2. The Analyst tool translates the question into SQL using the semantic view dimensions/metrics
3. Return the answer with evidence

### Non-answer detection

**File:** `ai-service/tools/snowflake_tools.py` — `query_cortex_agent()`

If the Cortex Agent returns LLM fallback knowledge instead of querying data, the response is flagged:

Detected phrases: "i do not have access", "i don't have access", "i cannot access", "no specific data", "not available in", "unable to retrieve", "i don't have specific", "i do not have specific", "cannot provide specific"

When detected, the tool returns `is_non_answer: true` with a `fallback_hint` instructing the explorer to use direct SQL.

---

## 6. Trust Contract

**File:** `ai-service/routers/agent.py` — tool contract hints

Every tool call produces a trust contract hint that the frontend uses to display evidence state:

| Tool | source_mode | confidence_decision | trust_state |
|------|-------------|--------------------|----|
| `execute_rcr_query` (success) | structured | medium | answer_ready |
| `query_cortex_agent` (success) | structured | medium | answer_ready |
| `query_cortex_agent` (non-answer) | structured | low | insufficient_evidence |
| `query_cortex_agent` (auth error) | structured | abstain | blocked_access |
| `search_document_chunks` (success) | document | medium | answer_ready |
| `query_document_facts` (success) | document | medium | answer_ready |

When multiple hints exist with different `source_mode` values (e.g., "structured" + "document"), the final trust state is promoted to `source_mode: "hybrid"`.

---

## 7. Post-Publish Auto-Repair

**File:** `ai-service/routers/agent.py` — `_post_publish_auto_repair()`

Deterministic safety net that runs after the publishing LLM stream ends. Ensures the pipeline is complete regardless of what the LLM did or didn't do.

### Steps:
1. **Cortex Search Service** — checks `EKAIX.{DP}_DOCS` for an existing service. If absent and DOC_CHUNKS has rows, creates one with `TARGET_LAG = '1 hour'` and a 300-second timeout.
2. **Cortex Agent** — checks `EKAIX.{DP}_MARTS` for an existing agent. If absent, discovers the semantic view and creates the agent with both Analyst + DocumentSearch tools.
3. **Persist** — writes `published_at` and `published_agent_fqn` to PostgreSQL.
4. **Conditional cleanup** — deletes PG `doc_chunks` rows ONLY if `has_search_service` is `True`. If Cortex Search creation failed, PG chunks are preserved as the fallback document store.

---

## 8. Known Issues

### ISSUE-1: Cortex Agent returns LLM knowledge instead of querying data

**Status:** Partially mitigated (fallback added), root cause unresolved

The Cortex Agent's Analyst tool can only answer questions about dimensions and metrics declared in the semantic view YAML. If the semantic view lacks a year `time_dimension` or cause-factor `dimension`, the Analyst cannot filter/group by those fields and falls back to the LLM's parametric knowledge.

**Root cause:** The semantic view YAML is generated by the model-builder agent based on the BRD. If the BRD doesn't explicitly name "year" as a time dimension or "probable cause" as a dimension, they won't appear in the YAML. The YAML validation (`validate_semantic_view_yaml`) only checks syntax, not whether the model covers the business questions.

**Mitigation:** The explorer now detects non-answers and falls back to direct SQL via `execute_rcr_query`. This bypasses the semantic model entirely and queries the raw source tables.

**Proper fix needed:** The model-builder should auto-detect date columns and add them as `time_dimensions`. The YAML validator should cross-check that the model covers the BRD's analytical questions.

### ISSUE-2: Cortex Search Service creation fails (401 Unauthorized / timeout)

**Status:** Unresolved

`CREATE CORTEX SEARCH SERVICE` DDL intermittently fails with:
- `290401 (08001): 401 Unauthorized` — Snowflake auth issue, not from our code
- `000604 (57014): SQL execution was cancelled by the client due to a timeout` — DDL takes 2+ minutes

**Impact:** When the search service doesn't exist, the Cortex Agent has no `DocumentSearch` tool. Document questions fall back to `search_document_chunks` (PostgreSQL). PG chunks are now preserved when Cortex Search creation fails (see ISSUE-3 fix), so this is degraded but not broken.

**Mitigation:** The auto-repair uses `STATEMENT_TIMEOUT_IN_SECONDS=300` and the Snowflake connection has `network_timeout=180s`. Still fails sometimes.

### ISSUE-3: PG doc_chunks deleted after publish

**Status:** Fixed (2026-02-26)

The auto-repair unconditionally deleted PostgreSQL `doc_chunks` after publishing. If the Cortex Search Service failed to create (ISSUE-2), there was no document store at all.

**Fix:** Step 4 now checks `has_search_service` before deleting. If Cortex Search creation failed, PG chunks are preserved so `search_document_chunks` still works.

### ISSUE-4: Explorer LLM compliance is probabilistic

**Status:** Inherent limitation

The explorer-agent is an LLM. Even with clear instructions (use ALL lanes, fall back to SQL on non-answer), the LLM may:
- Ignore the route plan lanes
- Not follow the fallback instructions
- Produce a vague answer instead of calling tools
- Lose the FQN from the task description in long contexts

**Mitigation:** The instructions are as explicit as possible. The route plan is passed both in the supervisor contract and the task description. The CORTEX AGENT RULE prioritises FQN from the task description over discovery.

### ISSUE-5: source_mode always "structured" for Cortex Agent answers

**Status:** Partially fixed

The Cortex Agent may internally use its `DocumentSearch` tool, but the router always tags the result as `source_mode: "structured"`. This means the frontend trust state shows "structured" even for hybrid answers.

**Mitigation:** When the explorer calls BOTH `query_cortex_agent` AND `search_document_chunks`, the trust contract merger promotes to `source_mode: "hybrid"` because both tool hints are present. The remaining gap is when the Cortex Agent uses DocumentSearch internally without the explorer calling `search_document_chunks` separately.

### ISSUE-6: Auto-repair gated on LLM tool success

**Status:** Fixed (2026-02-26)

Auto-repair only ran when `_publish_completed=True`, which was set only after a successful `create_cortex_agent` tool call. If the LLM skipped the tool entirely, auto-repair never fired — no agent created, no FQN persisted, no phase transition to explorer.

**Fix:** Removed `_publish_completed` gate. Auto-repair now runs unconditionally when `_current_phase == "publishing"` and no `_failure_plan_message`. The function already checks agent/service existence internally before creating, so it's safe to run even when the LLM already created everything.

---

## 9. Fix History

### 2026-02-26: Routing + Fallback Fixes

**Files changed:** `ai-service/routers/agent.py`, `ai-service/agents/prompts.py`, `ai-service/tools/snowflake_tools.py`

| Fix | What changed | Bug addressed |
|-----|-------------|--------------|
| Expanded `has_metric` tokens | Added: how many, how much, percentage, rate, frequency, number of, most common, least common, highest, lowest, ranking | Metric questions weren't classified correctly |
| Expanded `has_policy` tokens | Added: recommend, recommendation, advisory, bulletin, regulation, standard, guidance, best practice, safety management, corrective action | Compound questions with policy signals weren't triggering hybrid |
| Expanded `has_document_signal` tokens | Added: investigation, finding, assessment, analysis report, ntsb, faa | Domain-specific document signals weren't recognised |
| Added `has_documents` to route planner | `metric` intent now adds `document_chunks` lane when product has documents | Metric questions on hybrid products never searched documents |
| Fixed CORTEX AGENT RULE | Changed SHOW AGENTS fallback from source database to `EKAIX.{DP}_MARTS` | Explorer searched wrong schema for agents |
| Added non-answer detection | `query_cortex_agent` flags responses with "I do not have access" phrases | Explorer accepted LLM fallback as real answers |
| Added SQL fallback instructions | Explorer prompt rule 5: fall back to `execute_rcr_query` on non-answers | No recovery path when Cortex Agent couldn't answer |
| Updated Rule 24 | Orchestrator now passes route plan lanes + fallback instructions to explorer | Explorer didn't know which lanes to activate |
| Trust contract for non-answers | Non-answers get `confidence_decision: "low"`, `trust_state: "insufficient_evidence"` | Non-answers showed as confident answers in UI |
| Guarded PG chunk deletion | Step 4 only deletes when `has_search_service=True` | PG chunks wiped even when Cortex Search failed (ISSUE-3) |
| Removed `_publish_completed` gate | Auto-repair runs unconditionally in publishing phase | Auto-repair never fired when LLM skipped tools (ISSUE-6) |
