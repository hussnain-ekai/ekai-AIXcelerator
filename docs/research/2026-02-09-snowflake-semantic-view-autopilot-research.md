# Snowflake Semantic View Autopilot — Research for ekaiX Integration

**Date:** 2026-02-09
**Researcher:** Claude (Opus 4.6)
**Status:** Complete
**Sources:** Snowflake official docs, press releases, demo notebooks, analyst reports, dev community articles (all fetched live Feb 9, 2026)

---

## 1. What Is Semantic View Autopilot?

An AI-powered service within Snowsight that automates the creation and ongoing governance of Snowflake Semantic Views. Announced GA on **February 3, 2026**.

Semantic Views are schema-level objects in Snowflake (not YAML files on a stage — that's the legacy "semantic model" format). They integrate natively with Snowflake's privilege system, sharing mechanisms, and metadata catalog. Snowflake now recommends Semantic Views over legacy semantic models for all new implementations.

**Key claim:** "Cut semantic model creation from days to minutes."

**Early adopters:** eSentire, HiBob, Simon AI, VTS.

---

## 2. Two Sub-Features

### 2a. Fast-Gen (Tableau Import)

Generates a Snowflake Semantic View from an existing Tableau workbook.

**Supported file formats:** `.TWB`, `.TWBX`, `.TDS` (max 50 MB)

**What it extracts from Tableau:**
- Tables, columns, relationships
- Calculated fields, parameters, filters
- Custom SQL (requires CREATE VIEW privilege — parsed into a Snowflake view)

**Limitations:**
- Published datasources NOT supported
- Large extracts inside `.twbx` NOT supported
- Tableau Level of Detail (LOD) calculations NOT supported

**Workflow (Snowsight UI only):**
1. Navigate to AI & ML > Cortex Analyst > Create new Semantic View
2. Upload Tableau file as "context"
3. Review/select tables and columns
4. Configure AI options (sample values, auto-generated descriptions)
5. Create and save — generation takes a few minutes

**No programmatic API for Fast-Gen.** UI-only.

### 2b. Agentic Optimize (Query History)

Analyzes existing verified queries to extract generalizable semantic concepts and suggest improvements to an existing semantic view.

**What it does:**
- Parses verified queries to find implicit business definitions
- Example: From a query asking about "active users last month", it extracts the definition of "active" and suggests adding an `is_active` filter to the customer table
- Suggests better metric definitions, stronger dimension relationships, more intuitive naming

**Workflow (Snowsight UI only):**
1. Navigate to AI & ML > Cortex Analyst
2. Select the semantic view to optimize
3. Under Suggestions > "Get more suggestions"
4. Choose role and warehouse for optimization

**Performance:**
- Executes verified queries up to 4 times each
- Takes minutes (small set) to hours (dozens of slow queries)
- More than 20 verified queries may cause slow performance
- Simple queries provide limited useful information

**Prerequisites:**
- CORTEX_USER role (directly assigned, not via secondary roles)
- Access to at least one LLM (Claude Sonnet 4 recommended by Snowflake)
- Read access to underlying tables/columns
- Existing semantic view with at least one verified query

**No programmatic API for Agentic Optimize.** UI-only. Preview Feature.

---

## 3. Available Programmatic Functions (NOT Autopilot — but related)

While Autopilot itself is UI-only, Snowflake provides these programmatic functions for working with semantic views:

| Function | Purpose |
|----------|---------|
| `SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML(schema, yaml, verify_only)` | Create/validate semantic view from YAML |
| `SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW(view_name)` | Export semantic view back to YAML |
| `SYSTEM$EXPORT_TDS_FROM_SEMANTIC_VIEW(view_name)` | Generate Tableau .TDS file from semantic view (per demo notebook) |
| `CREATE SEMANTIC VIEW` SQL | Native SQL DDL for semantic views |
| `ALTER SEMANTIC VIEW` | Modify existing semantic view |
| `DESCRIBE SEMANTIC VIEW` | Inspect semantic view structure |
| `SHOW SEMANTIC VIEWS` | List semantic views |
| `SHOW SEMANTIC DIMENSIONS` | List dimensions |
| `SHOW SEMANTIC METRICS` | List metrics |
| `SHOW SEMANTIC DIMENSIONS FOR METRIC` | Show dimensions applicable to a metric |
| `SNOWFLAKE.CORTEX.COMPLETE()` | LLM completion (used internally by Autopilot for AI generation) |

**Community approach (GENERATE_SEMANTIC_VIEW stored procedure):**
A dev community pattern uses `SNOWFLAKE.CORTEX.COMPLETE()` with table metadata + sample data to auto-generate semantic view DDL. Single-table only. Uses Claude Sonnet 4.5 as default LLM. This is effectively what ekaiX's generation agent already does but more sophisticated.

---

## 4. Open Semantic Interchange (OSI) Initiative

Launched Sept 2025 by Snowflake, Salesforce, BlackRock, dbt Labs, and RelationalAI. Supported by Alation, Atlan, Cube, Hex, Honeydew, Mistral AI, Omni, Select Star, Sigma, ThoughtSpot.

**Goal:** Create a common open standard for semantic definitions across AI, BI, and data analytics ecosystem.

**Autopilot connection:** Autopilot continuously maintains Open Semantic Interchange connections — syncing business logic across dbt Labs, Looker, Sigma, ThoughtSpot (GA "soon").

**Implication for ekaiX:** OSI means semantic definitions will become portable across tools. ekaiX's semantic views could potentially feed into this ecosystem, making them more valuable (not just for Cortex Agents but for all OSI-compatible tools).

---

## 5. Semantic Views vs Legacy Semantic Models

| Aspect | Semantic View (new) | Semantic Model (legacy) |
|--------|-------------------|----------------------|
| Storage | Schema-level object in Snowflake | YAML file on a Snowflake stage |
| Privileges | Native RBAC (GRANT/REVOKE) | Stage-level access only |
| Sharing | Private listings, Marketplace, org listings | Not shareable natively |
| Catalog | Integrated with Snowflake metadata catalog | Not cataloged |
| Creation | SQL DDL, Snowsight UI, YAML upload, Autopilot | Manual YAML authoring |
| Round-trip | `SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW` | Direct file access |
| Replication | NOT supported | N/A (file-based) |
| Cortex Analyst | Fully supported | Backward-compatible |
| Cortex Agents | Fully supported | Supported |
| YAML limit | 1 MB / ~32K tokens | No hard limit |
| Recommendation | **Recommended for all new implementations** | Legacy/backward-compatible |

**Key distinction:** Semantic Views have `join_type` and `relationship_type` automatically inferred from data — legacy YAML requires manual specification.

---

## 6. Competitive Analysis: Autopilot vs ekaiX

### What Autopilot Does That ekaiX Also Does
| Capability | Autopilot | ekaiX |
|-----------|-----------|-------|
| Auto-generate semantic views | Yes (from Tableau files or table metadata) | Yes (from BRD + ERD via AI conversation) |
| Publish to Snowflake | Yes (native schema object) | Yes (`SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML`) |
| Column classification (fact/dim/metric) | Yes (AI-powered) | Yes (template-based + AI) |
| Synonym generation | Yes | Yes |
| Description generation | Yes | Yes |
| Validated against real data | Yes (via Agentic Optimize) | Yes (validation agent with `verify_only=TRUE`) |

### What Autopilot Does That ekaiX Does NOT
| Capability | Details | Impact on ekaiX |
|-----------|---------|----------------|
| **Tableau .TWB import** | Extracts business logic from existing Tableau workbooks | HIGH — many enterprises have existing Tableau definitions |
| **Query history mining** | Agentic Optimize learns from ACCOUNT_USAGE.QUERY_HISTORY | MEDIUM — backward-looking, captures existing patterns |
| **BI tool sync** | Continuous updates to dbt, Looker, Sigma, ThoughtSpot via OSI | MEDIUM — ekaiX publishes to Snowflake only |
| **Continuous optimization** | Learns from user activity over time to refine definitions | LOW — ekaiX supports post-publish revision (manual trigger) |
| **TDS export** | `SYSTEM$EXPORT_TDS_FROM_SEMANTIC_VIEW` generates Tableau files | LOW — nice-to-have for Tableau users |

### What ekaiX Does That Autopilot Does NOT
| Capability | Details | Impact |
|-----------|---------|--------|
| **Conversational requirements capture** | Interactive BRD from business analyst conversation | HIGH — captures business INTENT, not just existing patterns |
| **Forward-looking requirements** | New metrics/dimensions not in query history | HIGH — Autopilot only knows what already exists |
| **Data quality assessment** | Pre-modeling quality scoring and reporting | HIGH — protects from building on bad data |
| **ERD discovery + visualization** | Auto-detect PKs/FKs, build interactive ERD | MEDIUM — Autopilot uses table metadata but no visualization |
| **Cortex Agent creation** | Auto-creates and publishes Cortex Agents | MEDIUM — Autopilot creates views only, not agents |
| **Multi-table relationships from conversation** | User confirms/corrects inferred relationships | MEDIUM — Autopilot infers silently |
| **Iterative revision with approval gates** | BRD review → YAML review → validation → publish | HIGH — governance workflow that Autopilot lacks |
| **Business context documentation** | Full 7-section BRD artifact for audit trail | HIGH — Autopilot has no requirements documentation |
| **SPCS Native App deployment** | Runs entirely within customer's Snowflake account | MEDIUM — Autopilot is a Snowflake-native feature |

---

## 7. Honest Threat Assessment

### Direct Threat Level: MEDIUM-HIGH for Generation Phase

Autopilot directly competes with ekaiX's generation phase. For organizations that:
- Already have Tableau workbooks defining their metrics → Autopilot Fast-Gen is faster and simpler
- Have extensive query history → Agentic Optimize builds from proven patterns
- Just want a semantic view quickly → Autopilot is "minutes" vs ekaiX's ~20-minute conversation

### Where ekaiX Remains Differentiated

1. **New requirements** — Autopilot cannot capture requirements that don't exist in query history or Tableau files. If a business analyst says "I want a new MTBF metric combining these fields in this specific way", Autopilot has no mechanism for that. ekaiX was built for exactly this.

2. **Business context preservation** — Autopilot generates a semantic view but doesn't document WHY those metrics were chosen, what business rules apply, or what assumptions were made. ekaiX's BRD captures all of this.

3. **Governance workflow** — Autopilot is a "generate and done" tool. ekaiX provides review gates (BRD review, YAML review, validation, publish approval) that enterprise governance requires.

4. **Data quality gating** — Autopilot will build a semantic view on bad data without warning. ekaiX's discovery phase surfaces quality issues BEFORE modeling begins.

5. **Cortex Agent creation** — Autopilot creates semantic views. ekaiX creates semantic views AND Cortex Agents AND grants access — full deployment pipeline.

6. **Non-technical users** — Autopilot requires navigating Snowsight, understanding what tables/columns to select, and interpreting AI-generated descriptions. ekaiX's conversational interface is designed for business analysts who don't know Snowflake.

### Counter-argument (be honest)

The "non-technical user" argument weakens if Snowflake improves Autopilot's UX. The "new requirements" argument weakens if organizations mostly formalize existing patterns (which is common). The "governance" argument only matters in regulated industries.

---

## 8. Integration Opportunities for ekaiX

### 8a. Autopilot Import Mode (HIGH VALUE)

**Concept:** When a customer already has a semantic view built by Autopilot, let ekaiX import and enhance it rather than starting from scratch.

**Implementation:**
```python
# In explorer or generation agent:
yaml = execute_query("SELECT SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW('DB.SCHEMA.EXISTING_SV')")
# Parse YAML, show to user, ask what to add/modify
# Enter YAML REVISION MODE with the imported definition
```

**Value:** Customers who already used Autopilot can use ekaiX to add requirements-driven enhancements that Autopilot couldn't capture.

### 8b. Query History Mining in Discovery (MEDIUM VALUE)

**Concept:** During discovery, mine `ACCOUNT_USAGE.QUERY_HISTORY` to understand how the data is already being used. Show the user: "These are the top 20 queries already running against your data — here's what they tell us about common metrics and dimensions."

**Implementation:**
```sql
SELECT query_text, execution_count, avg_execution_time
FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY
WHERE database_name = :database AND schema_name = :schema
AND query_type = 'SELECT'
AND execution_status = 'SUCCESS'
ORDER BY execution_count DESC
LIMIT 50;
```

**Value:** Combines Autopilot's backward-looking strength with ekaiX's forward-looking requirements capture. Discovery agent can say: "I see your team already runs 50 queries against this data. The most common patterns suggest metrics like X and Y. Do you want to formalize these, and what NEW metrics do you need?"

### 8c. Cortex-Native Generation (MEDIUM VALUE)

**Concept:** Use `SNOWFLAKE.CORTEX.COMPLETE()` inside Snowflake instead of external LLM for generation. Similar to the community `GENERATE_SEMANTIC_VIEW` approach but with ekaiX's multi-table, BRD-driven, template-based assembly.

**Implementation:** Replace the external LLM call in generation agent with a Snowflake stored procedure that calls `CORTEX.COMPLETE()` with the same prompt. Benefits: no data leaves Snowflake, uses Snowflake's optimized LLM infrastructure.

**Caveat:** Cortex models may be less capable than Gemini/Claude for complex multi-table generation. Needs testing.

### 8d. TDS Export (LOW VALUE, NICE-TO-HAVE)

**Concept:** After publishing, offer "Export to Tableau" button that calls `SYSTEM$EXPORT_TDS_FROM_SEMANTIC_VIEW` to generate a .TDS file.

**Value:** Small but completes the BI integration story.

### 8e. Continuous Optimization Feed (FUTURE)

**Concept:** After ekaiX publishes a semantic view, connect Agentic Optimize to continuously improve it based on how the Cortex Agent is actually being used.

**Implementation:** This requires Snowflake to support programmatic access to Agentic Optimize (currently UI-only). Monitor for API availability.

---

## 9. Recommended Positioning for ekaiX

**Don't compete with Autopilot on speed.** Autopilot wins for quick, backward-looking semantic view generation from existing artifacts.

**Compete on:**
1. **Business intent capture** — "Autopilot tells Snowflake what you already know. ekaiX tells Snowflake what you actually need."
2. **Governance and auditability** — Full BRD + review gates + approval workflow
3. **Forward-looking requirements** — New metrics that don't exist in query history
4. **End-to-end deployment** — Semantic view + Cortex Agent + access grants in one flow
5. **Non-technical accessibility** — Conversational interface vs Snowsight navigation

**Integrate where possible:**
- Import existing Autopilot-generated views as a starting point
- Mine query history during discovery (adopt Autopilot's backward-looking strength)
- Export to Tableau after publishing

---

## 10. Key Technical Facts Summary

| Fact | Detail |
|------|--------|
| GA date | February 3, 2026 |
| Access | Snowsight UI only (no programmatic API for Autopilot itself) |
| Programmatic alternatives | `SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML`, `SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW`, `CREATE SEMANTIC VIEW` SQL |
| Input sources | Tableau files (.TWB/.TWBX/.TDS), query history, table metadata |
| BI tool sync | dbt Labs, Looker, Sigma, ThoughtSpot (via Open Semantic Interchange) |
| Optimization LLM | Claude Sonnet 4 recommended |
| YAML limit | 1 MB / ~32K tokens per semantic view |
| Replication | Semantic views NOT supported for replication |
| Sharing | Private listings, Marketplace, org listings (cross-region NOT supported) |
| Prerequisites | CREATE SEMANTIC VIEW privilege, USAGE, SELECT on source tables |
| Optimization prereq | CORTEX_USER role (directly assigned), existing verified queries |

---

## Sources

- [Snowflake Semantic View Autopilot Documentation](https://docs.snowflake.com/en/user-guide/views-semantic/autopilot)
- [Snowflake Press Release — Feb 3, 2026](https://www.businesswire.com/news/home/20260203233912/en/Snowflake-Delivers-Semantic-View-Autopilot-as-the-Foundation-for-Trusted-Scalable-Enterprise-Ready-AI)
- [Snowflake Blog — Semantic View Autopilot](https://www.snowflake.com/en/blog/semantic-view-autopilot/)
- [Optimize Semantic Views with Verified Queries](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/analyst-optimization)
- [Semantic Views Overview](https://docs.snowflake.com/en/user-guide/views-semantic/overview)
- [SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML](https://docs.snowflake.com/en/sql-reference/stored-procedures/system_create_semantic_view_from_yaml)
- [SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW](https://docs.snowflake.com/en/sql-reference/functions/system_read_yaml_from_semantic_view)
- [Snowflake Demo Notebook — Semantic View Autopilot](https://github.com/Snowflake-Labs/snowflake-demo-notebooks/blob/main/Snowflake_Semantic_View_Autopilot/Semantic_View_Autopilot.ipynb)
- [Constellation Research — Cortex Code, Autopilot GA](https://www.constellationr.com/insights/news/snowflake-cortex-code-semantic-view-autopilot-ga-0)
- [Dev Community — Auto-Generate Semantic Views with AI](https://dev.to/tsubasa_tech/auto-generate-snowflake-semantic-views-with-ai-a-developers-fast-track-to-cortex-analyst-44bp)
- [phData — Semantic Views Real-World Insights](https://www.phdata.io/blog/snowflake-semantic-views-real-world-insights-best-practices-and-phdatas-approach/)
- [SiliconANGLE — Snowflake Platform-Native AI](https://siliconangle.com/2026/02/03/snowflake-bets-platform-native-ai-enterprises-rethink-custom-development/)
- [Open Semantic Interchange Initiative](https://www.snowflake.com/en/blog/open-semantic-interchange-ai-standard/)
- [Semantic View YAML Specification](https://docs.snowflake.com/en/user-guide/views-semantic/semantic-view-yaml-spec)
- [Snowflake Developer Guide — Build Semantic Views with Autopilot](https://www.snowflake.com/en/developers/guides/snowflake-semantic-view-autopilot/)
- [Semantic Views vs Semantic Models — Medium](https://medium.com/@binaga.bhushan/snowflake-intelligence-semantic-views-vs-semantic-models-what-really-works-and-why-75ebe30925a3)
