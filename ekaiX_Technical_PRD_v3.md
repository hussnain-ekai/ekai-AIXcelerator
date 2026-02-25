# ekaiX — Comprehensive Technical Product Requirements Document

**Product:** ekai AIXcelerator (ekaiX)
**Version:** 3.2
**Date:** February 25, 2026
**Document Type:** Authoritative Technical PRD — replaces v1 Technical PRD and v2 Extended PRD
**Target Audience:** AI Coding Agents (Claude Code), Engineering Teams, Product Stakeholders

---

## AI Coding Agent Build Instructions

> **This section is mandatory reading for any AI coding agent (Claude Code or equivalent) tasked with building ekaiX.**

### Dependency and Documentation Freshness

Your training data has a knowledge cutoff (typically 2024 or earlier). It is now February 2026. Before implementing ANY component, dependency, or integration, you MUST search the internet for the current documentation, API signatures, and package versions. Never rely on memorized APIs — they have changed. This applies to every dependency: Next.js, MUI, FastAPI, LangChain, LangGraph, Deep Agents, Snowflake SPCS, Snowpark, and all others.

### Production-Only Policy

No mock data, no simulations, no synthetic data, no hardcoded sample responses. Every data path must connect to real services (Snowflake, PostgreSQL, Neo4j, Redis, MinIO). All database endpoints must return actual data from live queries. If a service is unavailable during development, fail explicitly — do not fall back to fake data.

### Product Generality

ekaiX is a product used by multiple customers for multiple projects. Do not overfit on a single use case, example dataset, or industry vertical. All schema names, field names, prompts, and UI labels must be generic and reusable. Customer-specific configuration belongs in environment variables or runtime settings, never in source code.

### Build Approach

- Read this PRD completely before starting implementation
- Follow the must-follow build checklist in Appendix A
- Reference existing specification files (listed below) for detailed schemas and contracts
- When in doubt about a specification, check the referenced file — not your training data

### Reference Specification Files

These files contain detailed schemas, contracts, and specifications that complement this PRD:

| File | Contents |
|------|----------|
| `docs/plans/postgresql-schema.sql` | Complete PostgreSQL 18 schema (tables, indexes, RLS policies, triggers, partitioning) |
| `docs/plans/api-specification.yaml` | OpenAPI 3.1 spec (30+ endpoints, request/response schemas, SSE streaming, error codes) |
| `docs/plans/2026-02-04-ui-ux-design-spec.md` | Validated UI/UX design spec (every screen, component, interaction, state) |
| `docs/plans/tech-stack-research.md` | Verified 2026 package versions, breaking changes, install commands, decision log |
| `CLAUDE.md` | Project conventions, environment variables, development commands, mandatory rules |

---

## Table of Contents

- [Part I: Product Definition](#part-i-product-definition)
- [Part II: Core Concepts](#part-ii-core-concepts)
- [Part III: User Experience](#part-iii-user-experience)
- [Part IV: Technical Architecture](#part-iv-technical-architecture)
- [Part V: Component Specifications](#part-v-component-specifications)
- [Part VI: Agent System](#part-vi-agent-system)
- [Part VII: Intelligence Infrastructure](#part-vii-intelligence-infrastructure)
- [Part VIII: Data Models](#part-viii-data-models)
- [Part IX: Snowflake Integration](#part-ix-snowflake-integration)
- [Part X: LLM Configuration](#part-x-llm-configuration)
- [Part XI: Security and Governance](#part-xi-security-and-governance)
- [Part XII: Reliability and Observability](#part-xii-reliability-and-observability)
- [Part XIII: Non-Functional Requirements](#part-xiii-non-functional-requirements)
- [Part XIV: Development Environment](#part-xiv-development-environment)
- [Part XV: Deployment and Packaging](#part-xv-deployment-and-packaging)
- [Part XVI: Testing Strategy](#part-xvi-testing-strategy)
- [Part XVII: Release Gates](#part-xvii-release-gates)
- [Part XVIII: Delivery Roadmap](#part-xviii-delivery-roadmap)
- [Appendices](#appendices)

---

# Part I: Product Definition

## 1. Executive Summary

### What ekaiX Is

ekaiX (ekai AIXcelerator) is a governed enterprise intelligence system deployed as a Snowflake Native App. It transforms enterprise data — structured warehouse tables, unstructured business documents, or both — into a trusted semantic intelligence layer that delivers accurate, evidence-backed answers to business users through natural conversation.

ekaiX is not a generic chatbot. It is not only a semantic model generator. It is a system that:

1. Builds semantic intelligence from any combination of structured and unstructured sources
2. Grounds every answer in deterministic evidence via a knowledge graph
3. Serves accurate answers through one agentic chat experience
4. Uses Snowflake Cortex services for execution and serving
5. Refuses to fabricate certainty when evidence is weak — abstains with a concrete recovery path

### How ekaiX Differs from ekai

ekai (docs.ekai.ai) is the parent platform — a multi-warehouse semantic modeling product supporting Snowflake, Databricks, BigQuery, Synapse, and PostgreSQL. It generates DBT projects, data catalogs, business glossaries, metrics, lineage, and validation rules.

ekaiX is a separate, focused product variant:

| Dimension | ekai | ekaiX |
|-----------|------|-------|
| Deployment | Multi-platform (SaaS, AWS, Snowflake) | Snowflake Native App only (SPCS) |
| Data Sources | 5+ warehouse platforms | Snowflake primary (architect for extensibility) |
| Data Layers | Gold layer focus | Any layer: raw, silver, gold. Non-gold triggers native transformation |
| Transformation | DBT project generation | Native Snowflake (Dynamic Tables, Snowpark, stored procedures) |
| Documents | PDF upload for BRD context | Full document intelligence (extraction, facts, entity resolution, hybrid Q&A) |
| Answer Quality | Generated artifacts (code, catalogs) | Evidence-backed answers with trust contracts |
| Core Value | Automate semantic modeling workflow | Governed enterprise intelligence — accurate answers from any data |
| Differentiator | Speed (11x faster data projects) | Trust (evidence-first, abstain over hallucinate) |

### Why ekaiX Exists

Enterprise organizations have mature data warehouses and growing document libraries. Business users need answers that span both. Generic AI tools fail because they are not grounded in governed enterprise data, do not preserve permissions and tenant boundaries, return plausible but unverifiable answers, and cannot reliably combine warehouse truth with document truth.

ekaiX solves this by building a business semantic intelligence layer — a knowledge graph that maps entities, relationships, metrics, facts, and evidence paths across all sources — and using that graph to ground every answer in deterministic evidence.

---

## 2. Problem Statement

Business users face three compounding problems:

**Problem 1: Data is fragmented across structured and unstructured sources.** Revenue metrics live in Snowflake. Pricing policies live in PDFs. Customer segment definitions live in Confluence. No single system combines these into a coherent intelligence layer.

**Problem 2: Generic AI tools produce plausible but unverifiable answers.** An LLM can synthesize a convincing answer about Q3 revenue, but the user has no way to verify whether the number came from actual data, a hallucinated pattern, or a stale document. For enterprise decisions, plausibility is not enough — verifiability is required.

**Problem 3: Semantic modeling is a technical bottleneck.** Creating semantic views, data catalogs, and business glossaries requires data engineering expertise. Business users who understand the domain cannot self-serve. Data engineers who can build models lack deep business context.

**ekaiX's Solution:** A conversational AI system that discovers and profiles data sources (any layer), captures business requirements through natural conversation, builds a knowledge-graph-grounded semantic intelligence layer, transforms data when needed using native Snowflake mechanisms, generates semantic views and business artifacts, serves evidence-backed answers with trust contracts, and publishes to Snowflake Intelligence as Cortex Agents.

---

## 3. Product Vision and End Goal

### Vision

A business analyst can create and use a trusted data product through natural conversation, without SQL or data engineering expertise, and receive accurate, explainable, evidence-backed answers that span structured warehouse data and unstructured business documents.

### End Goal

Deliver a production-ready Snowflake Native App where:

1. Users start with structured sources, unstructured sources, or both
2. ekaiX builds and maintains semantic intelligence artifacts for those sources
3. Users ask operational and analytical questions in one cohesive chat
4. Every answer is evidence-backed and permission-safe
5. Cortex Agent serving is publishable and governed in Snowflake
6. The system abstains with a concrete recovery path when evidence is insufficient

### Success Criteria

- Full workflow (source registration to published Cortex Agent) completes in under 35 minutes
- Generated semantic definitions work correctly more than 90% of the time
- Confident-but-wrong rate below 2% (critical error rate)
- Exactness correctness above 95% for deterministic asks
- Citation validity above 98%
- Cross-tenant data leakage: zero (absolute)

---

## 4. Product Principles

1. **Single business chat, multiple engines under the hood.** The user talks to one agent in one thread. Structured queries, document retrieval, hybrid synthesis, transformations — all happen invisibly behind a single conversational interface.

2. **Evidence first, narrative second.** Every answer must be grounded in verifiable evidence before it is narrated to the user. The evidence (SQL references, document citations, graph paths) is always available for inspection.

3. **No deterministic evidence, no deterministic answer.** For exact-value questions (numbers, dates, names), the answer must come from SQL row-level evidence or normalized document facts. Pure semantic-similarity chunk retrieval is not sufficient for exact values.

4. **Abstain safely instead of hallucinating.** When evidence is missing, conflicting, or below confidence threshold, ekaiX must abstain clearly and provide a concrete next step. Example: "I cannot determine the exact Q3 revenue because the REVENUE table has no data after August. You could upload the September close report or check if the ETL has completed."

5. **Business language by default, technical detail on demand.** The primary chat never shows UUIDs, SQL keywords, database technology names, tool names, or implementation jargon. Technical details are available in expandable panels for power users.

6. **No limbo states; always show status and next action.** Every operation emits an explicit lifecycle state (running, waiting, blocked, failed, recovered, completed). Every failure state includes what the user can do next.

7. **User-controlled context; system-explained impact of context changes.** Adding, removing, or updating sources triggers targeted staleness detection — not a full pipeline reset. The system shows what changed and what needs revalidation.

---

## 5. Personas

### Primary: Business Analyst

- Understands their business domain deeply (sales, finance, supply chain, HR, regulatory, etc.)
- Does NOT know SQL, database internals, or data engineering terminology
- Thinks in business concepts: "revenue by region," "contract renewal rate," "policy compliance score" — never in tables, joins, or column names
- Needs accurate metrics, transaction details, and policy context
- Needs to adjust requirements iteratively in conversation
- Expects the system to speak their language, ask intelligent questions that demonstrate understanding of their domain, and translate technical findings into business impact
- Judges ekaiX by: "Did I get the right answer? Can I explain why it's right?"

**Interaction model:** The business analyst drives every stage through conversation. During discovery, the agent builds a shared understanding of the business domain (Conceptual Data Model per DAMA CDM framework) through intelligent questioning — not silent batch processing. During requirements capture, the agent interviews the analyst to extract metrics, dimensions, business rules, and acceptance criteria through progressive disclosure. The analyst never sees a tool name, a SQL keyword, or a database technology name — all complexity is hidden behind business language.

### Secondary: Analytics / Product Owner

- Owns metric definitions and business semantics
- Needs reviewable, reusable semantic artifacts (YAML, BRD, data catalog)
- Needs confidence before publishing to Snowflake Intelligence
- Judges ekaiX by: "Are the generated artifacts correct and complete?"

### Governance: Data / Platform Steward

- Enforces security, permissions, compliance, and auditability
- Needs clear operational visibility, traceability, and controls
- Needs to verify RCR enforcement and workspace isolation
- Judges ekaiX by: "Can I audit every answer? Is tenant isolation airtight?"

---

## 6. Scope

### In Scope

- Source-agnostic data product creation (structured-only, document-only, hybrid)
- Data discovery and profiling for any warehouse layer (raw, curated, silver, gold)
- Native Snowflake transformations when source is not gold-ready (Dynamic Tables, Snowpark, stored procedures — NOT DBT)
- Direct semantic views when source is gold-ready
- Document extraction, fact normalization, entity resolution
- Mission control workflow from discovery to publish
- Business requirements capture through AI conversation
- Semantic view YAML generation and validation
- Neo4j knowledge graph for business semantic grounding
- Hybrid question planning and execution with trust contracts
- Cortex Agent publishing to Snowflake Intelligence
- Artifact download (ZIP with semantic definitions, catalog, glossary, metrics, lineage)
- Evidence and trust UX (confidence, exactness, citations, recovery)
- No-limbo execution states and failure recovery
- Governance, audit, and cross-tenant safety controls
- One cohesive chat experience per data product (build and explore in same thread)

### Out of Scope

- DBT project generation (ekai platform feature, not ekaiX)
- Multi-warehouse support beyond Snowflake (future extensibility only)
- Open-ended, unguided AI assistant with no governance
- Replacing enterprise warehousing/MDM systems
- Autonomous policy overrides without admin controls
- Code sync to Git repositories (ekai platform feature)
- BI-tool-specific export adapters (PowerBI, Tableau, LookML) — future scope

### Relationship to ekai Platform

ekaiX shares the ekai brand design system and some architectural patterns but is a distinct product with a separate codebase, separate deployment model (Snowflake Native App only), separate value proposition (governed intelligence answers, not just semantic modeling artifacts), and shared design language (ekai brand colors, typography, component conventions).

---

# Part II: Core Concepts

## 7. Data Product

A data product in ekaiX is a governed intelligence unit — the central entity around which all work is organized.

### Source Registrations

- **Structured sources:** Snowflake database + schema + table references with the user's RCR permissions
- **Document sources:** Uploaded PDFs/DOCX files with extraction status and quality diagnostics
- **Both:** Any combination of the above

At least one source type is required. Table selection is NOT mandatory — a data product can start with documents only.

### Intelligence Artifacts (produced by the system)

| Artifact | Description |
|----------|-------------|
| ERD Graph | Entity-relationship diagram with AI-detected PK/FK, confidence scores |
| Data Quality Report | Per-table and per-column health assessment |
| Business Requirements Document (BRD) | Structured capture of business intent, metrics, dimensions, filters |
| Semantic View YAML | Snowflake-compatible semantic definition |
| Data Catalog | Auto-generated table/column documentation |
| Business Glossary | Standardized term definitions |
| Metrics/KPIs | Calculated measure definitions with calculation logic |
| Validation Rules | Data quality tests with severity levels |
| Data Lineage | Source-to-output transformation map (OpenLineage format) |
| Document Semantic Model | Extracted chunks, entities, facts, and cross-source links |

### Trust Evidence

- Answer contracts — per-answer evidence records (source mode, exactness, confidence, citations)
- Evidence packets — SQL references, document citations, graph paths
- Audit trail — every action logged with user, timestamp, and details

### Published Serving Configuration

- Cortex Agent deployed to Snowflake Intelligence with semantic view + search bindings
- Downloadable artifact package (ZIP with all generated artifacts)

### Lifecycle States

A data product progresses through: draft, discovery, requirements, modeling, validation, published, archived. Each state transition is explicit and auditable. The user can return to any previous stage to modify requirements or sources.

---

## 8. Business Semantic Intelligence

This is the core differentiator. Traditional RAG systems use vector dot products (embedding similarity) to retrieve relevant chunks. This works for "find documents about topic X" but fails for enterprise questions like "What was the exact contract value for customer Y in Q3?"

ekaiX uses business semantic / logical similarity grounded through the Neo4j knowledge graph.

### Why Knowledge Graph, Not Just Embeddings

| Approach | How It Works | Failure Mode |
|----------|-------------|-------------|
| Vector similarity (RAG) | Embed question, find nearest chunks, synthesize answer | Returns chunks that look similar but may contain wrong entity, wrong time period, or outdated values |
| Business semantic grounding (ekaiX) | Resolve entities, traverse graph paths, validate evidence chains, synthesize with proof | Only answers when explicit evidence path exists from question entities to source data |

### How It Works

1. **Entity Resolution:** When a user asks about a specific entity, ekaiX resolves it to a specific node in Neo4j, linked to both warehouse tables and document references.

2. **Relationship Traversal:** The graph traces paths from question entities through tables, measures, and time dimensions. Each hop is an explicit, auditable relationship.

3. **Evidence Validation:** Before answering, the system verifies: Does the entity have a concrete data reference? Does the metric have a defined calculation? Does the time dimension resolve to actual date ranges? Are there conflicting facts?

4. **Confidence Decision:** All paths resolve with high confidence leads to validated_exact. Some paths uncertain leads to estimated with warnings. Key paths missing leads to abstain with specific recovery action.

### Graph Model Purpose

The Neo4j knowledge graph is NOT just an ERD visualization tool. It is the grounding backbone for entity resolution across structured and unstructured sources, claim validation (every metric claim must have a requirement to semantic mapping to data path), conflict detection (document facts that contradict warehouse data trigger warnings), staleness tracking (when sources change impacted graph paths are marked stale), and hallucination prevention (no answer without an explicit evidence path).

---

## 9. Trust Contract

Every assistant response in ekaiX carries a trust contract — a machine-readable and UI-displayable record of the answer's evidence basis.

### Contract Fields

| Field | Type | Values |
|-------|------|--------|
| source_mode | enum | structured, document, hybrid, unknown |
| exactness_state | enum | validated_exact, estimated, insufficient_evidence, not_applicable |
| confidence_decision | enum | high, medium, abstain |
| trust_state | enum | answer_ready, answer_with_warnings, abstained_missing_evidence, abstained_conflicting_evidence, blocked_access, failed_recoverable, failed_admin |
| evidence_summary | string or null | Human-readable summary of evidence used |
| conflict_notes | string array | Specific conflicts found between sources |
| citations | Citation array | SQL refs, document refs, graph paths with confidence scores |
| recovery_actions | RecoveryAction array | What user can do if answer is not ready |
| metadata.observed_at | ISO timestamp | When the answer was produced |
| metadata.evidence_created_at | ISO timestamp | When evidence was gathered |
| metadata.model_id | string | LLM model used |
| metadata.trace_id | string | Langfuse trace ID |

### Citation Structure

Each citation contains: type (sql, document, graph), source_id (table FQN or document ID), reference (SQL query or chunk text), confidence (0.0-1.0), provenance (how this citation was obtained).

### Recovery Action Structure

Each recovery action contains: action (machine-readable type), description (human-readable explanation), metadata (action-specific parameters).

### Exactness Policy

For exact-value questions (numbers, dates, specific names):

- **Allowed evidence:** SQL row-level results, normalized document facts (subject-predicate-object triples extracted and verified)
- **Not allowed as sole evidence:** Pure semantic-similarity chunk retrieval, embedding-based nearest-neighbor results
- **On missing deterministic evidence:** exactness_state = insufficient_evidence, trust_state = abstained_missing_evidence, plus a specific recovery action
- **On conflicting evidence:** trust_state = abstained_conflicting_evidence, plus conflict notes explaining what conflicts and from which sources

### Trust State Flow

Question received leads to route planning (structured/document/hybrid/unknown). Route planning leads to lane execution (structured lane via SQL, document lane via graph + retrieval, or both in parallel). Lane execution leads to evidence validation (graph path check, conflict detection, exactness check). Evidence validation leads to one of: answer_ready, answer_with_warnings, abstained_missing_evidence, abstained_conflicting_evidence, blocked_access, or failed states.

---

## 10. Source Modes

### Structured Only

The data product contains only Snowflake table references. All questions are answered via SQL execution against semantic views. Evidence is always SQL-backed. Discovery profiles schemas. Requirements capture metrics/dimensions. Modeling generates semantic view YAML. Q&A executes SQL via RCR with row-level evidence.

### Document Only

The data product contains only uploaded documents. Questions are answered via the two-tier document intelligence architecture:

**Tier 1 (always-on): Cortex Search Service.** After document extraction, enriched text chunks are uploaded to a Snowflake table (`EKAIX.{dp_name}_DOCS.DOC_CHUNKS`) and indexed by a Cortex Search Service. This handles qualitative questions — policies, context, explanations, definitions. The Cortex Agent routes document questions to this search service natively.

**Tier 2 (requirements-driven): Structured Extraction.** When the BRD contains quantitative requirements (metrics, KPIs, numeric comparisons, aggregations), the model-builder agent uses `AI_EXTRACT` to pull typed data from document chunks into real Snowflake tables in the `EKAIX.{dp_name}_MARTS` schema. A semantic view is then generated over these extracted tables, enabling Cortex Analyst text-to-SQL queries. This is only created when the business requirements demand quantitative analysis.

**Pipeline path:** Discovery runs extraction diagnostics and entity detection. Requirements capture business intent. If BRD is qualitative-only, orchestrator routes directly to publishing with Cortex Search only (no semantic view). If BRD has quantitative requirements, orchestrator routes to model-builder in document extraction mode — it extracts structured data, generates a semantic view, validates, then publishes with both Cortex Search and Cortex Analyst tools.

### Hybrid

The data product contains both structured (Snowflake tables) and document sources. The published Cortex Agent has dual tools:

- **Cortex Analyst (text-to-SQL):** Queries the semantic view over warehouse tables (and optionally extracted document tables). Handles metric/aggregation questions.
- **Cortex Search (DocumentSearch):** Searches uploaded document chunks. Handles policy/context/definition questions.

The Cortex Agent decides per-question which tool to use. Discovery runs both structured and document discovery. Requirements use combined context. The semantic view references real warehouse tables. Document chunks are uploaded to Snowflake and indexed by a Cortex Search Service. Both resources are bound to the single Cortex Agent at publish time. Q&A uses the agent's native dual-tool routing with evidence union and conflict resolution via the trust contract.

---

# Part III: User Experience

## 11. Entry and Onboarding

### Creating a Data Product

From the dashboard, the user clicks "Create Data Product" and provides:

1. **Name** — unique identifier (alphanumeric + underscore)
2. **Description** — brief business purpose
3. **Source Configuration:**
   - Databases — select Snowflake databases the user can access (queried via RCR)
   - Schemas — select schemas within chosen databases
   - Tables — optionally select specific tables (or all tables in schema)
   - Documents — upload PDFs/DOCX files (max 20MB each)

At least one source (tables or documents) is required. The user can add more sources at any time during the workflow.

### Source Registration

Structured sources: The system queries INFORMATION_SCHEMA using the user's RCR permissions. Only databases/schemas/tables the user's Snowflake role can access are shown. Selected tables are registered as source nodes in Neo4j.

Document sources: Files are uploaded to MinIO object storage. Extraction is triggered via Cortex PARSE_DOCUMENT. Extraction status is tracked (pending, processing, completed, failed). Extracted content is chunked, entities detected, facts normalized. All persisted as nodes in Neo4j.

---

## 12. Mission Control Stages

The data product progresses through six stages. Each stage has explicit entry criteria, outputs, and quality gates. The user can return to any previous stage.

### Stage 1: Discovery

**Purpose:** Understand selected sources and produce grounded context.

**Structured discovery:** Query INFORMATION_SCHEMA for tables, columns, constraints. Profile tables statistically (uniqueness, nullability, patterns, row counts, value distributions). Detect primary keys (>98% uniqueness, 0% nulls). Infer foreign key relationships (column name similarity, data type compatibility, value overlap). Classify tables as FACT or DIMENSION. Generate business-friendly descriptions. Build ERD in Neo4j. Produce Data Quality Report.

**Document discovery:** Run Cortex PARSE_DOCUMENT on each uploaded file. Assess extraction quality. Detect entities (people, organizations, products, dates, amounts). Extract candidate facts (subject-predicate-object triples). Assess document quality and coverage. Build document nodes in Neo4j.

**Output:** Discovery summary in business language (no technical jargon), Data Quality Report artifact, ERD artifact, document extraction diagnostics, recommended next actions.

**Quality Gate:** Data quality score above 60% or explicit user acknowledgment of quality issues.

**Communication Rules:** The Discovery Agent MUST follow the Analysis, Recognition, Question, Suggestion pattern. It engages the user conversationally between analysis steps — it is NOT a silent batch job. See Section 24 for full specification.

### Stage 2: Requirements

**Purpose:** Capture business intent deeply and precisely.

Agent asks focused, high-signal follow-up questions. Uses discovered context (ERD, data quality, document facts) to avoid generic questions. Captures business objective, metric definitions with calculation logic, dimensions, filters, constraints, time ranges, business rules. Integrates document context. Supports iterative edits at any time.

**Output:** Business Requirements Document (BRD) artifact. Requirement nodes in Neo4j linked to tables, columns, documents.

**Quality Gate:** BRD completeness validation (objective defined, at least one metric, dimensions specified).

### Stage 3: Modeling

**Purpose:** Convert requirements into executable semantic intelligence.

**For gold-ready data:** Generate semantic view YAML from BRD + ERD. Reference gold layer tables directly (no transformation).

**For non-gold data:** Assess data layer maturity (bronze/silver). Determine required transformations. Generate native Snowflake transformation logic (Dynamic Tables, Snowpark, stored procedures). Execute transformations to produce curated tables. Generate semantic view YAML referencing the produced tables.

**For documents:** Build normalized fact model from extracted entities and facts. Create retrieval surfaces. Link document entities to structured entities where overlap exists.

**For hybrid:** All of the above, plus cross-source entity linking rules and precedence rules when sources provide different values.

**Output:** Semantic view YAML artifact, data catalog, business glossary, metrics definitions, document semantic model, cross-source entity links in Neo4j.

### Stage 4: Validation

**Purpose:** Prevent incorrect or unverifiable answers before publish.

**Structured validation:** YAML syntax validation, column existence verification against actual schema, SQL compilation test (EXPLAIN without execution), test query execution with sample results (10-100 rows), cardinality checks (detect row inflation from bad joins), null percentage checks, data range validation.

**Document validation:** Extraction quality review, fact confidence assessment, citation integrity check.

**Hybrid validation:** Cross-source consistency check, join/link confidence assessment, exactness validation for deterministic asks.

**Output:** Validation report artifact, publish readiness state (ready or blocked with reasons), sample data preview.

**Quality Gate:** All critical validation checks must pass. Warnings are shown but do not block.

### Stage 5: Publishing

**Purpose:** Deploy serving configuration for business Q&A.

**Cortex Agent publishing:** Create semantic view in Snowflake. Create Cortex Agent with semantic view and search bindings. Configure agent instructions, description, LLM model. Grant access to user's Snowflake role. Return Snowflake Intelligence URL.

**Artifact packaging:** Generate downloadable ZIP containing semantic view YAML, data catalog, business glossary, metrics/KPI definitions, data lineage, BRD, ERD (DBML), data quality report, validation report.

**Output:** Published Cortex Agent accessible via Snowflake Intelligence. Downloadable artifact package.

### Stage 6: Explorer

**Purpose:** Ongoing business Q&A and artifact refinement.

After publishing, the user continues in the same chat to ask business questions, get evidence-backed answers with trust contracts, refine artifacts, add new sources and trigger targeted re-discovery, and modify requirements to regenerate semantic views.

The Explorer is not a separate interface — it is the continuation of the same cohesive chat experience.

---

## 13. Chat Interface

### Layout

The workspace consists of three panels: a sidebar (left, 240px fixed) with navigation and data product list; a message thread (center, flexible width) with phase stepper, messages, and input bar; and an artifact panel (right, resizable ~380px) showing the current artifact in detail.

### Message Thread Components

**User messages:** Plain text with optional file attachments.

**Agent messages:** Card-style bubbles containing narrative text in business language, tool call indicators (indented, collapsible) with progress descriptions, artifact cards (inline previews linking to artifact panel), trust cards (per-answer evidence display), and progress narratives for long-running operations.

### Trust Card (per answer)

Each answer displays: source mode badge (Structured/Document/Hybrid), confidence indicator (High checkmark / Medium warning / Abstain X), exactness state (Validated Exact / Estimated), evidence summary line, expandable evidence drawer, and recovery action buttons when applicable.

### Phase Stepper

Visual progress bar showing current stage: Discovery, Requirements, Modeling, Validation, Publishing, Explorer. Each stage shows completed, active, or pending state.

### Progress Narratives

For long-running operations, human-readable progress messages replace generic spinners. Examples: "Profiling ORDERS table (15 of 23 tables complete)...", "Detected 12 relationships between your customer and transaction tables", "Running validation queries — checking revenue calculations..."

### Artifact Panel

Right-side resizable panel showing the current artifact:

| Artifact | Renderer |
|----------|----------|
| ERD Graph | Interactive node/edge diagram (React Flow). Tables as nodes, relationships as edges with cardinality labels |
| Data Quality Report | Overall health score, per-table scores, per-column flagged issues with pass/warn/fail indicators |
| BRD Document | Structured business requirements with sections |
| YAML Viewer | Syntax-highlighted semantic view YAML with line numbers |
| Data Preview | Table grid of query results with CSV export |
| Data Catalog | Auto-generated table/column documentation |
| Business Glossary | Standardized term definitions |
| Metrics/KPIs | Calculated measure definitions |
| Validation Rules | Data quality tests with severity levels |
| Data Lineage | Source-to-output transformation diagram |
| Documents Panel | Uploaded documents, extraction status, context activation controls |

---

## 14. Document Management

### Upload and Registration

Documents enter via initial data product creation, chat attachment (drag-and-drop or file picker), or the Documents panel. Supported formats: PDF, DOCX (max 20MB per file).

All uploads register source metadata (filename, size, type, upload timestamp), version identity (new upload of same filename creates new version), extraction status (pending, processing, completed, failed), and quality diagnostics (extraction confidence, completeness).

### Extraction Pipeline

1. **Parse:** Cortex PARSE_DOCUMENT extracts raw text with layout awareness
2. **Chunk:** Text split into semantic chunks (paragraph-level, with overlap)
3. **Entity Detection:** Named entities extracted (people, organizations, products, dates, amounts)
4. **Fact Normalization:** Key facts converted to subject-predicate-object triples
5. **Semantic Linking:** Facts linked to knowledge graph entities
6. **Embedding:** Chunks embedded for retrieval (as one signal, not the only signal)
7. **Graph Update:** All extracted entities, facts, and links persisted as Neo4j nodes/edges

### Context Activation

Not every uploaded document must be active for every stage. ekaiX supports stage-aware context activation:

- **Active evidence** — document is used in current stage's analysis
- **Candidate evidence** — document is available but not yet activated
- **Excluded evidence** — document is explicitly excluded from current analysis

The user controls activation via the Documents panel. The system shows impact hints when activation changes.

### Change Impact

When documents are added, updated, or deleted: impacted artifacts are marked as stale (not deleted), targeted rerun options are shown by stage, stale evidence is flagged but previous answers remain available, full pipeline resets are avoided — only affected graph paths are revalidated.

---

## 15. Trust UX

### Per-Answer Display

Every assistant answer includes: source mode badge (colored indicator for Structured, Document, Hybrid), confidence indicator, exactness state, evidence summary, expandable evidence drawer, and recovery actions when answer is not ready.

### Abstain Messaging

When ekaiX abstains, the message follows a two-line pattern:

1. **Why** — specific about what evidence is missing or conflicting
2. **What to do** — concrete actions the user can take

No vague language like "I'm not sure" or "This might be..." — always specific about what is missing and what the user can do.

### Long-Running Operations

Human-readable progress narrative (not generic spinners). Optional technical details panel (expandable). No "still processing" dead-end. Explicit failure state plus next action if blocked.

---

# Part IV: Technical Architecture

## 16. System Architecture

### Two-Plane Model

ekaiX separates into a control plane and a data/serving plane:

**Control Plane** — orchestration, state, and workflow governance:
- Frontend (Next.js): business chat and mission-control UX
- Backend API (Fastify): contracts, auth context, orchestration endpoints, SSE relay
- AI Service (FastAPI): supervisor, planning, tool routing, answer synthesis, subagent orchestration
- Redis: in-flight orchestration state and streaming coordination
- PostgreSQL: persistent product/workflow/evidence metadata

**Data/Serving Plane** — retrieval, semantics, and answer execution:
- Snowflake warehouse assets (structured source of truth)
- Snowflake Cortex services (LLM, PARSE_DOCUMENT, Cortex Search, Cortex Agents)
- MinIO object storage (document uploads, extraction outputs, artifact files)
- Neo4j knowledge graph (cross-source semantic grounding, ERD, entity resolution)

### Service Topology

| Service | Technology | Port | Role |
|---------|-----------|------|------|
| Frontend | Next.js 15 + React 19 | 3000 | Business chat UI, artifact panels, theme system |
| Backend | Fastify 5 + TypeScript | 8000 | API gateway, auth, SSE relay, database access |
| AI Service | FastAPI + LangGraph | 8001 | Agent orchestrator, subagents, tools, LLM routing |
| PostgreSQL | PostgreSQL 18 | 5432 | App state, workspaces, artifacts, audit, checkpoints |
| Neo4j | Neo4j 2025 | 7474/7687 | Knowledge graph, ERD, entity resolution |
| Redis | Redis 8 | 6379 | Session state, cache, streaming coordination |
| MinIO | MinIO | 9000/9001 | Artifact storage, document storage |
| Snowflake | Snowflake (external) | N/A | Data warehouse, Cortex AI, agent serving |

### Request Flow

Browser sends request to Frontend (Next.js :3000). Frontend calls Backend API (Fastify :8000). Backend authenticates via SPCS header, applies RLS, and either handles directly (database queries, CRUD) or proxies to AI Service (FastAPI :8001) for agent operations. AI Service runs LangGraph orchestrator with subagents and tools. Tools interact with Snowflake, Neo4j, PostgreSQL, Redis, and MinIO. Responses stream back via SSE through Backend to Frontend.

---

## 17. Container Stack (SPCS)

In production, all services deploy as containers in Snowflake's SPCS (Snowpark Container Services). Each service has a Dockerfile and spec.yaml defining resource limits, health checks, and network policies.

### Container Definitions

| Container | Base Image | Resources | Health Check |
|-----------|-----------|-----------|-------------|
| frontend | node:24-alpine | 1 CPU, 1GB RAM | GET / |
| backend | node:24-alpine | 2 CPU, 2GB RAM | GET /health |
| ai-service | python:3.11-slim | 4 CPU, 8GB RAM | GET /health |
| postgresql | postgres:18 | 2 CPU, 4GB RAM | pg_isready |
| neo4j | neo4j:2025 | 2 CPU, 4GB RAM | HTTP :7474 |
| redis | redis:8-alpine | 1 CPU, 1GB RAM | redis-cli ping |
| minio | minio/minio | 1 CPU, 2GB RAM | mc ready |

### Network Architecture

All containers share a private SPCS network. Only the frontend container exposes a public endpoint via SPCS ingress. Backend and AI Service communicate internally. Data services (PostgreSQL, Neo4j, Redis, MinIO) are only accessible within the SPCS network.

---

## 18. Network and Security Architecture

### RCR (Restricted Caller's Rights)

All Snowflake queries execute with EXECUTE AS CALLER semantics. The user's Snowflake role determines what data they can access. This is non-negotiable — ekaiX never elevates privileges.

### SPCS Isolation

Each customer installation runs in their own Snowflake account. Data never leaves the customer's Snowflake environment. LLM calls to external providers send only metadata and questions, never raw data. Cortex AI calls stay within Snowflake.

### Tenant Boundaries

PostgreSQL RLS policies enforce workspace isolation at the database level. Every query sets the current user context before execution. Neo4j queries filter by data_product_id. Redis keys are scoped by session and user. MinIO paths are scoped by data_product_id.

---

# Part V: Component Specifications

## 19. Frontend (Next.js)

### Technology Stack

| Package | Version | Purpose |
|---------|---------|---------|
| Next.js | 15 | App Router, SSR, API routes |
| React | 19 | UI rendering |
| MUI (Material UI) | 7 | Component library (exclusive — no other UI libs) |
| @emotion/react | 11 | CSS-in-JS for MUI theming |
| Zustand | 5 | State management (scoped stores per data product) |
| @tanstack/react-query | 5 | Data fetching, caching, synchronization |
| @xyflow/react | 12 | Interactive ERD graph visualization |
| react-syntax-highlighter | 15 | YAML/code syntax highlighting |
| recharts | 2 | Charts for data quality metrics |

### Pages and Routes

| Route | Page | Description |
|-------|------|-------------|
| / | Redirect | Redirects to /data-products |
| /data-products | Dashboard | Data product listing with search, filter, sort |
| /data-products/[id] | Chat Workspace | Main workspace: chat + artifacts + phase stepper |
| /llm-configuration | Settings | LLM provider and model configuration |
| /user-management | Admin | User management |

### State Management Pattern

Each data product page mounts a fresh ChatStoreProvider keyed by data product ID. This ensures isolated state per data product with no accidental state bleed between products. Store is destroyed on navigation.

Key store state: messages (conversation history), isStreaming (agent active flag), currentPhase (workflow stage), sessionId (SSE session), artifacts (generated artifacts), activePanel (current artifact view), dataTier (gold/silver/bronze), latestAnswerContract (trust metadata), reasoningLog (rolling reasoning history).

### SSE Streaming

The frontend connects to the backend's SSE endpoint after sending a message. It uses a ReadableStream with TextDecoder to parse events line-by-line. Retry logic uses exponential backoff with jitter (up to 5 retries, 1s base delay). Network errors and 5xx trigger retry. 4xx errors (except 408/429) do not retry. Page Visibility API pauses reconnection when the tab is backgrounded.

### Theme System

MUI's createTheme API defines dark and light palettes with ekai brand colors. The single accent color is gold (#D4A843). Dark theme: background #1A1A1E, surface #252528, text #F5F5F5. Light theme derives the inverse. No secondary accent colors — gold only.

---

## 20. Backend API (Fastify)

### Technology Stack

| Package | Version | Purpose |
|---------|---------|---------|
| Fastify | 5 | HTTP framework |
| @fastify/cors | latest | CORS middleware |
| @fastify/rate-limit | latest | Rate limiting (100 req/min) |
| @fastify/multipart | latest | File upload handling |
| pg | 8 | PostgreSQL client |
| redis | 5 | Redis async client |
| neo4j-driver | 6 | Neo4j graph database |
| minio | 8 | MinIO object storage |
| snowflake-sdk | 2 | Snowflake queries |
| zod | 3 | Runtime schema validation |
| pino | 9 | Structured JSON logging |

### Route Groups

| Group | Prefix | Endpoints |
|-------|--------|-----------|
| Health | /health | GET / (liveness probe) |
| Auth | /auth | GET /current-user |
| Databases | /databases | GET /, GET /:db/schemas, GET /:db/schemas/:schema/tables, POST /:db/reference |
| Data Products | /data-products | GET /, POST /, GET /:id, PUT /:id, DELETE /:id, POST /:id/share, GET /:id/health-check, POST /:id/publish |
| Agent | /agent | POST /message, GET /stream/:sessionId, POST /retry, POST /interrupt/:sessionId, GET /history/:sessionId |
| Artifacts | /artifacts | GET /:type/:dataProductId, GET /:type/:dataProductId/export |
| Documents | /documents | GET /:dataProductId, POST /upload, DELETE /:id, POST /:id/extract, GET /context/:dataProductId/current, POST /context/:dataProductId/apply, GET /semantic/:dataProductId/registry, GET /semantic/:dataProductId/chunks, GET /semantic/:dataProductId/facts, GET /semantic/:dataProductId/evidence |
| Settings | /settings | GET /llm-config, POST /llm-config |

### Middleware

**Auth middleware:** Extracts Sf-Context-Current-User header (SPCS) or X-Dev-User header (local dev). Attaches user object to request. Returns 401 if missing.

**RLS enforcement:** Every PostgreSQL query sets app.current_user before executing. Row-level security policies at the database level prevent cross-workspace data access.

**SSE relay:** The backend proxies SSE streams from the AI Service to the frontend. It opens a connection to the AI Service, reads events, and re-emits them to the browser client.

### API Contracts

Full request/response schemas for all endpoints are defined in `docs/plans/api-specification.yaml` (OpenAPI 3.1). The AI coding agent must implement endpoints matching those contracts exactly.

---

## 21. AI Service (FastAPI)

### Technology Stack

| Package | Version | Purpose |
|---------|---------|---------|
| FastAPI | 0.128 | Async HTTP framework |
| LangChain | 1.2 | Agent orchestration framework |
| LangGraph | 1.0 | Stateful graph execution |
| Deep Agents | 0.3 | Subagent framework (create_deep_agent) |
| langchain-mcp-adapters | 0.2 | MCP tool integration |
| LiteLLM | 1.81 | Multi-provider LLM router with failover |
| snowflake-connector-python | 4.2 | Snowflake SDK |
| snowflake-snowpark-python | 1.45 | Snowpark for RCR |
| asyncpg | 0.31 | PostgreSQL async client |
| neo4j | 6.1 | Neo4j driver |
| redis | 7.1 | Redis async client |
| minio | 7.2 | MinIO client |
| langfuse | 3.12 | Tracing and observability |
| Pydantic | 2.11 | Configuration and validation |

### Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| /health | GET | Service health check |
| /agent/message | POST | Accept user message, launch async agent processing |
| /agent/stream/{session_id} | GET | SSE stream of agent events |
| /agent/interrupt/{session_id} | POST | Cancel in-flight agent run |
| /agent/rollback/{session_id} | POST | Revert to previous checkpoint |
| /config/llm | GET/POST | LLM provider configuration |
| /documents/extract | POST | Trigger document extraction |

### Lifespan Management

On startup: initialize all backend connections (PostgreSQL, Neo4j, Redis, MinIO, Snowflake), restore LLM configuration overrides from PostgreSQL, initialize orchestrator and checkpointer. On shutdown: close all connections cleanly.

### Answer Contract Emission

After each conversation turn, the AI Service emits a HybridAnswerContract as the final SSE event. This contract contains source_mode, exactness_state, confidence_decision, trust_state, citations, and recovery_actions. The exactness guardrail prevents validated_exact without deterministic SQL or document_fact citations.

---

# Part VI: Agent System

## 22. Orchestrator

### Framework

The orchestrator uses LangGraph with Deep Agents (create_deep_agent factory). It manages six subagents, each with their own tools and system prompts. State is checkpointed to PostgreSQL via AsyncPostgresSaver after every tool call, enabling interrupt/resume/rollback.

### Delegation Logic

The orchestrator receives every user message with an injected supervisor context contract containing current_phase, data_product_id, already_published flag, data_tier (gold/silver/bronze), transformation_done, brd_exists, semantic_view_exists, validation_status, forced_subagent, forced_intent, and publish_approval. The orchestrator never shows this contract to the user.

The orchestrator delegates to subagents based on 30 transition rules (27 base + 3 document routing rules) that determine which subagent handles the current phase and user intent. Key routing decisions:

- Discovery phase: delegate to discovery-agent
- Non-gold data detected: delegate to transformation-agent before modeling
- Requirements phase: delegate to model-builder-agent (requirements capture mode)
- Modeling phase: delegate to modeling-agent (star schema) then model-builder-agent (semantic view)
- Validation phase: delegate to model-builder-agent (validation mode)
- Publishing phase: delegate to publishing-agent
- Explorer phase: delegate to explorer-agent
- End-to-end autopilot intent: chain agents automatically without pausing
- **Document routing (D1):** Document product with qualitative-only BRD → skip semantic view, auto-chain to publishing-agent with document-search-only mode
- **Document routing (D2):** Document product with quantitative BRD → delegate to model-builder in document extraction mode (AI_EXTRACT creates real tables, then semantic view generation)
- **Document routing (D3):** Hybrid product → follow standard structured path; publishing automatically includes Cortex Search Service alongside semantic view

### State Management

The orchestrator copies full conversation history into task() descriptions when delegating to subagents. This ensures subagents have complete context. Feature flags (feature_hybrid_planner, feature_exactness_guardrail, feature_trust_ux_contract) control progressive rollout of hybrid intelligence features.

---

## 23. Discovery Agent

### Purpose

Profile selected sources and build grounded context for subsequent stages. This agent runs a deterministic pipeline first (profiling, classification, quality scoring) then presents results conversationally.

### Structured Discovery Workflow

1. Query INFORMATION_SCHEMA for all tables and columns in selected schemas
2. Profile each table: row count, column statistics (distinct count, null percentage, data types, value distributions)
3. Detect primary keys: columns with >98% uniqueness and 0% nulls
4. Infer foreign keys: column name similarity, data type compatibility, value overlap analysis
5. Classify tables as FACT or DIMENSION based on column patterns
6. Compute data quality score with deductions for duplicates, orphaned FKs, missing descriptions, high null rates
7. Build ERD in Neo4j (Database, Schema, Table, Column nodes with HAS_SCHEMA, HAS_TABLE, HAS_COLUMN, FK_REFERENCES edges)
8. Generate Data Quality Report artifact
9. Generate Data Description artifact (internal, used by downstream agents)

### Document Discovery Workflow

1. Run Cortex PARSE_DOCUMENT on each uploaded file
2. Assess extraction quality (completeness, confidence)
3. Detect named entities
4. Extract candidate facts as subject-predicate-object triples
5. Assess document quality and coverage
6. Build document nodes in Neo4j (Document, Chunk, Entity, Fact nodes)

### Communication Pattern (CRITICAL)

The Discovery Agent MUST follow this four-step conversational pattern. It is NOT a silent batch job that dumps results — it engages the user conversationally between analysis steps to build a shared understanding of the business domain.

**[Analysis]** State what was observed in the data (column patterns, table structures, statistical findings) — translated into business language.

**[Recognition]** Identify the business domain pattern this represents (retail transactions, B2B orders, financial reporting, supply chain logistics, etc.).

**[Question]** Ask a sharp, business-focused question that helps refine understanding — NOT generic "What is this table?" but specific: "This looks like a retail transaction system with separate pricing tiers — do your enterprise customers get different pricing than retail?"

**[Suggestion]** Propose an answer based on data evidence, inviting the user to confirm or correct: "Based on the presence of SHIPPERS and FREIGHT columns, I'm leaning toward B2B logistics."

### Example: Good Discovery Output

```
I've analyzed your 23 customer data tables. Here's what I found:

Your data looks healthy (90/100). I found a central Customer table connected
to invoices, orders, and segments — a clean star pattern that's great for
analytics.

I noticed you have orders, order details, and product tables with price
fields in multiple locations. This typically represents either a retail
transaction system or a B2B order management workflow.

Which of these better describes your business?
 • Retail/E-commerce (customer-facing orders)
 • B2B Sales (orders between business entities)
 • Internal procurement (company purchasing)

Based on the presence of shipping and freight information, I'm leaning
toward B2B logistics.
```

### Example: Bad Discovery Output (NEVER Produce This)

```
Discovery phase complete for Data Product ID `019c2fe4-5e5a-754b-8449-a260a01eb124`.
Tables Profiled: 23 (All views)
Relationships Detected: 21 inferred relationships based on metadata.
Overall Data Health Score: 25/100
ERD Status: Built and persisted to Neo4j.
Critical Data Quality Warnings:
- Profiling Limitations: TABLESAMPLE returned 100% NULLs
- Numeric/Date as VARCHAR: 15 columns in COERCE_ tables
```

### Question Strategy Rules

1. **Sharp, business-focused questions** — NO generic "What is this table?" fluff. Extract semantic meaning from column names FIRST; use statistics only as fallback.
2. **DAMA CDM approach** — questions build the Conceptual Data Model (business entities and relationships), not technical schema documentation.
3. **Never ignore user instructions** — if the user says "this is a supply chain system," build on that context, never question it.
4. **Progressive disclosure** — don't dump all findings at once; reveal in digestible steps across 1-3 conversational rounds.
5. **Translate ALL technical findings** into business impact (e.g., "some date fields are stored as text, which may cause sorting issues in reports").
6. **Bias toward false positives** in relationship detection — show uncertain relationships. Users correct visible errors more easily than they find missing relationships.
7. **Always cite reasoning** — "Based on high uniqueness and the '_id' suffix, this appears to be a unique identifier for each customer."

### Tools Available

query_information_schema, profile_table, compute_quality_score, save_data_description, get_latest_data_description, build_erd_from_description, update_erd, query_erd_graph, save_quality_report

---

## 24. Transformation Agent

### Purpose

When source data is not gold-ready (bronze or silver tier), this agent creates native Snowflake transformations to produce curated tables suitable for semantic modeling.

### Workflow

1. Profile source tables to assess data quality issues (type mismatches, nulls, duplicates, inconsistent formats)
2. Generate transformation specifications: type casting, deduplication, null handling, column renaming
3. Create Snowflake VIEWs or Dynamic Tables in a curated schema (EKAIX.{dp_name}_CURATED)
4. Batch process transformations with Cortex AI fallback for complex DDL generation
5. Register transformed layer table FQNs for downstream agents

### Transformation Mechanisms

| Mechanism | When to Use |
|-----------|-------------|
| VIEWs | Simple type casting, column renaming, null handling |
| Dynamic Tables | Aggregations, joins, windowing functions |
| Snowpark stored procedures | Complex multi-step transformations |

The agent decides which mechanism to use based on transformation complexity. DBT is explicitly excluded — all transformations use native Snowflake features.

### Tools Available

profile_source_table, transform_tables_batch, register_transformed_layer, validate_ddl_with_explain, execute_ddl, safe_cast, generate_ddl_via_cortex

---

## 25. Modeling Agent

### Purpose

Design the gold layer star schema (facts and dimensions) following Kimball methodology. This agent creates Dynamic Tables that serve as the foundation for semantic views.

### Workflow

1. Analyze the Data Description artifact and ERD
2. Design fact tables (grain, measures, degenerate dimensions, foreign keys)
3. Design dimension tables (attributes, hierarchies, slowly changing dimensions)
4. Generate CREATE DYNAMIC TABLE statements
5. Validate grain (no duplicate rows at grain level) via EXPLAIN
6. Create gold tables in EKAIX.{dp_name}_MARTS schema
7. Generate documentation artifacts: data catalog, business glossary, metrics definitions, validation rules
8. Register gold layer tables for semantic view generation

### Grain Validation

Every fact table must pass grain validation: the combination of grain columns must produce zero duplicate rows. The agent runs a validation query before committing the table definition.

### Tools Available

create_gold_tables_batch, generate_gold_table_ddl, create_gold_table, validate_gold_grain, register_gold_layer, save_data_catalog, save_business_glossary, save_metrics_definitions, save_validation_rules, save_openlineage_artifact

---

## 26. Model Builder Agent (Requirements + Semantic View)

### Purpose

Combined agent that handles BRD capture through conversation, semantic view YAML generation, and validation. This is the most complex subagent with a 7-step lifecycle.

### Lifecycle

1. **Requirements capture:** Intelligent interview to understand business objective, metrics (with calculation logic), dimensions, filters, time ranges, business rules
2. **BRD generation:** Produce structured Business Requirements Document
3. **Pause for user review:** Present BRD summary and wait for approval
4. **Semantic view YAML generation:** Generate Snowflake-compatible YAML from BRD + ERD + gold layer tables
5. **YAML verification:** Cross-check YAML against BRD for completeness (all metrics covered, all dimensions included)
6. **Pause for user review:** Present YAML summary
7. **Validation:** Run SQL compilation, cardinality checks, sample queries, data quality checks

### Requirements Capture — Intelligent Questioning

The Model Builder uses a structured interview approach to extract business requirements. This is NOT a form — it is a conversation that adapts based on user responses and discovered data.

**Interview approach:**
1. **Understand the analytical objective** — What business question does the user want to answer? What decisions will this data support?
2. **Identify required metrics and dimensions** — Offer specific choices derived from discovered data (column names, data distributions, value ranges)
3. **Resolve ambiguities with data** — When multiple interpretations exist, present options with real data examples (record counts, sample values, distributions)
4. **Validate business logic** — Confirm calculation formulas, exclusion rules, time ranges, and aggregation levels with the user
5. **Build complete BRD** — Every metric has a calculation formula, every dimension has a business description, every filter has a justification

**Questioning rules:**
- **Show actual data examples** when explaining choices (e.g., "Your order status column has 5 values: PENDING, CONFIRMED, SHIPPED, DELIVERED, CANCELLED. Which statuses should be included in revenue calculations?")
- **Provide business context** with questions (e.g., "You have 1.2M orders over 3 years. Revenue ranges from $0.50 to $45,000. Do you want to exclude any ranges?")
- **Progressive disclosure** — don't overwhelm with all questions upfront; ask 2-3 per turn, building on previous answers
- **Max 15 conversation turns** — prevent infinite interview loops; escalate remaining ambiguities as assumptions in the BRD
- **Offer recommendations based on common patterns** (e.g., "Most retail analytics exclude cancelled orders from revenue. Shall I do the same?")
- **Never assume** — if multiple interpretations exist, present options with data. Never silently pick one.

### BRD Structure

The BRD captures: executive summary, business context, detailed requirements (each with description, priority, acceptance criteria), metric definitions (name, calculation formula, dimensions, filters), data quality requirements, and constraints.

### Semantic View YAML

The generated YAML follows Snowflake's semantic view specification with tables (fully qualified names), measures (name, expression, type, description), dimensions (name, expression, type, description), time_dimensions (name, expression, type, granularity), joins (table pairs, conditions, types), and filters (name, expression, description).

### Tools Available

save_brd, get_latest_brd, verify_brd_completeness, save_semantic_view, get_latest_semantic_view, create_semantic_view, validate_semantic_view_yaml, verify_yaml_against_brd, validate_sql, update_validation_status

---

## 27. Publishing Agent

### Purpose

Deploy semantic views, Cortex Search Services, and Cortex Agents to Snowflake Intelligence. Supports three publishing modes based on data product source type.

### Publishing Modes

| Mode | When | Creates |
|------|------|---------|
| Structured | Data product has warehouse tables only | Semantic view + Cortex Agent (Analyst tool only) |
| Document-only | Data product has documents only, qualitative BRD | Cortex Search Service + Cortex Agent (DocumentSearch tool only) |
| Document + extraction | Data product has documents only, quantitative BRD | Cortex Search Service + extracted tables + semantic view + Cortex Agent (both tools) |
| Hybrid | Data product has both tables and documents | Semantic view + Cortex Search Service + Cortex Agent (both tools) |

### Workflow

1. **Summary message:** Present what will be published based on mode — semantic model stats (tables, facts, dimensions, metrics) for structured, document corpus summary for document-only, or both for hybrid. Include data quality disclaimer.
2. **User approval:** Wait for explicit user approval before deploying.
3. **Deploy (structured):** Create semantic view → Create Cortex Agent with Analyst tool → Grant access.
4. **Deploy (document-only):** Create Cortex Search Service over DOC_CHUNKS → Create Cortex Agent with DocumentSearch tool only (no semantic_view_fqn) → Grant access.
5. **Deploy (hybrid):** Create semantic view → Create Cortex Search Service → Create Cortex Agent with both tools → Grant access.
6. **Artifact packaging:** Generate downloadable ZIP with all artifacts.

### Cortex Agent Configuration

The `create_cortex_agent` tool accepts an optional `semantic_view_fqn`. When omitted, the agent is created with only the DocumentSearch tool. When documents are present, the agent auto-detects the Cortex Search Service in `EKAIX.{dp_name}_DOCS.EKAIX_DOCUMENT_SEARCH`. The agent specification YAML dynamically includes only the tools that have backing resources.

### Tools Available

create_semantic_view, create_cortex_agent, create_document_search_service, grant_agent_access, upload_artifact, log_agent_action

---

## 28. Explorer Agent

### Purpose

Handle ad-hoc business questions after publishing (or during any phase). Routes questions to the appropriate intelligence lane.

### Routing Logic

For structured questions (metrics, aggregations, specific values): execute SQL via Cortex Agent or semantic view. For document questions (policies, definitions, context): retrieve from document facts and chunks. For hybrid questions: execute both lanes in parallel, validate evidence from both, synthesize with trust contract.

### Tools Available

query_cortex_agent, execute_rcr_query, query_document_facts, search_document_chunks, query_erd_graph, get_latest_brd, get_latest_semantic_view

---

## 29. Agent Communication Guidelines

### Mandatory Rules

The ICP (Ideal Customer Profile) is a business analyst, NOT a data engineer. Every message every agent produces must be written for someone who understands their business domain deeply but does NOT know SQL, database internals, or data engineering terminology.

### Forbidden in Agent Output

The following MUST NEVER appear in agent chat messages:

| Category | Forbidden Examples | Business Replacement |
|----------|--------------------|---------------------|
| Internal identifiers | UUIDs, data product IDs | (omit entirely) |
| Database technologies | Neo4j, Redis, MinIO, PostgreSQL | (omit entirely) |
| SQL keywords/syntax | VARCHAR, TABLESAMPLE, COUNT DISTINCT, INFORMATION_SCHEMA | (omit — describe in business terms) |
| Tool names | task(), save_brd(), execute_rcr_query(), upload_artifact | (omit entirely) |
| Implementation details | "persisted to Neo4j", "sampled using BERNOULLI" | (omit entirely) |
| Raw profiling metrics | null_pct, uniqueness_pct, distinct_count | "some values are missing", "each value is unique" |
| PK/FK jargon | Primary key, foreign key, FQN | "unique identifier", "linking column", "table reference" |
| Markdown formatting | Headers (#), bold (**), backticks (`) | Unicode bullets, plain text |
| Orchestration terms | subagent, auto-chain, checkpoint, supervisor, context contract | (omit entirely) |

### Required Communication Pattern

All agents follow the **Analysis → Recognition → Question → Suggestion** pattern during interactive phases. Agents do NOT silently run all tools and dump a summary. They engage the user conversationally between analysis steps to build shared understanding.

- **Analysis:** State observations in business language
- **Recognition:** Identify the business domain pattern
- **Question:** Ask a sharp, domain-specific question (never generic)
- **Suggestion:** Propose an answer based on evidence, invite correction

This pattern applies to: Discovery Agent (data understanding), Model Builder (requirements capture), and Explorer Agent (clarifying ambiguous questions).

### Output Sanitization

The supervisor guardrails service detects and removes: internal reasoning leakage (task(), subagent references, rule citations), markdown formatting, technical terms (UUID replaced with "internal ID", FQN replaced with "table reference"), and backticks from streaming tokens.

### Failure Recovery Messages

When operations fail, the system classifies failures into categories (timeout, access, validation, dependency, execution) and generates phase-specific recovery plans in business language. Failure messages never expose internal stack traces, tool names, or database errors — they describe what went wrong in business terms and what the user can do next.

---

## 30. Tool Catalog

### Tool Modules Overview

| Module | Tool Count | Primary Purpose |
|--------|-----------|-----------------|
| snowflake_tools | 11 | Snowflake queries, profiling, semantic views, Cortex Agents |
| neo4j_tools | 4 | ERD graph, relationship paths, entity classification |
| postgres_tools | 14 | App state, artifacts (BRD, YAML, descriptions), document facts/chunks |
| discovery_tools | 1 | ERD construction from data description |
| transformation_tools | 3 | Source profiling, batch transforms, curated layer registration |
| modeling_tools | 7 | Gold tables, grain validation, documentation artifacts |
| ddl | 6 | DDL validation, execution, type casting, Cortex AI DDL generation |
| naming | 5 | Schema naming conventions, EKAIX database/schema creation |
| minio_tools | 3 | Artifact upload, retrieval, listing |
| web_tools | 1 | Documentation fetching from Snowflake docs |

### Key Snowflake Tools

| Tool | Purpose |
|------|---------|
| execute_rcr_query | Read-only RCR queries (Restricted Caller's Rights) |
| query_information_schema | SHOW databases/schemas/tables/columns |
| profile_table | Column statistics (distinct, null_pct, data types, is_likely_pk) |
| validate_sql | SQL syntax validation via Snowflake |
| compute_quality_score | Data quality scoring from profile results |
| create_semantic_view | Deploy semantic model YAML to Snowflake |
| validate_semantic_view_yaml | Schema validation of YAML content |
| create_cortex_agent | Deploy Cortex Agent to Snowflake Intelligence |
| query_cortex_agent | Query a deployed Cortex Agent |
| grant_agent_access | GRANT USAGE to user role |
| verify_yaml_against_brd | Cross-check semantic model completeness |

### Key PostgreSQL Tools (App State and Documents)

| Tool | Purpose |
|------|---------|
| save_workspace_state / load_workspace_state | Persist/restore conversation state |
| save_data_description / get_latest_data_description | Data description artifact |
| save_brd / get_latest_brd / verify_brd_completeness | BRD lifecycle |
| save_semantic_view / get_latest_semantic_view / update_validation_status | Semantic view lifecycle |
| save_quality_report | Data quality report artifact |
| query_document_facts | Retrieve deterministic exact-value facts from documents |
| search_document_chunks | Retrieve document snippets by similarity |

### Key Neo4j Tools

| Tool | Purpose |
|------|---------|
| query_erd_graph | Retrieve all nodes and edges for a data product |
| update_erd | Persist ERD changes (nodes, edges) |
| get_relationship_path | Find connection path between two tables |
| classify_entity | Mark table as FACT or DIMENSION |

---

# Part VII: Intelligence Infrastructure

## 31. Neo4j Knowledge Graph

### Graph Model — Nodes

| Node Label | Properties | Purpose |
|------------|-----------|---------|
| Database | name, created_at | Snowflake database |
| Schema | name, database | Schema within database |
| Table | fqn, name, schema, database, classification (FACT/DIMENSION), row_count | Source table |
| Column | name, table_fqn, data_type, is_pk, is_nullable, distinct_count, null_pct | Table column |
| Document | id, filename, extraction_status, confidence | Uploaded document |
| Chunk | id, document_id, text, position, embedding | Extracted text chunk |
| Entity | name, type (person/org/product/date/amount), source | Detected entity |
| Fact | id, subject, predicate, object, confidence, numeric_value | Normalized fact |
| Requirement | id, description, priority, data_product_id | Business requirement |
| Metric | name, calculation, dimensions, filters | Metric definition |
| SemanticView | id, yaml_hash, validation_status | Generated semantic definition |
| DataProduct | id, name, status | Data product root node |

### Graph Model — Relationships

| Relationship | From | To | Properties |
|-------------|------|-----|-----------|
| HAS_SCHEMA | Database | Schema | |
| HAS_TABLE | Schema | Table | |
| HAS_COLUMN | Table | Column | |
| FK_REFERENCES | Column | Column | confidence, cardinality |
| HAS_DOCUMENT | DataProduct | Document | |
| HAS_CHUNK | Document | Chunk | position |
| MENTIONS_ENTITY | Chunk | Entity | confidence |
| STATES_FACT | Chunk | Fact | confidence |
| LINKS_TO_COLUMN | Entity | Column | link_confidence |
| LINKS_TO_TABLE | Entity | Table | link_confidence |
| SATISFIES_REQUIREMENT | SemanticView | Requirement | |
| DEFINES_METRIC | SemanticView | Metric | |
| CITED_IN | Fact/Column | AnswerEvidence | citation_type |
| CONFLICTS_WITH | Fact | Fact | conflict_type, detected_at |
| VERSION_OF | Document | Document | version_number |
| BELONGS_TO | Table/Document | DataProduct | |

### Runtime Usage Rules

Before answer finalization, the system must:

1. Build a question subgraph for detected business entities and metrics
2. Validate that each material claim has an evidence path through the graph
3. Check for conflicting or stale nodes and edges
4. Downgrade confidence or abstain if graph support is insufficient

### Hallucination Guardrails via Graph

- No metric claim without a requirement-to-semantic-mapping path
- No document claim without a provenance path to source chunk/fact
- No hybrid claim without an explicit cross-source entity link
- No fallback to unsupported free-text "best guess"

---

## 32. Document Intelligence Pipeline

### Two-Tier Architecture

Document intelligence operates on two tiers, both grounded in a shared chunk table uploaded to Snowflake:

**Tier 1 — Cortex Search (always-on for document/hybrid products):** Qualitative intelligence. A Cortex Search Service indexes enriched document chunks and handles policy, context, definition, and explanatory questions. Created at publish time via `create_document_search_service`.

**Tier 2 — Structured Extraction (requirements-driven):** Quantitative intelligence. When the BRD contains metrics, KPIs, or numeric requirements, `AI_EXTRACT` pulls typed data from document chunks into real Snowflake tables. A semantic view is generated over these extracted tables, enabling Cortex Analyst text-to-SQL queries.

### End-to-End Flow

1. **Upload:** File stored in MinIO at documents/{data_product_id}/uploads/{filename}
2. **Parse:** Cortex AI_PARSE_DOCUMENT extracts text with layout awareness (LAYOUT mode)
3. **Quality Assessment:** Compute extraction confidence and completeness score
4. **Chunk (PostgreSQL):** Split into semantic chunks stored in `doc_chunks` table for local agent tools
5. **Chunk (Snowflake):** Upload enriched chunks to `EKAIX.{dp_name}_DOCS.DOC_CHUNKS` table using `SNOWFLAKE.CORTEX.SPLIT_TEXT_RECURSIVE_CHARACTER(text, 'markdown', 1500, 200)`. This is fire-and-forget from the backend upload handler — does not block the upload response.
6. **Entity Extraction:** Detect named entities using Cortex AI_EXTRACT (or LLM-based extraction)
7. **Fact Normalization:** Convert key statements to subject-predicate-object triples with confidence scores. Extract numeric values, dates, and currency amounts as typed fields.
8. **Semantic Linking:** Match extracted entities to existing knowledge graph nodes (warehouse table names, column values, previously extracted entities). Create LINKS_TO_COLUMN and LINKS_TO_TABLE edges with link_confidence scores.
9. **Graph Persistence:** Create Document, Chunk, Entity, Fact nodes and all relationship edges in Neo4j
10. **Registry Update:** Update doc_registry in PostgreSQL with version, checksums, extraction metadata

### Snowflake DOC_CHUNKS Table Schema

```sql
CREATE TABLE IF NOT EXISTS EKAIX.{dp_name}_DOCS.DOC_CHUNKS (
  chunk_id VARCHAR NOT NULL,
  document_id VARCHAR NOT NULL,
  filename VARCHAR,
  doc_kind VARCHAR,
  page_no INTEGER,
  section_path VARCHAR,
  chunk_seq INTEGER,
  chunk_text VARCHAR,
  uploaded_at TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);
```

### Cortex Search Service Creation

At publish time, `create_document_search_service` creates:

```sql
CREATE OR REPLACE CORTEX SEARCH SERVICE EKAIX.{dp_name}_DOCS.EKAIX_DOCUMENT_SEARCH
  ON chunk_text
  ATTRIBUTES document_id, filename, doc_kind, page_no
  WAREHOUSE = {warehouse}
  TARGET_LAG = '1 hour'
AS SELECT chunk_id, document_id, filename, doc_kind, page_no,
          section_path, chunk_seq, chunk_text
   FROM EKAIX.{dp_name}_DOCS.DOC_CHUNKS;
```

### Tier 2: Structured Extraction from Documents

When the BRD contains quantitative requirements, the model-builder agent:

1. Derives an extraction schema from the BRD (what metrics/dimensions to extract)
2. Calls `extract_structured_from_documents` with a JSON schema defining tables and extraction prompts
3. Each table definition maps column names to AI_EXTRACT prompts (e.g., `{"country": "What country?", "value": "What numeric value?"}`)
4. AI_EXTRACT runs against each document chunk, creating real Snowflake tables in `EKAIX.{dp_name}_MARTS`
5. A semantic view is generated over these extracted tables using the standard YAML generation flow
6. The published Cortex Agent binds both the semantic view (Analyst tool) and the search service (DocumentSearch tool)

### Fact Storage Structure

Facts are stored in both PostgreSQL (for fast SQL-based retrieval) and Neo4j (for graph traversal). Each fact has: subject (text), predicate (text), object (text), numeric_value (decimal, nullable), confidence (0.0-1.0), currency (ISO code, nullable), source_document_id, source_chunk_id, extraction_method.

### Retrieval Surfaces

| Surface | When Used | Returns |
|---------|-----------|---------|
| Cortex Search Service | Document policy/context questions (via published agent) | Relevant document chunks with search scores |
| Cortex Analyst | Quantitative questions over extracted tables (via published agent) | SQL-backed row-level results |
| query_document_facts | Exact-value questions (local agent fallback) | Deterministic facts with confidence scores |
| search_document_chunks | Context/policy questions (local agent fallback) | Relevant text chunks with similarity scores |
| Neo4j graph traversal | Entity resolution, relationship questions | Evidence paths through the knowledge graph |

---

## 33. Hybrid Question Planning

### Route Planning

When a question arrives in Explorer mode, the route planner determines the execution strategy:

| Question Type | Route | Example |
|--------------|-------|---------|
| Metric/aggregation over warehouse data | Structured lane | "What was Q3 revenue by segment?" |
| Policy/definition from documents | Document lane | "What is our return policy for enterprise customers?" |
| Metric with document context | Hybrid (both lanes) | "What was Q3 revenue, and does the pricing policy allow the discounts applied?" |
| Unknown/ambiguous | Both lanes, evaluate results | "Tell me about our Acme Corp relationship" |

### Dual-Lane Execution

For hybrid questions, both lanes execute in parallel:

**Structured lane:** Parse question for metrics/dimensions, generate SQL via semantic view or Cortex Agent, execute with RCR, collect row-level evidence.

**Document lane:** Resolve entities in question against knowledge graph, retrieve relevant facts and chunks, validate provenance paths, collect document evidence.

### Evidence Union

After both lanes complete, results are merged:

1. Collect all citations from both lanes
2. Check for conflicts (structured data says X, document says Y)
3. If no conflicts: merge evidence, set source_mode = hybrid
4. If conflicts detected: set trust_state = abstained_conflicting_evidence, include conflict_notes explaining the discrepancy
5. Apply exactness guardrail: if question requires exact value and only chunk-similarity evidence exists (no SQL or normalized facts), force abstain

### Conflict Resolution Rules

| Conflict Type | Resolution |
|--------------|------------|
| Structured and document agree | Merge, high confidence |
| Structured has data, document silent | Use structured, note document gap |
| Document has data, structured silent | Use document, note warehouse gap |
| Structured and document disagree on values | Abstain, show both values, suggest user investigation |
| Stale document (newer warehouse data) | Flag staleness, prefer structured, warn about document currency |

---

## 34. Supervisor Guardrails

### Workflow Transition Guards

The supervisor evaluates whether phase transitions are allowed based on prerequisites:

| Transition | Prerequisites |
|-----------|--------------|
| Discovery to Requirements | Data description exists AND (all tables gold OR transformation done) |
| Requirements to Modeling | BRD exists and is complete |
| Modeling to Validation | Semantic view YAML exists |
| Validation to Publishing | Validation status = valid |
| Any to Explorer | Data product exists |

### Intent Detection

The supervisor detects user intent from message content:

- **Requirements transition:** Keywords like "move to requirements", "proceed", "go ahead", "next step"
- **End-to-end autopilot:** Keywords like "end-to-end", "autopilot", "without pause", "do everything"
- **Analysis only:** Keywords like "just analyze", "only discovery", "no publish"

### Output Safety

The supervisor guardrails service performs:

- **Internal reasoning leak detection:** Scans for task(), subagent, auto-chain, rule references, supervisor context fragments
- **Text sanitization:** Removes markdown formatting, drops internal leak lines, replaces technical terms (UUID with "internal ID", FQN with "table reference")
- **Token sanitization:** Strips backticks and bold markers from streaming tokens
- **Failure classification:** Categorizes failures into timeout, access, validation, dependency, execution
- **Recovery message generation:** Produces phase-specific, business-language recovery plans

### Exactness Guardrail

When a question requires exact evidence (numbers, dates, specific names):

1. Check if citations include deterministic sources (SQL row-level results or normalized document facts)
2. If yes: allow validated_exact or estimated based on confidence
3. If no (only chunk similarity or free-text): force exactness_state = insufficient_evidence and trust_state = abstained_missing_evidence
4. This prevents false positives from semantic similarity that looks correct but lacks deterministic backing

---

# Part VIII: Data Models

## 35. PostgreSQL Schema

The complete schema is defined in `docs/plans/postgresql-schema.sql`. Key design decisions:

### Tables

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| workspaces | One per Snowflake user, RLS root | id (UUIDv7), snowflake_user, settings (JSONB) |
| data_products | Core entity, full agent state | id, workspace_id, name, database_reference, schemas[], status (enum), state (JSONB), health_score, published_agent_fqn |
| data_product_shares | Explicit sharing between users | data_product_id, shared_with_user, permission (view/edit) |
| business_requirements | Versioned BRD documents | data_product_id, version, brd_json (JSONB), is_complete |
| semantic_views | Generated YAML definitions | data_product_id, version, yaml_content, validation_status |
| artifacts | MinIO file references (versioned) | data_product_id, artifact_type (enum), version, minio_path |
| uploaded_documents | Document registry | data_product_id, filename, minio_path, extraction_status, extracted_content |
| data_quality_checks | Health check results | data_product_id, overall_score, check_results (JSONB), acknowledged |
| audit_logs | Partitioned by month | workspace_id, action_type, action_details (JSONB), user_name, created_at |

### PostgreSQL 18 Features Used

- UUIDv7 for timestamp-ordered primary keys (native in PG18)
- Virtual generated columns for JSONB field extraction
- Row-Level Security (RLS) for workspace isolation
- JSONB with GIN indexes for flexible state storage
- Range partitioning on audit_logs by timestamp

### RLS Policy Pattern

Every table has RLS enabled. Policies use `current_setting('app.current_user', true)` to filter rows. The backend middleware sets this per-request from the SPCS header. Users see only their own workspace data, plus data products explicitly shared with them.

### Document Semantic Tables (Migration Extensions)

Additional tables for hybrid intelligence:

| Table | Purpose |
|-------|---------|
| doc_registry | Document metadata with version tracking and checksums |
| doc_chunks | Extracted text chunks with extraction_confidence and ACL scope |
| doc_facts | Normalized facts (subject-predicate-object) with confidence, numeric_value |
| doc_fact_links | Cross-domain entity linking (target_domain, target_key, link_confidence) |
| qa_evidence | Answer contract and citation storage for trust UX |
| ops_alert_events | Operational signals (stalled runs, isolation violations) |

---

## 36. Neo4j Graph Schema

### Node Constraints

| Node Label | Unique Property |
|------------|----------------|
| Database | name |
| Schema | name + database |
| Table | fqn (fully qualified name) |
| Column | name + table_fqn |
| Document | id |
| Fact | id |
| DataProduct | id |

### Index Strategy

Property indexes on: Table.fqn, Column.name, Entity.name, Fact.subject, Document.id. Full-text indexes on: Entity.name, Fact.object (for search). Relationship indexes on: FK_REFERENCES.confidence, LINKS_TO_COLUMN.link_confidence.

---

## 37. Redis Key Patterns

| Key Pattern | TTL | Purpose |
|-------------|-----|---------|
| agent:session:{session_id} | 1 hour | Active session state |
| checkpoint:{session_id}:{checkpoint_id} | 24 hours | LangGraph checkpoints |
| cache:erd:{data_product_id} | 1 hour | Cached ERD graph data |
| cache:profile:{table_fqn} | 1 hour | Cached table profile |
| cache:llm_config | No TTL | Current LLM provider settings |
| stream:{session_id} | 5 minutes | SSE event queue |

---

## 38. MinIO Bucket Structure

### Artifacts Bucket

Path pattern: `artifacts/{data_product_id}/{type}/v{version}/{filename}`

| Type | Files |
|------|-------|
| erd | ERD JSON, DBML export |
| yaml | Semantic view YAML |
| brd | BRD Markdown |
| quality_report | Data quality report JSON |
| data_catalog | Catalog Markdown |
| business_glossary | Glossary Markdown |
| metrics | Metrics definitions Markdown |
| validation_rules | Rules JSON |
| lineage | OpenLineage JSON |
| export | ZIP package of all artifacts |

### Documents Bucket

Path pattern: `documents/{data_product_id}/uploads/{filename}` for originals and `documents/{data_product_id}/extracted/{document_id}/` for extraction outputs.

---

# Part IX: Snowflake Integration

## 39. Stored Procedures

All stored procedures use EXECUTE AS CALLER for RCR (Restricted Caller's Rights). The user's Snowflake role determines what data they can access.

### Discovery Procedures

| Procedure | Purpose |
|-----------|---------|
| SHOW DATABASES / SHOW SCHEMAS / SHOW TABLES | List accessible objects |
| INFORMATION_SCHEMA queries | Column metadata, constraints, data types |
| TABLESAMPLE queries | Statistical profiling (distinct counts, null rates, distributions) |

### Profiling Procedures

Profile operations use TABLESAMPLE for efficiency on large tables. The profiling computes: row count, column count, per-column distinct count, null percentage, min/max values, data type, is_likely_pk flag (uniqueness > 98% and null_pct = 0).

### Validation Procedures

| Procedure | Purpose |
|-----------|---------|
| EXPLAIN (without execution) | SQL compilation test |
| SELECT with LIMIT | Sample data verification |
| COUNT(DISTINCT grain_columns) vs COUNT(*) | Grain validation (no duplicates at fact grain) |
| NULL percentage checks | Flag unexpected nulls |

### Publishing Procedures

| Procedure | Purpose |
|-----------|---------|
| CREATE SEMANTIC VIEW | Deploy YAML definition to Snowflake |
| CREATE CORTEX AGENT | Create agent with semantic view binding |
| GRANT USAGE | Grant access to user's role |

---

## 40. Cortex AI Integration

### LLM Calls

ekaiX uses Snowflake Cortex for LLM operations when the provider is configured as snowflake-cortex. Cortex provides: COMPLETE function for text generation (using AI_COMPLETE in 2026), embedding generation for vector similarity, and summarization capabilities.

### PARSE_DOCUMENT

Used for document extraction. Input: raw PDF/DOCX binary. Output: structured text with layout information, page numbers, and confidence scores.

### Cortex Search

The Tier 1 document intelligence layer. For every document or hybrid data product, ekaiX creates a Cortex Search Service (`EKAIX.{dp_name}_DOCS.EKAIX_DOCUMENT_SEARCH`) over the `DOC_CHUNKS` table at publish time. The search service indexes `chunk_text` with attributes `document_id`, `filename`, `doc_kind`, `page_no`. TARGET_LAG is set to 1 hour for near-real-time indexing of new uploads. The published Cortex Agent binds to this service as its `DocumentSearch` tool.

### Cortex Agents

Published as the serving mechanism for business Q&A. A Cortex Agent supports three tool configurations:

| Configuration | Tools | When |
|--------------|-------|------|
| Structured only | cortex_analyst_text_to_sql (Analyst) | Structured data products |
| Document only | cortex_search (DocumentSearch) | Document products with qualitative BRD |
| Dual tool (hybrid) | Both Analyst + DocumentSearch | Hybrid products, or document products with quantitative BRD |

The `create_cortex_agent` tool dynamically builds the agent specification YAML with only the tools that have backing resources. `semantic_view_fqn` is optional — when omitted, the agent is document-search-only. The agent decides per-question which tool to invoke.

---

## 41. Semantic View YAML Specification

The semantic view YAML follows Snowflake's specification for Cortex Analyst compatibility.

### Required Elements

| Element | Description |
|---------|-------------|
| name | Unique name for the semantic view |
| description | Business-friendly description |
| tables | Array of table references with fully qualified names |
| measures | Calculated metrics (name, expression, type, description) |
| dimensions | Categorical attributes (name, expression, type, description) |
| time_dimensions | Date/time fields with granularity (day, week, month, quarter, year) |

### Optional Elements

| Element | Description |
|---------|-------------|
| joins | Table join definitions (table pairs, conditions, join types) |
| filters | Named filter expressions for common constraints |
| synonyms | Alternative names for measures/dimensions |
| sample_questions | Example questions the semantic view can answer |

### Validation Requirements

Every semantic view must pass: YAML syntax validation, column existence verification (all referenced columns exist in Snowflake), SQL compilation (EXPLAIN test), and sample query execution (at least one successful query).

---

## 42. Native Transformation

When source data is not gold-ready, ekaiX uses native Snowflake mechanisms:

### Dynamic Tables

For ongoing, auto-refreshing transformations. Created in EKAIX.{dp_name}_MARTS schema. Define target refresh schedule. Snowflake handles incremental refresh automatically.

### Snowpark Stored Procedures

For complex multi-step transformations. Python-based with EXECUTE AS CALLER. Used when transformation logic requires procedural code.

### Views

For simple transformations (type casting, column renaming, null handling). Created in EKAIX.{dp_name}_CURATED schema. No materialization — computed on read.

### Schema Naming Convention

| Schema | Purpose |
|--------|---------|
| EKAIX.{dp_name}_CURATED | Intermediate curated views (bronze/silver cleanup) |
| EKAIX.{dp_name}_MARTS | Gold layer Dynamic Tables (facts and dimensions) |

---

## 43. SPCS Deployment

### Compute Pools

Each ekaiX installation uses a single compute pool with auto-scaling. Minimum 1 node, maximum 3 nodes. Instance family: CPU_X64_M for general workloads.

### Service Specs

Each container has a spec.yaml defining: container image reference, resource limits (CPU, memory), health check endpoint and interval, environment variables (from SPCS secrets), network ports, and volume mounts (for persistent data).

### Image Registry

Container images are pushed to the Snowflake image registry within the application package. Images are tagged with semantic versions. Vulnerability scanning runs before push.

### Application Package Structure

The SPCS application package contains:

| File | Purpose |
|------|---------|
| manifest.yml | Package metadata, version, privileges |
| setup_script.sql | Creates compute pools, services, grants |
| spcs_services/{service}/Dockerfile | Container build definitions |
| spcs_services/{service}/spec.yaml | Service specifications |
| stored_procedures/*.py | RCR stored procedures |

---

# Part X: LLM Configuration

## 44. Multi-Provider Support

ekaiX supports multiple LLM providers, configurable at runtime via the UI:

| Provider | Models | Integration |
|----------|--------|------------|
| Snowflake Cortex | Llama, Mistral, others available in Cortex | Native AI_COMPLETE function |
| Google Vertex AI | Gemini Pro, Gemini Flash | langchain-google-vertexai, requires GCP credentials |
| Azure OpenAI | GPT-4o, GPT-4 Turbo | langchain-openai with Azure endpoint |
| Anthropic | Claude Opus 4, Claude Sonnet 4 | langchain-anthropic |
| OpenAI | GPT-4o, GPT-4 Turbo | langchain-openai |

### Provider Configuration

Provider selection and model configuration are stored in PostgreSQL (not environment variables). Configuration is UI-only — users select provider and model via the /llm-configuration page. Settings persist across service restarts and are restored on startup.

### Credential Handling

Provider credentials are set in environment variables (not UI):
- Vertex AI: GOOGLE_APPLICATION_CREDENTIALS pointing to service account JSON
- Azure: azure_openai_api_key, azure_openai_endpoint, azure_openai_deployment
- Anthropic: anthropic_api_key
- OpenAI: openai_api_key
- Cortex: No separate credentials (uses Snowflake connection)

---

## 45. LiteLLM Fallback Router

ekaiX uses LiteLLM for automatic failover between providers.

### Failover Logic

1. Primary provider configured via UI
2. If primary fails (timeout, rate limit, error): automatically retry with fallback provider
3. Retry with exponential backoff (max 3 retries per provider)
4. If all providers fail: return error to user with recovery action

### Configuration

The llm_fallback_config setting stores a JSON array of provider configurations in priority order. Each entry specifies: provider name, model, timeout, max_retries, and credentials reference.

### Azure OpenAI Prompt Sanitization

When using Azure OpenAI, system prompts are automatically sanitized to soften directive language. "CRITICAL" becomes "Important", "MUST" becomes "should", etc. This prevents Azure content filtering from blocking legitimate prompts.

---

# Part XI: Security and Governance

## 46. Authentication

### SPCS Header Authentication

In production (SPCS deployment), user identity comes from the Sf-Context-Current-User header injected by Snowflake. This header cannot be spoofed in production — Snowflake guarantees its authenticity.

### Local Development Authentication

In local development, the backend accepts the X-Dev-User header as a fallback. If neither header is present on localhost, a default dev user is used.

### No Separate Auth System

ekaiX does not implement its own authentication. There are no login pages, no JWT tokens, no session cookies. User identity is always derived from Snowflake.

---

## 47. Authorization

### RCR (Restricted Caller's Rights)

All Snowflake data access uses the caller's permissions. If a user's Snowflake role cannot access a database, ekaiX cannot show them that database's data. This is enforced at the Snowflake level, not the application level.

### Workspace Isolation

PostgreSQL RLS policies ensure users only see their own workspace data. The backend sets app.current_user on every query. Shared data products are visible based on explicit share records.

### Sharing Permissions

Data products can be shared with other Snowflake users with either view or edit permission. Only the owner can share, revoke shares, or delete a data product.

---

## 48. Audit Trail

Every user and agent action is logged to the audit_logs table:

| Field | Description |
|-------|-------------|
| action_type | create_product, update_product, delete_product, publish, share, agent_message, discovery_start, etc. |
| action_details | JSONB with action-specific context |
| user_name | Snowflake username who triggered the action |
| workspace_id | Workspace scope |
| data_product_id | Related data product (nullable) |
| created_at | Timestamp |

Audit logs are partitioned by month for scalability. Retention policies should be configurable per installation.

---

## 49. Data Privacy

### Data Residency

No customer data leaves the Snowflake account. All processing happens within SPCS containers. External LLM providers receive only questions and metadata — never raw data rows. Cortex AI calls stay entirely within Snowflake.

### Metadata-Only LLM Context

When calling external LLM providers, the system sends: table names, column names, data types, statistical summaries, user questions, and generated SQL. It does NOT send: raw data values, document content, PII, or credentials.

### Document ACLs

Document access is controlled at the data product level via workspace isolation. Within a data product, context activation controls which documents inform which stages.

---

# Part XII: Reliability and Observability

## 50. No-Limbo State Machine

Every operation emits an explicit lifecycle state:

| State | Description | User Experience |
|-------|-------------|----------------|
| running | Operation actively executing | Progress narrative with current step |
| waiting | Waiting for user input or approval | Clear prompt for what's needed |
| blocked | Prerequisites not met | What's blocking + what to do |
| failed_recoverable | Transient error, user can retry | Error explanation + retry button |
| failed_admin | System error, needs admin | Error explanation + escalation path |
| recovered | Recovered from previous failure | Confirmation of recovery |
| completed | Operation finished successfully | Results and next steps |

No operation can be in an undefined state. No "still processing" dead-ends. Every failure includes a concrete next action.

---

## 51. Error Handling

### Graceful Degradation

If a service is unavailable:

| Service Down | Impact | Degradation |
|-------------|--------|-------------|
| Neo4j | No ERD, no graph grounding | Continue with SQL-only mode, warn user |
| Redis | No caching, no session state | Fall back to PostgreSQL for state |
| MinIO | No artifact storage | Queue artifacts, retry when available |
| Snowflake | No data access | Block with clear error message |
| AI Service | No agent operations | Block agent, allow CRUD operations |

### Retry Logic

Tool calls use exponential backoff with max 3 retries. Snowflake queries retry on transient errors (connection timeout, warehouse scaling). LLM calls retry via LiteLLM failover router.

---

## 52. SSE Resilience

### Client-Side

Exponential backoff with jitter (up to 5 retries, 1s base delay, 0-30% random jitter). Retry on network errors and 5xx. No retry on 4xx (except 408/429). Page Visibility API pauses reconnection when tab is backgrounded. Auto-reconnect when tab returns to foreground.

### Server-Side

Backend SSE relay uses AbortController for cleanup. Timeout on idle connections (30s). Stream ends with explicit done event. Error events include categorized error codes for client-side handling.

### Error Categorization

| Category | Retry | User Action |
|----------|-------|-------------|
| network_error | Yes | Automatic retry |
| server_error (5xx) | Yes | Automatic retry |
| timeout (408) | Yes | Automatic retry |
| rate_limited (429) | Yes (with backoff) | Wait and retry |
| unauthorized (401) | No | Re-authenticate |
| forbidden (403) | No | Check permissions |
| not_found (404) | No | Check session |
| client_error (other 4xx) | No | Fix request |

---

## 53. Observability

### Structured Logging

All services use structured JSON logging with consistent fields: timestamp, level, service, trace_id, user, data_product_id, message, and context-specific fields.

### Langfuse Tracing

The AI Service integrates with Langfuse for LLM tracing. Every agent invocation creates a trace with: input, output, model used, token counts, latency, tool calls, and error information. Traces are linked to data products and sessions.

### Metrics

Key metrics to track: agent response latency (p50, p95, p99), tool call success rate, LLM token usage per session, SSE connection count and error rate, discovery pipeline duration, validation pass/fail rate, publish success rate.

### Operational Alerts

The ops_alert_events table captures operational signals: stalled agent runs (no progress in 60s), isolation violations (cross-workspace access attempts), high error rates, and service degradation.

---

# Part XIII: Non-Functional Requirements

## 54. Performance

| Metric | Target |
|--------|--------|
| Chat message to first token | Under 2 seconds |
| Discovery pipeline (20 tables) | Under 5 minutes |
| Discovery pipeline (100 tables) | Under 15 minutes |
| Semantic view generation | Under 30 seconds |
| Validation suite | Under 2 minutes |
| Publishing to Snowflake | Under 1 minute |
| Explorer query (structured) | Under 10 seconds |
| Explorer query (document) | Under 15 seconds |
| Explorer query (hybrid) | Under 20 seconds |
| Full workflow (source to published) | Under 35 minutes |

---

## 55. Scalability

| Dimension | Limit |
|-----------|-------|
| Tables per data product | Up to 200 |
| Columns per table | Up to 500 |
| Documents per data product | Up to 100 |
| Document size | 20MB each |
| Concurrent conversations per installation | Up to 10 |
| Data products per workspace | Up to 50 |
| Workspaces per installation | Up to 100 |

---

## 56. Reliability

| Requirement | Target |
|-------------|--------|
| Service uptime | 99.5% |
| Agent state recovery after crash | Within 30 seconds (from PostgreSQL checkpoint) |
| No data loss on service restart | All state in PostgreSQL, all artifacts in MinIO |
| Graceful degradation | Documented fallback for every service dependency |

---

## 57. Usability

| Requirement | Detail |
|-------------|--------|
| Accessibility | WCAG 2.1 AA compliance |
| Responsive design | Desktop-first, minimum 1280px viewport |
| Keyboard navigation | Tab through all interactive elements |
| Theme support | Dark and light modes with gold accent |
| Language | Business-first, no technical jargon in primary UI |

---

# Part XIV: Development Environment

## 58. Prerequisites

| Software | Version | Purpose |
|----------|---------|---------|
| Node.js | 24 LTS | Frontend and backend runtime |
| Python | 3.11 | AI service runtime |
| Docker | 27+ | Data services containers |
| Docker Compose | v5+ | Data services orchestration |
| PM2 | Latest | Process management |
| Git | Latest | Version control |

### Snowflake Account

A real Snowflake account is required for development. Credentials are stored in sf.txt (not committed) and loaded via environment variables. Account: lqb12348.us-east-1, User: EKAIBA, Warehouse: COMPUTE_WH, Role: ACCOUNTADMIN.

---

## 59. Local Architecture

In local development, the three app services (frontend, backend, AI service) run natively while data services run via Docker Compose.

### Service Startup Order

1. Start data services: `docker compose up -d` (PostgreSQL, Neo4j, Redis, MinIO)
2. Wait for health checks to pass
3. Start app services: `pm2 start ecosystem.config.js` (frontend, backend, AI service)

### Data Services (Docker Compose)

| Service | Image | Ports | Volume |
|---------|-------|-------|--------|
| PostgreSQL 18 | postgres:18 | 5432 | pgdata |
| Neo4j 2025 | neo4j:2025 (with APOC plugins) | 7474, 7687 | neo4jdata |
| Redis 8 | redis:8-alpine | 6379 | redisdata |
| MinIO | minio/minio | 9000, 9001 | miniodata |

---

## 60. PM2 Process Management

All app services are managed via PM2 using ecosystem.config.js at the project root.

### Process Definitions

| Process | Script | Working Directory |
|---------|--------|-------------------|
| frontend | npm run dev | ./frontend |
| backend | npm run dev | ./backend |
| ai-service | venv/bin/python -m uvicorn main:app --reload --port 8001 | ./ai-service |

### Key Commands

- `pm2 start ecosystem.config.js` — Start all services
- `pm2 stop all` — Stop all services
- `pm2 restart all` — Restart all services
- `pm2 logs` — Tail all logs
- `pm2 logs backend` — Tail specific service
- `pm2 status` — Show service status

---

## 61. Environment Variables

A single .env file in the repository root contains ALL configuration for every service. Never create separate .env files in subdirectories.

### Variable Groups

**Service Ports:** FRONTEND_PORT (3000), BACKEND_PORT (8000), AI_SERVICE_PORT (8001)

**Environment:** NODE_ENV (development/production/test)

**CORS:** ALLOWED_CORS_ORIGINS (comma-separated origins)

**Frontend URLs:** NEXT_PUBLIC_API_URL (backend URL exposed to browser), NEXT_PUBLIC_WS_URL (WebSocket URL)

**Internal URLs:** AI_SERVICE_URL (backend-to-AI-service communication)

**PostgreSQL:** DATABASE_URL (full connection string), POSTGRES_PASSWORD

**Neo4j:** NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

**Redis:** REDIS_URL (full connection string with password), REDIS_PASSWORD

**MinIO:** MINIO_ENDPOINT, MINIO_PORT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_USE_SSL

**Snowflake:** SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD, SNOWFLAKE_WAREHOUSE, SNOWFLAKE_DATABASE, SNOWFLAKE_ROLE

**LLM (runtime via UI, not env):** llm_provider, llm_fallback_config — stored in PostgreSQL

**Tracing:** LANGFUSE_SECRET_KEY, LANGFUSE_PUBLIC_KEY, LANGFUSE_BASE_URL, LANGCHAIN_TRACING_V2, LANGCHAIN_API_KEY

**Tuning:** PG_IDLE_TIMEOUT_MS, NEXT_PUBLIC_SSE_MAX_RETRIES, NEXT_PUBLIC_SSE_BASE_DELAY_MS, LLM_MAX_TOKENS, LLM_TEMPERATURE, AGENT_RECURSION_LIMIT, SESSION_TTL_SECONDS, CACHE_TTL_SECONDS

**Feature Flags:** feature_hybrid_planner, feature_exactness_guardrail, feature_trust_ux_contract

---

## 62. Development Workflow

### Initial Setup

1. Clone repository
2. Copy .env.example to .env and fill in credentials
3. Start data services with docker compose
4. Run PostgreSQL schema: `psql $DATABASE_URL < docs/plans/postgresql-schema.sql`
5. Install frontend dependencies: `cd frontend && npm install`
6. Install backend dependencies: `cd backend && npm install`
7. Create AI service venv and install: `cd ai-service && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`
8. Start all services: `pm2 start ecosystem.config.js`

### Daily Development

Start data services (if not running), start app services via PM2, make changes (hot reload active for all services), run tests in the relevant service directory.

---

# Part XV: Deployment and Packaging

## 63. SPCS Service Dockerfiles

Each service has a multi-stage Dockerfile:

**Stage 1 (builder):** Install dependencies, compile TypeScript (for Node services) or install Python packages (for AI service).

**Stage 2 (runtime):** Copy built artifacts, set health check, configure non-root user, expose port.

### Requirements

- Multi-stage builds for minimal image size
- Health check endpoints configured in Dockerfile
- Non-root user for security
- Resource limits defined in spec.yaml (not Dockerfile)
- No secrets in images — all from SPCS secrets at runtime

---

## 64. Application Package

### manifest.yml

Defines: application name, version, required privileges (CREATE DATABASE, EXECUTE TASK, CREATE COMPUTE POOL), references to consumer account databases, and setup script path.

### setup_script.sql

Creates: EKAIX database, compute pool, all SPCS services, grants to consumer roles, stored procedures with EXECUTE AS CALLER, and initial configuration.

### Consumer Account Testing

Before release, the application package must be tested in a separate Snowflake account (not the development account) to verify: installation succeeds, RCR enforcement works (user cannot access data outside their role), workspace isolation holds, and Cortex Agent publishing succeeds.

---

# Part XVI: Testing Strategy

## 65. Unit Tests

### Frontend (Vitest)

Test: component rendering, store actions, SSE event parsing, answer contract normalization, trust badge rendering, artifact hydration logic.

### Backend (Vitest)

Test: route handlers, schema validation (Zod), auth middleware, RLS query building, SSE relay logic, document extraction service.

### AI Service (pytest)

Test: supervisor guardrails (transition validation, output sanitization, leak detection), tool functions (with mocked Snowflake/Neo4j connections), hybrid evaluation scoring, answer contract construction, configuration loading and override.

---

## 66. Integration Tests

Test against real services (Snowflake, PostgreSQL, Neo4j, Redis, MinIO):

- Snowflake INFORMATION_SCHEMA queries return real data
- RCR enforcement prevents access to unauthorized databases
- PostgreSQL RLS prevents cross-workspace data access
- Document upload, extraction, and retrieval pipeline
- Agent message send, SSE stream, and response
- Checkpoint save and restore (interrupt/rollback)
- Artifact persistence and retrieval

---

## 67. E2E Tests (Playwright)

Critical user paths to test:

1. Create data product with tables, run discovery, review artifacts
2. Complete requirements capture, generate BRD
3. Generate semantic view, validate, publish
4. Explorer: ask structured question, verify answer with trust contract
5. Upload document, extract, ask document question
6. Hybrid: ask question spanning structured and document sources

Use Playwright MCP tools for browser automation (do not install Playwright separately).

---

## 68. Benchmark Harness

### Hybrid Q&A Accuracy

Maintain domain-specific benchmark sets including: structured-only cases, document-only cases, hybrid cases, conflict/adversarial cases, abstain scenarios (where the correct answer is "I don't know").

### Benchmark Structure

Each test case specifies: category, question, expected answer (text or numeric), required citations, and whether abstain is acceptable. Scoring evaluates: correctness (exact match or tolerance), citation validity, abstain validity, and critical error detection (confident but wrong).

### Release Gate Integration

Benchmark results feed into release gate decisions. The aggregate_results function computes global and per-category summaries and determines whether release_gate_passed based on critical_error_rate threshold (must be 2% or below).

---

# Part XVII: Release Gates

## 69. Mandatory Gates

Release is blocked if any of these thresholds fail:

| Gate | Threshold | Measurement |
|------|-----------|-------------|
| Critical error rate | Under 2% | Confident-but-wrong answers in benchmark |
| Exactness correctness | Above 95% | Correct exact-value answers for deterministic asks |
| Citation validity | Above 98% | Citations that trace to actual sources |
| Abstention precision | Above 90% | Correct abstentions (truly unanswerable questions) |
| Cross-tenant leakage | Zero | Any data visible across workspace boundaries |

---

## 70. Data Quality Gates

| Gate | Threshold | Stage |
|------|-----------|-------|
| Source data quality | Above 60% (or acknowledged) | After Discovery |
| BRD completeness | Objective + 1 metric + dimensions | After Requirements |
| YAML validation | All critical checks pass | After Validation |
| Grain validation | Zero duplicate rows at grain | After Modeling |

---

# Part XVIII: Delivery Roadmap

## 71. Phase A: Source-Agnostic Foundation

Remove table-only gating from all workflows. Make discovery valid for documents-only, tables-only, and hybrid entry. Implement data layer assessment (bronze/silver/gold tier detection). Build transformation agent for non-gold sources. Align stage transitions with real completion criteria.

## 72. Phase B: Full Cortex and Document Serving

Harden structured intelligence lane (semantic views, Cortex Agent queries). Implement document extraction pipeline end-to-end (PARSE_DOCUMENT, chunking, entity extraction, fact normalization). Build document semantic persistence (PostgreSQL and Neo4j). Ensure publish model supports all three source modes. Stabilize extraction diagnostics and context-impact handling.

## 73. Phase C: Neo4j Grounded Hybrid Intelligence

Expand graph model to include document entities, facts, and cross-source links. Implement hybrid route planner (structured/document/both decision). Build dual-lane execution with evidence union. Enforce runtime graph checks before answer finalization. Add contradiction and staleness-aware confidence downgrades.

## 74. Phase D: Trust UX and Hardening

Finalize answer trust UI (source badges, confidence indicators, evidence drawers, recovery actions). Implement exactness guardrail with deterministic evidence enforcement. Eliminate internal orchestration noise from user stream. Complete output sanitization (leak detection, markdown stripping, technical term replacement). Build benchmark harness and release gate automation.

## 75. Phase E: Multi-Platform Extensibility

Architect plugin system for non-Snowflake warehouses (future). Design provider-agnostic transformation abstraction. Plan BI tool export adapters (PowerBI, Tableau, LookML). Prepare for SaaS deployment model alongside SPCS.

---

# Appendices

## Appendix A: Must-Follow Build Checklist

This checklist covers EVERYTHING required to build ekaiX. Check off each item as completed.

### Infrastructure Setup

- [ ] Docker Compose file with PostgreSQL 18, Neo4j 2025, Redis 8, MinIO
- [ ] Health checks configured for all data services
- [ ] Persistent volumes for all data services
- [ ] Single root .env file with all environment variables documented
- [ ] .env.example with placeholder values (no real credentials)
- [ ] .gitignore excluding .env, sf.txt, credentials files, node_modules, venv, .next, __pycache__
- [ ] PM2 ecosystem.config.js with all three app services

### PostgreSQL Database

- [ ] Run postgresql-schema.sql to create all tables, indexes, RLS policies, triggers
- [ ] Document semantic tables (doc_registry, doc_chunks, doc_facts, doc_fact_links)
- [ ] QA evidence table for trust contract persistence
- [ ] Operational alert events table
- [ ] Verify RLS policies work (test cross-workspace isolation)
- [ ] Audit log partitions created (6 months ahead)

### Neo4j Graph Database

- [ ] Node constraints for all label types (Database, Schema, Table, Column, Document, Entity, Fact)
- [ ] Property indexes on frequently queried fields
- [ ] Full-text indexes on Entity.name, Fact.object
- [ ] Cypher queries for ERD CRUD operations
- [ ] Cypher queries for document entity/fact operations
- [ ] Cypher queries for cross-source entity linking

### Backend (Fastify + TypeScript)

- [ ] Project setup: Fastify 5, TypeScript strict mode, Zod validation
- [ ] Auth middleware (SPCS header extraction, dev fallback)
- [ ] PostgreSQL service with connection pool and RLS user setting
- [ ] Neo4j service with Bolt protocol connection
- [ ] Redis service with async client
- [ ] MinIO service with bucket creation and signed URLs
- [ ] Snowflake service with real SDK queries (no mocks)
- [ ] Health endpoint with all service status checks
- [ ] Auth routes (GET /auth/current-user)
- [ ] Database discovery routes (GET /databases, schemas, tables; POST /reference)
- [ ] Data product CRUD routes (GET, POST, PUT, DELETE /data-products)
- [ ] Data product sharing routes (GET, POST, DELETE /data-products/:id/share)
- [ ] Health check routes (GET, POST /data-products/:id/health-check)
- [ ] Publish route (POST /data-products/:id/publish)
- [ ] Agent routes (POST /message, GET /stream/:sessionId, POST /retry, POST /interrupt, GET /history)
- [ ] Artifact routes (GET /:type/:dataProductId, export)
- [ ] Document routes (GET, POST upload, DELETE, POST extract, context management, semantic registry/chunks/facts/evidence)
- [ ] Settings routes (GET/POST /llm-config)
- [ ] SSE relay from AI service to frontend
- [ ] Structured JSON logging with pino
- [ ] Rate limiting (100 req/min)
- [ ] CORS configuration from environment
- [ ] Global error handler with consistent error response format
- [ ] Zod schemas for all request validation

### AI Service (FastAPI + LangGraph)

- [ ] Project setup: FastAPI, Python venv, Pydantic settings
- [ ] Configuration loading from root .env with all settings groups
- [ ] LLM provider abstraction (multi-provider support via LiteLLM)
- [ ] LLM configuration persistence (PostgreSQL) and runtime override
- [ ] LangGraph orchestrator with Deep Agents
- [ ] PostgreSQL AsyncPostgresSaver for checkpointing
- [ ] Supervisor context contract injection
- [ ] Supervisor guardrails (transition validation, output sanitization, leak detection)
- [ ] Discovery Agent with system prompt and tools
- [ ] Transformation Agent with system prompt and tools
- [ ] Modeling Agent with system prompt and tools
- [ ] Model Builder Agent (requirements + semantic view + validation) with system prompt and tools
- [ ] Publishing Agent with system prompt and tools
- [ ] Explorer Agent with system prompt and tools
- [ ] Snowflake tools (RCR queries, profiling, semantic views, Cortex Agents)
- [ ] Neo4j tools (ERD CRUD, relationship paths, entity classification)
- [ ] PostgreSQL tools (state management, artifact CRUD, document facts/chunks)
- [ ] Transformation tools (profiling, batch transforms, layer registration)
- [ ] Modeling tools (gold tables, grain validation, documentation artifacts)
- [ ] DDL utilities (validation, execution, type casting, Cortex AI fallback)
- [ ] Naming utilities (schema naming conventions, database/schema creation)
- [ ] MinIO tools (artifact upload, retrieval, listing)
- [ ] Document extraction endpoint (Cortex PARSE_DOCUMENT integration)
- [ ] Hybrid answer contract construction and emission
- [ ] Exactness guardrail implementation
- [ ] SSE event streaming (tokens, tool calls, artifacts, phase changes, reasoning, status)
- [ ] Agent interrupt and rollback endpoints
- [ ] Session history endpoint for recovery
- [ ] Feature flag support (hybrid planner, exactness guardrail, trust UX contract)
- [ ] Langfuse tracing integration
- [ ] Lifespan management (startup/shutdown for all connections)
- [ ] Health endpoint

### Frontend (Next.js + React + MUI)

- [ ] Project setup: Next.js 15, React 19, MUI 7, Emotion, TypeScript strict mode
- [ ] MUI theme system with ekai brand colors (dark + light, gold accent only)
- [ ] App layout with sidebar navigation (240px fixed)
- [ ] Dashboard page: data product table with search, filter, sort, pagination
- [ ] Data product creation modal (name, description, databases, schemas, tables, documents)
- [ ] Chat workspace page with scoped Zustand store per data product
- [ ] Phase stepper component (Discovery through Explorer)
- [ ] Message thread with agent/user message rendering
- [ ] Agent message cards with tool call indicators (collapsible)
- [ ] Trust cards on agent messages (source badge, confidence, exactness, evidence, recovery)
- [ ] Artifact cards inline in messages
- [ ] Chat input with file upload (drag-and-drop, file picker)
- [ ] SSE streaming with exponential backoff, jitter, visibility API
- [ ] Artifact panel (resizable right panel)
- [ ] ERD graph viewer (React Flow, interactive nodes/edges)
- [ ] Data Quality Report viewer (health scores, per-table issues)
- [ ] BRD viewer (structured document)
- [ ] YAML viewer (syntax highlighted)
- [ ] Data preview (table grid with CSV export)
- [ ] Data catalog viewer
- [ ] Business glossary viewer
- [ ] Metrics/KPI viewer
- [ ] Validation rules viewer
- [ ] Data lineage viewer
- [ ] Documents panel (upload, extraction status, context activation, delete, re-extract)
- [ ] LLM configuration page (provider selection, model assignment)
- [ ] Session recovery on page reload (restore from backend history)
- [ ] Answer contract normalization and rendering
- [ ] Progress narratives for long-running operations
- [ ] Abstain messaging (two-line pattern: why + what to do)
- [ ] No internal orchestration noise in UI (filter hidden messages)
- [ ] React Query for data fetching with caching

### Document Intelligence

- [ ] Document upload to MinIO
- [ ] Cortex PARSE_DOCUMENT extraction integration
- [ ] Text chunking (paragraph-level, 512-token target, 64-token overlap)
- [ ] Entity extraction (people, organizations, products, dates, amounts)
- [ ] Fact normalization (subject-predicate-object triples with confidence)
- [ ] Semantic linking (entities to knowledge graph nodes)
- [ ] Neo4j persistence (Document, Chunk, Entity, Fact nodes and edges)
- [ ] PostgreSQL persistence (doc_registry, doc_chunks, doc_facts, doc_fact_links)
- [ ] Context activation UI (active/candidate/excluded per stage)
- [ ] Change impact detection (staleness marking on add/update/delete)
- [ ] Document retrieval surfaces (facts for exact values, chunks for context)

### Hybrid Intelligence

- [ ] Route planner (structured/document/hybrid decision)
- [ ] Structured lane execution (SQL via semantic view or Cortex Agent)
- [ ] Document lane execution (graph traversal + fact/chunk retrieval)
- [ ] Dual-lane parallel execution for hybrid questions
- [ ] Evidence union (merge citations from both lanes)
- [ ] Conflict detection (structured vs document disagreement)
- [ ] Exactness guardrail (block validated_exact without deterministic evidence)
- [ ] Trust contract emission after every conversation turn
- [ ] Recovery action generation for abstain states

### Trust and Evidence UX

- [ ] Answer contract data model (source_mode, exactness, confidence, trust_state, citations, recovery_actions)
- [ ] Trust card component rendering all contract fields
- [ ] Source mode badges (Structured blue, Document green, Hybrid purple)
- [ ] Confidence indicators (High checkmark, Medium warning, Abstain X)
- [ ] Expandable evidence drawer
- [ ] Recovery action buttons
- [ ] Abstain two-line messaging (why + what to do)
- [ ] QA evidence persistence (PostgreSQL qa_evidence table)

### Security and Governance

- [ ] SPCS header authentication (Sf-Context-Current-User)
- [ ] PostgreSQL RLS enforcement on all tables
- [ ] Workspace isolation verification
- [ ] Snowflake RCR (EXECUTE AS CALLER) for all data queries
- [ ] Audit logging for all user and agent actions
- [ ] No credentials in source code
- [ ] No data sent to external LLMs (metadata only)
- [ ] Document ACL enforcement

### Testing

- [ ] Frontend unit tests (Vitest): components, stores, SSE parsing, trust rendering
- [ ] Backend unit tests (Vitest): routes, middleware, services, schemas
- [ ] AI service unit tests (pytest): guardrails, tools, hybrid eval, contracts
- [ ] Integration tests against real Snowflake
- [ ] RLS isolation test (cross-workspace access attempt must fail)
- [ ] E2E tests (Playwright MCP): create product, discovery, requirements, publish, explore
- [ ] Benchmark harness for hybrid Q&A accuracy
- [ ] Release gate validation

### SPCS Packaging

- [ ] Multi-stage Dockerfiles for all services
- [ ] spec.yaml for each SPCS service
- [ ] manifest.yml for application package
- [ ] setup_script.sql for installation
- [ ] Image vulnerability scanning
- [ ] Consumer account installation test

### Observability

- [ ] Structured JSON logging across all services
- [ ] Langfuse tracing for LLM calls
- [ ] Health check endpoints for all services
- [ ] Operational alert events table
- [ ] Error categorization and recovery message generation

---

## Appendix B: API Endpoint Reference

Full OpenAPI 3.1 specification is in `docs/plans/api-specification.yaml`. Summary of all endpoints:

### Health
| Method | Path | Purpose |
|--------|------|---------|
| GET | /health | Service liveness probe |

### Auth
| Method | Path | Purpose |
|--------|------|---------|
| GET | /auth/current-user | Get current authenticated user |

### Databases
| Method | Path | Purpose |
|--------|------|---------|
| GET | /databases | List accessible Snowflake databases |
| GET | /databases/:db/schemas | List schemas in database |
| GET | /databases/:db/schemas/:schema/tables | List tables in schema |
| POST | /databases/:db/reference | Create REFERENCE + GRANT CALLER |

### Data Products
| Method | Path | Purpose |
|--------|------|---------|
| GET | /data-products | List user's data products (paginated) |
| POST | /data-products | Create new data product |
| GET | /data-products/:id | Get data product details |
| PUT | /data-products/:id | Update data product |
| DELETE | /data-products/:id | Delete data product |
| GET | /data-products/:id/share | List shares |
| POST | /data-products/:id/share | Share with user |
| DELETE | /data-products/:id/share/:shareId | Revoke share |
| GET | /data-products/:id/health-check | Get latest health check |
| POST | /data-products/:id/health-check | Run new health check |
| POST | /data-products/:id/health-check/:checkId/acknowledge | Acknowledge issues |
| POST | /data-products/:id/publish | Publish to Snowflake Intelligence |

### Agent
| Method | Path | Purpose |
|--------|------|---------|
| POST | /agent/message | Send message to AI agent |
| GET | /agent/stream/:sessionId | SSE stream of agent events |
| POST | /agent/retry | Retry with edited content |
| POST | /agent/interrupt/:sessionId | Interrupt active agent |
| GET | /agent/history/:sessionId | Recover chat history |
| POST | /agent/rollback/:checkpointId | Rollback to checkpoint |
| GET | /agent/checkpoints | List available checkpoints |
| POST | /agent/approve | Approve/reject agent action |

### Artifacts
| Method | Path | Purpose |
|--------|------|---------|
| GET | /artifacts/:type/:dataProductId | Get artifact payload |
| GET | /artifacts/:type/:dataProductId/export | Download artifact |
| GET | /artifacts | List artifacts for data product |
| PUT | /artifacts/:id | Update artifact metadata |

### Documents
| Method | Path | Purpose |
|--------|------|---------|
| GET | /documents/:dataProductId | List documents |
| POST | /documents/upload | Upload document (multipart) |
| DELETE | /documents/:id | Delete document |
| POST | /documents/:id/extract | Trigger extraction |
| GET | /documents/:id/content | Get extracted text |
| GET | /documents/context/:dataProductId/current | Current context state |
| GET | /documents/context/:dataProductId/delta | Context change deltas |
| POST | /documents/context/:dataProductId/apply | Bulk update context |
| GET | /documents/semantic/:dataProductId/registry | Semantic registry |
| GET | /documents/semantic/:dataProductId/chunks | Extracted chunks |
| GET | /documents/semantic/:dataProductId/facts | Extracted facts |
| GET | /documents/semantic/:dataProductId/evidence | Query evidence links |

### Settings
| Method | Path | Purpose |
|--------|------|---------|
| GET | /settings/llm-config | Get LLM configuration |
| POST | /settings/llm-config | Save LLM configuration |

---

## Appendix C: Agent System Prompt Summary

Each subagent has a dedicated system prompt. Key characteristics:

| Agent | Prompt Focus | Key Rules |
|-------|-------------|-----------|
| Orchestrator | Coordinate delegation, enforce state machine, apply supervisor contract | 27 transition rules, copy full history into task() descriptions, never show contract to user |
| Discovery | Conversational profiling, business context questions, data quality | Analysis/Recognition/Question/Suggestion pattern, DAMA CDM progression, plain text only |
| Transformation | Specify transformation specs, batch processing | Native Snowflake only (no DBT), Cortex AI fallback for DDL |
| Modeling | Star schema design (Kimball), fact/dimension tables | Grain validation required, generate all documentation artifacts |
| Model Builder | 7-step lifecycle: requirements, BRD, pause, YAML, verify, pause, validate | Most complex agent, handles BRD + semantic view + validation |
| Publishing | Two-message publish flow: summary then deploy | Requires explicit user approval, includes data quality disclaimer |
| Explorer | Hybrid routing: structured/document/both | Route based on question type, always emit trust contract |

### Communication Rules (All Agents)

- Business language only — no SQL, no UUIDs, no tool names
- Plain text format — no markdown headers, bold, backticks (unicode bullets OK)
- Never reference internal systems (Neo4j, Redis, MinIO, PostgreSQL)
- Never show orchestration terms (task(), subagent, auto-chain, checkpoint)
- Sanitize all output through supervisor guardrails before streaming to user

---

## Appendix D: Glossary

| Term | Definition |
|------|-----------|
| BRD | Business Requirements Document — structured capture of business intent, metrics, dimensions, filters |
| Cortex Agent | Snowflake AI agent accessible via Snowflake Intelligence, bound to semantic views and search services |
| Cortex Analyst | Snowflake service for natural language queries against semantic views |
| DAMA CDM | Data Management Association Conceptual Data Model — progression from conceptual to logical to physical understanding |
| Data Product | The governed intelligence unit in ekaiX — sources + requirements + artifacts + trust evidence + published serving |
| Dynamic Table | Snowflake table with automatic incremental refresh based on a query definition |
| ERD | Entity-Relationship Diagram — visual representation of tables, columns, and relationships |
| Evidence Path | A traceable chain through the knowledge graph from question entities to source data |
| Exactness | Whether an answer provides a precise, deterministically verified value or an estimate |
| FQN | Fully Qualified Name — DATABASE.SCHEMA.TABLE format for Snowflake objects |
| Gold Layer | Curated, business-ready data suitable for direct semantic modeling |
| Hybrid Intelligence | Combining structured warehouse data and unstructured document data to answer questions |
| Knowledge Graph | Neo4j-based graph connecting entities, tables, documents, facts, metrics, and requirements |
| LiteLLM | Python library for multi-provider LLM routing with automatic failover |
| MinIO | S3-compatible object storage for artifacts and documents |
| Mission Control | The six-stage workflow: Discovery, Requirements, Modeling, Validation, Publishing, Explorer |
| RCR | Restricted Caller's Rights — Snowflake security model where queries execute with the caller's permissions |
| RLS | Row-Level Security — PostgreSQL feature that filters query results based on the current user |
| Route Planner | Component that decides whether to answer a question from structured, document, or both sources |
| Semantic View | Snowflake YAML definition that maps business concepts (metrics, dimensions) to physical tables |
| Source Mode | Whether a data product uses structured data, documents, or both |
| SPCS | Snowpark Container Services — Snowflake's container runtime for native apps |
| Supervisor Contract | Internal context packet injected into orchestrator input with current phase, state, and routing directives |
| Trust Contract | Machine-readable record attached to every answer with source mode, exactness, confidence, citations, and recovery actions |
| Trust State | Overall reliability assessment of an answer (ready, warnings, abstained, blocked, failed) |

---

*End of document. This PRD is the single authoritative specification for building ekaiX. All previous PRD versions are superseded.*
