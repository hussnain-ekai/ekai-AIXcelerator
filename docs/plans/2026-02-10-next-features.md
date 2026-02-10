# ekaiX Next Features — Feb 10, 2026

## 1. Synonyms & Fuzzy Matching for WHERE Clause Strings

**Problem:** Users ask "sales in NYC" but the column has "New York". LLM guesses but doesn't know actual values.

**Solution (two layers):**
- **sample_values** on dimensions in semantic view YAML — populate from discovery profiling (we already have distinct value distributions). Cortex Analyst uses these to map user input to actual column values automatically.
- **synonyms** on dimensions — map common abbreviations ("NYC" = "New York", "CA" = "California"). Can be auto-suggested by LLM during requirements phase based on sample_values.
- **Cortex Search as fuzzy lookup (optional)** — index dimension values in a Cortex Search Service. When Cortex Analyst can't match a filter value, the agent's Search tool does fuzzy retrieval to find the closest match. Useful for large cardinality columns (e.g., 50K product names).

**Implementation path:** Discovery already profiles top distinct values per column. Flow them into generation agent's JSON output → assembler writes `sample_values` and `synonyms` fields into YAML.

---

## 2. Cortex Agent MCP Integration (Post-Publish Testing)

**Problem:** After publishing, users must leave ekaiX and go to Snowflake Intelligence to test their agent. No in-app validation.

**Solution:**
- Connect to Snowflake's managed MCP server as an MCP client from ekaiX.
- After publishing, expose a "Test Your Agent" panel in the data product UI.
- User asks questions directly in ekaiX → proxied to published Cortex Agent via MCP `tools/call` → response displayed inline.
- Uses existing Snowflake OAuth connection (no extra credentials needed).
- Future-proof: MCP is becoming the standard protocol for agent-tool communication.

**Ref:** https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-mcp

---

## 3. Semantic Model Quality Validation (User-Assisted)

**Problem:** Published semantic view may have broken joins, wrong aggregations, or missing filters that produce incorrect numbers. No way to catch this before users rely on it.

**Solution (conversation-driven validation):**
- During validation phase, ask user: "Can you give us 2-3 business questions with expected answers so we can verify the model?"
- Run each question against the **Cortex Agent** (NOT raw SQL — agent handles joins/filters correctly on large data, avoids expensive full-table scans).
- Compare agent's answer to user's expected answer.
- If mismatch: show discrepancy conversationally — "You expected $2.3M but the model returns $4.6M for Q4 sales. This might indicate a duplicate join on maintenance_events."
- Let validation agent diagnose the issue (check join cardinality, filter logic, aggregation scope).
- Fix YAML and re-validate in a loop until all test questions match.
- Store validated Q&A pairs as `verified_queries` in the semantic view YAML — they become permanent guardrails.

**Key design constraint:** Never run open-ended queries on potentially large datasets. Always route through Cortex Agent which respects the semantic model's scope.

---

## 4. ML Functions as Custom Tools on Cortex Agent

**Problem:** Business users want predictions and anomaly alerts but can't write ML code.

**Solution:**
- During requirements phase, ask: "Do you need forecasting or anomaly detection on any of your metrics?"
- If yes, auto-generate Snowflake ML model creation SQL:
  - `SNOWFLAKE.ML.FORECAST` for time-series predictions
  - `SNOWFLAKE.ML.ANOMALY_DETECTION` for outlier detection
  - `SNOWFLAKE.ML.TOP_INSIGHTS` for root cause analysis
- Wrap each as a **stored procedure** and attach as a **custom tool** on the Cortex Agent.
- Result: users can ask "What will sales be next quarter?" or "Are there any anomalous transactions this month?" and the agent calls the ML function.

**Ref:** https://docs.snowflake.com/en/guides-overview-ml-functions

---

## 5. Data Maturity Classification & Transformation Agent

**Problem:** ekaiX assumes gold-layer data. When users connect bronze/silver data (untyped columns, duplicates, nested JSON, no PKs), the generated semantic model produces wrong answers.

**Solution:** Auto-classify source data maturity (bronze/silver/gold) during discovery, then deploy a new Transformation Agent that creates Snowflake Dynamic Tables to clean the data before semantic modeling.

**Full specification:** [`2026-02-11-data-maturity-transformation-agent.md`](./2026-02-11-data-maturity-transformation-agent.md)

---

## 6. Multi-Platform Support (Databricks, Microsoft Fabric)

**Problem:** ekaiX is currently a Snowflake Native App. Customers on Databricks or Microsoft Fabric can't use it.

**Solution:** The AI agents, frontend, and backend are platform-agnostic (~60-70% of code). The Snowflake-specific parts (discovery SQL, YAML semantic view spec, Cortex Agent DDL, SPCS packaging) need platform-specific backends. Architecture: `TransformationBackend` protocol abstraction with implementations for each platform.

**Platform mapping:**
- **Databricks:** Unity Catalog for discovery, Delta Live Tables for transforms, custom semantic layer
- **Microsoft Fabric:** OneLake for discovery, Dataflow Gen2 for transforms, Power BI semantic model

**See also:** Section 11 of [`2026-02-11-data-maturity-transformation-agent.md`](./2026-02-11-data-maturity-transformation-agent.md)

---

## Priority Order

| # | Feature | Effort | Impact |
|---|---------|--------|--------|
| 1 | sample_values + synonyms | Low | High — fixes a real accuracy problem |
| 3 | Quality validation with user Q&A | Medium | High — trust and correctness |
| 2 | MCP integration for testing | Medium | High — keeps users in ekaiX |
| 5 | Data maturity + transformation agent | High | Very High — unlocks bronze/silver data |
| 4 | ML Functions as tools | High | Medium — wow factor for demos |
| 6 | Multi-platform (Databricks, Fabric) | Very High | Very High — market expansion |
