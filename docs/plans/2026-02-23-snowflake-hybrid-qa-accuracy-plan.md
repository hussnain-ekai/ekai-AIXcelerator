# ekaiX Enterprise Hybrid Intelligence Plan (Feb 23, 2026)

## 1. Core Product Principle
Enterprise users do not care where data lives. They ask one question and expect one accurate answer.

ekaiX must treat:
1. Structured warehouse data.
2. Unstructured documents (SharePoint, Office, PDFs, emails, uploads).
3. Mixed questions across both worlds.

as one answer system with one accountability layer.

## 2. Non-Negotiable Accuracy Contract
Accuracy is enforced by product policy, not prompt style.

- Every answer must be grounded in evidence artifacts.
- Numeric answers must resolve to deterministic facts (SQL rows or normalized extracted facts), not free-text generation.
- If evidence is missing, stale, conflicting, or low quality, ekaiX must abstain with a recovery plan.
- No confident answer without traceable provenance.

This is the required behavior for Allan-type use cases (invoice line item exact amount, spare part history, latest purchase date).

## 3. Serving Model: One Agent Experience, Multi-Engine Under the Hood
### User-facing
- Single chat surface.
- No user decision about "structured vs unstructured."
- Answer first, citations second, recovery options third.

### System-facing
- One supervisor orchestration path.
- Three execution lanes:
  1. Structured lane: Cortex Analyst on semantic views.
  2. Unstructured lane: document parsing + normalized fact extraction + Cortex Search.
  3. Hybrid lane: planner combines both lanes and synthesizes.

## 4. Architecture (Snowflake-Native)
### 4.1 Control Plane
- Mission Control stages remain: discovery, requirements, modeling, validation, publishing.
- Supervisor agent handles query planning, execution tracking, fallback, and evidence enforcement.
- Agent state machine emits explicit statuses: `running`, `waiting`, `blocked`, `failed`, `recovered`, `completed`.

### 4.2 Data Plane
- Structured truth:
  - Semantic views over warehouse tables.
  - Cortex Analyst tool for business metrics and governed SQL.
- Document truth:
  - Ingest via uploads + SharePoint connector.
  - Parse/extract with `AI_PARSE_DOCUMENT` / `AI_EXTRACT`.
  - Build chunk index with Cortex Search.
  - Normalize high-value business facts into typed tables.
- Hybrid truth:
  - Query planner calls Analyst + Search + fact tables.
  - Response synthesizer enforces citation and consistency checks.

## 5. Document Semantic Layer (Critical Addition)
Pure chunk-level RAG is not enough for exact enterprise answers. ekaiX needs a modeled document layer.

### 5.1 Canonical Tables
- `doc_registry`
  - `doc_id`, `tenant_id`, `source_system`, `source_uri`, `title`, `mime_type`, `checksum`, `version_id`, `uploaded_by`, `uploaded_at`, `deleted_at`.
- `doc_chunks`
  - `chunk_id`, `doc_id`, `chunk_text`, `chunk_seq`, `section_path`, `page_no`, `embedding_ref`, `acl_scope`, `parser_version`.
- `doc_entities`
  - `entity_id`, `doc_id`, `entity_type`, `canonical_value`, `raw_value`, `start_offset`, `end_offset`, `confidence`, `source_chunk_id`.
- `doc_facts`
  - `fact_id`, `doc_id`, `fact_type`, `subject_key`, `predicate`, `object_value`, `object_unit`, `event_time`, `currency`, `confidence`, `source_chunk_id`, `source_page`.
- `doc_fact_links`
  - bridges facts to enterprise keys (`customer_id`, `part_id`, `asset_id`, `vendor_id`) with match rationale.
- `qa_evidence`
  - persisted evidence packet per answer (`query_id`, `tool_calls`, `sql_refs`, `fact_refs`, `chunk_refs`, `conflicts`, `final_decision`).

### 5.2 Why This Matters
- Typical RAG answers "what text looks similar."
- ekaiX must answer "what is true for this business concept."
- Typed fact modeling turns unstructured content into queryable business truth.

## 6. Ingestion and Extraction Flow
### 6.1 Source Ingestion
- Warehouse assets: current discovery flow (tables/views/profiles).
- Document assets:
  - SharePoint/Office connectors (scheduled sync + event-driven updates where available).
  - User uploads at any stage (creation, chat attachment, document panel).
  - Manual imports from local files.

### 6.2 Parsing and Normalization
- Format-aware extraction path:
  - Text-native docs: direct parse.
  - Scanned docs/images: OCR + layout recovery.
  - Structured files (SQL, CSV, XML, JSON): parser-first extraction.
  - BI model files (PBIX etc.): metadata extractor where possible + fallback to binary metadata.
- Every extractor writes parse diagnostics (`quality_score`, missing sections, OCR confidence, parse warnings).
- Maintain immutable document version history; never overwrite extracted evidence in place.

### 6.3 Context Delta Handling
- On add/update/delete:
  - mark impacted facts/chunks as changed.
  - mark impacted artifacts as stale (`requirements`, `metrics`, `semantic mappings`, `published agent`).
  - offer targeted rerun actions instead of full pipeline reset.

## 7. Unified Query Planner
### 7.1 Query Classification
Each question is classified by intent and evidence requirement:
- Metric/aggregation.
- Transaction lookup (invoice/order/part line item).
- Policy/procedure/explanation.
- Root-cause/hybrid analysis.
- Action request (artifact edit/publish/update).

### 7.2 Routing Matrix
- Metric questions -> structured lane first.
- Exact transactional questions in docs (invoice amount, purchase line, serial number) -> `doc_facts` first, fallback to chunk retrieval for disambiguation.
- Policy/explanation -> doc_chunks via Search, optionally enriched by structured context.
- Hybrid questions -> dual plan:
  1. Retrieve document evidence and entity anchors.
  2. Resolve anchors to semantic model keys.
  3. Run structured query for final metric answer.
  4. Assemble answer with both evidence types.

### 7.3 Deterministic Guardrails
- If question requires exact number:
  - must return sourced numeric fact row(s) plus citation.
  - if only fuzzy text evidence exists, answer is `insufficient evidence` with next action.
- No fallback to generative guess for exact-value questions.

## 8. Evidence-First Response Synthesis
### 8.1 Response Packet
Each answer must include:
- `answer_text`
- `confidence_decision` (`high`, `medium`, `abstain`)
- `evidence_summary`
- `citations` (doc/page/chunk or SQL reference)
- `conflict_notes`
- `recovery_plan` (if not high confidence)

### 8.2 Conflict Handling
- If sources disagree:
  - expose disagreement clearly.
  - identify newest/governed source preference policy.
  - request user selection only when policy cannot resolve safely.

### 8.3 Honest Failure Behavior
- Do not hide uncertainty behind generic prose.
- Explicitly state missing field/source and propose concrete remediation:
  - upload missing invoice.
  - confirm part alias mapping.
  - rerun extraction on low OCR quality pages.

## 9. Accuracy and Evaluation Framework
### 9.1 Benchmark Design
- Build customer-specific gold set (250-500 questions).
- Distribution:
  - structured-only
  - unstructured-only
  - hybrid
  - adversarial/conflict cases
- Include ambiguity, outdated docs, near-duplicate terms, unit/currency differences.

### 9.2 Metrics
- Correctness score (human-reviewed rubric).
- Numeric exactness score (tolerance zero or domain-specific).
- Citation validity score.
- Abstention precision (did we abstain when we should).
- Critical error rate (confident but wrong).
- Time-to-answer and cost-per-answer.

### 9.3 Release Gate
- No production expansion without passing benchmark gates.
- Priority metric is minimizing critical errors, not maximizing always-answer rate.

## 10. Security, Governance, and Compliance
- ACL propagation from source systems into chunk/fact/search indexes.
- Tenant isolation in all tables and search filters.
- Row-level and object-level security enforcement in retrieval and synthesis.
- PII tagging + masking policy in extraction outputs.
- Audit trail for every answer: query, tool calls, evidence IDs, model version, prompt hash.
- Retention policy by source class; support legal hold and right-to-delete flows.

## 11. UX Requirements for Trust
### 11.1 Keep the Current Shell, Upgrade the Core Interaction Model
- Keep single-chat UX and mission-control frame.
- Do not ask users to choose structured vs unstructured vs hybrid.
- Surface one answer contract with transparent evidence.

### 11.2 Mandatory UX Components (Build Scope)
1. `Source-aware answer card`
   - Badge: `Structured`, `Document`, `Hybrid`.
   - Show confidence state and recency marker.
2. `Evidence drawer`
   - SQL lineage references (query IDs / semantic metrics used).
   - Document citations (file, page, chunk, extracted fact rows).
   - Conflict markers and resolution policy result.
3. `Exactness mode`
   - For exact-number questions show:
     - `Validated exact value` OR
     - `Insufficient evidence for exact value`.
   - Never show probabilistic language as exact.
4. `Recovery panel`
   - Actionable paths only (upload missing doc, confirm entity alias, rerun extraction, refresh source sync).
   - No generic "try again later" messaging.
5. `Document context panel`
   - Version history, extraction health, parse diagnostics.
   - Context delta indicators: "this change affects requirements/model/agent."
6. `Long-running progress UX`
   - Human-readable progress by stage.
   - Optional details panel for technical trace.
   - Never stream internal orchestration noise as user-facing "thinking."

### 11.3 UX Content and Language Rules
- Default language: business-first, concise, decision-ready.
- Technical terms hidden behind "details."
- If abstaining:
  - explain why in one sentence,
  - provide best next step in one sentence,
  - include required evidence to unblock.

### 11.4 UX States
- `Answer Ready`
- `Answer with Warnings`
- `Abstained - Missing Evidence`
- `Abstained - Conflicting Evidence`
- `Blocked - Access/Permission`
- `Failed - Recoverable` (with next action)
- `Failed - Needs Admin` (with escalation guidance)

## 12. Implementation Plan (Detailed)
### Phase 0: Prerequisites (1 week)
- Finalize domain ontology templates (customer, invoice, part, asset, vendor, contract).
- Define evidence schema and provenance contract.
- Define failure taxonomy and recovery action catalog.
- Define UX state taxonomy and message templates.

### Phase 1: Document Semantic Layer (2 weeks)
- Implement canonical tables (`doc_registry`, `doc_chunks`, `doc_entities`, `doc_facts`, `qa_evidence`).
- Implement ingestion connectors + upload pipeline versioning.
- Integrate parse diagnostics and extraction quality scoring.
- Build Document Context panel backend contracts (versioning, diagnostics, context impact flags).

### Phase 2: Retrieval and Fact Serving (2 weeks)
- Build Cortex Search services over chunks and selected facts.
- Add faceted filters (tenant, product, date, document type, source, ACL).
- Implement transaction-grade fact lookup endpoints for exact numeric asks.
- Build Evidence drawer API payloads (SQL refs, doc refs, fact refs, conflicts).

### Phase 3: Unified Query Planner (2 weeks)
- Add intent classifier + execution planner.
- Implement route policies for structured/unstructured/hybrid.
- Add deterministic exact-answer guardrail and abstain path.
- Emit source mode and confidence contract for UI (`source_mode`, `exactness_state`, `abstain_reason`).

### Phase 4: Synthesis and UX (1-2 weeks)
- Implement Source-aware answer card and Exactness mode UI.
- Implement Evidence drawer UI with citations and conflict rendering.
- Implement Recovery panel with explicit next actions.
- Implement long-running progress UX with clean business-facing messages + optional details trace.
- Enforce UX language guardrails (no internal event jargon in primary chat stream).

### Phase 5: Evaluation and Hardening (2-3 weeks)
- Create benchmark harness and baseline comparisons (ekaiX vs generic assistants).
- Add regression suite for high-risk query categories.
- Add SLA/SLO monitoring and alerting for extraction/retrieval failure patterns.
- Add UX trust metrics:
  - citation open rate,
  - recovery action completion rate,
  - abstain acceptance rate,
  - user correction frequency after answer delivery.

## 13. Practical Constraints (No False Affirmations)
- Perfect accuracy on arbitrary unstructured input is not realistic.
- OCR noise, poor scan quality, and ambiguous wording are hard limits.
- ekaiX can still be enterprise-grade by enforcing:
  - evidence-based exactness when facts exist,
  - abstention when they do not,
  - clear remediation to close gaps.

For non-negotiable accuracy workloads, high-value document facts must be promoted into governed structured stores and validated continuously.

## 14. Success Criteria
ekaiX is successful when all are true:
1. Users ask once and receive a grounded answer independent of data location.
2. Exact-value questions return exact values with provenance or abstain safely.
3. Hybrid questions combine both worlds without silent contradiction.
4. Critical error rate stays near zero in customer benchmark packs.
5. Users can understand why an answer is trustworthy without reading technical logs.
6. Recovery flows reduce dead-end sessions and prevent limbo perception.

## 15. Snowflake Capabilities Used
- Cortex Analyst semantic models.
- Cortex Search services.
- Cortex Agents orchestration.
- `AI_PARSE_DOCUMENT` and `AI_EXTRACT`.
- Snowflake security primitives for row/object access and governance.

## 16. References
- Cortex Agents: https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents
- Cortex Search query/filter model: https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-search/query-cortex-search-service
- Cortex Analyst semantic model spec: https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/semantic-model-spec
- AI_EXTRACT: https://docs.snowflake.com/en/sql-reference/functions/ai_extract
- AI_PARSE_DOCUMENT: https://docs.snowflake.com/en/sql-reference/functions/ai_parse_document
- AI_PARSE_DOCUMENT images update (Jan 26, 2026): https://docs.snowflake.com/en/release-notes/2026/other/2026-01-26-ai-parse-document-images-preview
- SharePoint Openflow connector: https://docs.snowflake.com/en/user-guide/data-integration/openflow/connectors/sharepoint/about

## 17. Execution Backlog
Service-wise implementation tickets are tracked in:  
`docs/plans/2026-02-23-hybrid-qa-implementation-tickets.md`
