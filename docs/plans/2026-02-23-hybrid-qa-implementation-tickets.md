# ekaiX Hybrid Intelligence — Implementation Tickets by Service

**Date:** 2026-02-23  
**Primary spec:** `docs/plans/2026-02-23-snowflake-hybrid-qa-accuracy-plan.md`  
**Goal:** Build enterprise-grade accurate Q/A across structured, unstructured, and hybrid questions with one user-facing chat experience.

## Delivery Model
Use 5 execution tracks in parallel where possible:
1. `DATA` Snowflake schemas, indexes, ACL-ready data model.
2. `AI` supervisor planning, routing, synthesis, abstain policy.
3. `API` backend contracts, evidence endpoints, context delta APIs.
4. `UI` answer trust UX, evidence drawer, recovery UX, doc context UX.
5. `QA` benchmark harness, regression suites, reliability and security checks.

## Ticket Format
- ID: unique key (`HYB-<TRACK>-NNN`)
- Scope: what must be built
- Deliverables: code/schema/API/UI outputs
- Acceptance Criteria: objective pass conditions
- Dependencies: upstream tickets that must land first

## Milestones
- `M1 Foundation`: data model + contracts + UX state model
- `M2 Unstructured Serving`: extraction, chunk/fact serving, evidence persistence
- `M3 Hybrid Intelligence`: dual-lane planner + deterministic exactness guardrails
- `M4 Trust UX`: source-aware answers, evidence transparency, recovery flows
- `M5 Hardening`: benchmark gates, observability, security, rollout controls

## DATA Track (Snowflake + persistence)

### HYB-DATA-001 (M1)
- Scope: create canonical document semantic schema.
- Deliverables: DDL for `doc_registry`, `doc_chunks`, `doc_entities`, `doc_facts`, `doc_fact_links`, `qa_evidence`.
- Acceptance Criteria: migrations run cleanly in dev; tables include tenant and provenance columns; indexes support common filter paths.
- Dependencies: none.

### HYB-DATA-002 (M1)
- Scope: add document versioning and soft delete semantics.
- Deliverables: version keys, checksum, lifecycle status columns, update/delete triggers.
- Acceptance Criteria: new upload of same source creates new version; old evidence remains queryable; delete marks stale and propagates delta flags.
- Dependencies: HYB-DATA-001.

### HYB-DATA-003 (M2)
- Scope: create Cortex Search-ready views/materialized surfaces.
- Deliverables: curated `v_doc_search_chunks`, `v_doc_search_facts` with ACL fields.
- Acceptance Criteria: query latency and filter behavior validated on sample datasets; no cross-tenant leakage.
- Dependencies: HYB-DATA-001.

### HYB-DATA-004 (M2)
- Scope: enterprise key linking for document facts.
- Deliverables: matching/linking rules and persistence in `doc_fact_links`.
- Acceptance Criteria: invoice/part/customer references can resolve to semantic-model keys for hybrid queries.
- Dependencies: HYB-DATA-001, HYB-DATA-002.

### HYB-DATA-005 (M5)
- Scope: governance controls.
- Deliverables: row/object policies, retention jobs, legal-hold support paths.
- Acceptance Criteria: role-scoped retrieval tests pass; deletion/retention policy audit logs available.
- Dependencies: HYB-DATA-001 through HYB-DATA-004.

## AI Track (`ai-service`)

### HYB-AI-001 (M1)
- Scope: define unified response contract.
- Deliverables: typed schema for `source_mode`, `exactness_state`, `confidence_decision`, `citations`, `recovery_plan`.
- Acceptance Criteria: all answer paths emit contract-compliant payloads.
- Dependencies: none.

### HYB-AI-002 (M2)
- Scope: implement document extraction orchestration upgrades.
- Deliverables: parser diagnostics capture, extraction quality scoring, provenance stamping.
- Acceptance Criteria: each extraction persists warnings/quality scores; low-quality docs can trigger abstain path.
- Dependencies: HYB-DATA-001.

### HYB-AI-003 (M3)
- Scope: unified query classifier and planner.
- Deliverables: intent classification (`metric`, `transaction_lookup`, `policy`, `hybrid`) and route plan object.
- Acceptance Criteria: planner chooses correct lane on benchmark seed set; rationale persisted.
- Dependencies: HYB-AI-001, HYB-DATA-003.

### HYB-AI-004 (M3)
- Scope: deterministic exactness guardrail.
- Deliverables: strict policy for exact-number asks to use SQL/fact rows only; forced abstain when unresolved.
- Acceptance Criteria: no exact numeric answer is emitted from chunk similarity alone.
- Dependencies: HYB-AI-003, HYB-DATA-004.

### HYB-AI-005 (M3)
- Scope: conflict detection and synthesis policy.
- Deliverables: newest/governed-source precedence logic + explicit conflict output shape.
- Acceptance Criteria: contradictory source scenarios return transparent conflict notes and safe resolution path.
- Dependencies: HYB-AI-003.

### HYB-AI-006 (M4)
- Scope: no-limbo execution lifecycle.
- Deliverables: explicit state transitions and recoverable failure actions (`blocked`, `failed_recoverable`, `failed_admin`).
- Acceptance Criteria: long-running and failed tasks always surface next action; no silent hangs.
- Dependencies: HYB-AI-001.

### HYB-AI-007 (M5)
- Scope: evaluation harness integration.
- Deliverables: automated benchmark runner and per-category score outputs.
- Acceptance Criteria: structured, unstructured, and hybrid scorecards generated for release gate.
- Dependencies: HYB-AI-003 through HYB-AI-005.

## API Track (`backend`)

### HYB-API-001 (M1)
- Scope: expose document semantic layer APIs.
- Deliverables: endpoints for doc versions, extraction diagnostics, fact browsing, evidence retrieval.
- Acceptance Criteria: API contracts documented and validated with schema tests.
- Dependencies: HYB-DATA-001.

### HYB-API-002 (M2)
- Scope: context delta API.
- Deliverables: endpoint showing artifacts impacted by doc add/update/delete and recommended rerun actions.
- Acceptance Criteria: deleting or replacing docs marks stale artifacts and returns targeted rerun suggestions.
- Dependencies: HYB-DATA-002.

### HYB-API-003 (M3)
- Scope: hybrid answer API envelope.
- Deliverables: backend normalization of AI response contract for UI consistency.
- Acceptance Criteria: all responses include source mode, citations, exactness state, and recovery metadata.
- Dependencies: HYB-AI-001.

### HYB-API-004 (M4)
- Scope: evidence deep-link support.
- Deliverables: stable links/IDs for SQL references and document citations consumable by UI drawer.
- Acceptance Criteria: clicking evidence from UI resolves to exact source context.
- Dependencies: HYB-API-001, HYB-AI-005.

### HYB-API-005 (M5)
- Scope: audit and compliance API.
- Deliverables: answer trace retrieval endpoints (tool calls, evidence ids, model version hash).
- Acceptance Criteria: audit queries can reconstruct why each answer was produced.
- Dependencies: HYB-AI-005, HYB-DATA-005.

## UI Track (`frontend`)

### HYB-UI-001 (M1)
- Scope: UX state model.
- Deliverables: UI state enum and rendering rules for `Answer Ready`, `Warnings`, abstain states, blocked/failure states.
- Acceptance Criteria: no fallback generic error banners for agentic failures.
- Dependencies: HYB-AI-001.

### HYB-UI-002 (M4)
- Scope: source-aware answer card.
- Deliverables: badges for `Structured/Document/Hybrid`, confidence state, recency indicators.
- Acceptance Criteria: every answer displays source mode and confidence without opening details.
- Dependencies: HYB-API-003.

### HYB-UI-003 (M4)
- Scope: evidence drawer.
- Deliverables: citations panel with SQL references, doc page/chunk refs, fact rows, conflict markers.
- Acceptance Criteria: evidence can be opened per answer and supports deep links.
- Dependencies: HYB-API-004.

### HYB-UI-004 (M4)
- Scope: exactness mode UI.
- Deliverables: visual distinction between `validated exact value` and `insufficient evidence`.
- Acceptance Criteria: exact numeric questions never appear as "exact" unless flagged validated by contract.
- Dependencies: HYB-API-003, HYB-AI-004.

### HYB-UI-005 (M4)
- Scope: recovery UX.
- Deliverables: actionable follow-up controls (upload doc, map alias, rerun extraction, refresh sync).
- Acceptance Criteria: abstain responses always provide at least one actionable remediation path.
- Dependencies: HYB-API-002, HYB-AI-006.

### HYB-UI-006 (M2/M4)
- Scope: document context panel.
- Deliverables: doc versions, extraction diagnostics, context impact badges.
- Acceptance Criteria: users can see which mission-control artifacts became stale after doc changes.
- Dependencies: HYB-API-001, HYB-API-002.

### HYB-UI-007 (M4)
- Scope: long-running progress UX refresh.
- Deliverables: business-friendly progress narrative + optional technical details pane.
- Acceptance Criteria: internal orchestration noise is hidden from primary stream; no limbo perception.
- Dependencies: HYB-AI-006.

## QA Track (E2E, benchmark, release gates)

### HYB-QA-001 (M1)
- Scope: test matrix authoring.
- Deliverables: scenario matrix for structured, unstructured, hybrid, conflict, permissions, and failure recovery paths.
- Acceptance Criteria: matrix approved and mapped to automated/manual suites.
- Dependencies: none.

### HYB-QA-002 (M2)
- Scope: extraction quality regression suite.
- Deliverables: corpus across PDFs, scans, SQL, PBIX-like files, docs with partial/missing fields.
- Acceptance Criteria: quality score + parse warnings behave deterministically across versions.
- Dependencies: HYB-AI-002.

### HYB-QA-003 (M3)
- Scope: exactness guardrail tests.
- Deliverables: negative tests proving no guessed exact-number answers without deterministic facts.
- Acceptance Criteria: all guardrail cases pass in CI.
- Dependencies: HYB-AI-004.

### HYB-QA-004 (M4)
- Scope: Playwright trust UX suite.
- Deliverables: tests for evidence drawer, exactness mode, abstain recovery, long-running progress states.
- Acceptance Criteria: UX behaviors pass on desktop and mobile breakpoints.
- Dependencies: HYB-UI-002 through HYB-UI-007.

### HYB-QA-005 (M5)
- Scope: benchmark and release gate automation.
- Deliverables: scoring pipeline against 250-500 gold questions and gating report.
- Acceptance Criteria: release blocked if critical error threshold exceeded.
- Dependencies: HYB-AI-007.

## Cross-Cutting Ops/Security Track

### HYB-OPS-001 (M2)
- Scope: observability foundations.
- Deliverables: tracing for planner decisions, tool calls, retrieval latency, abstain reasons.
- Acceptance Criteria: per-answer traces visible in ops dashboards.
- Dependencies: HYB-AI-003.

### HYB-OPS-002 (M3)
- Scope: SLOs and alerting.
- Deliverables: alerts for extraction failures, stalled runs, citation-missing answers, cross-tenant query violations.
- Acceptance Criteria: alerts fire in staging chaos tests.
- Dependencies: HYB-OPS-001.

### HYB-OPS-003 (M5)
- Scope: rollout controls.
- Deliverables: feature flags for hybrid planner, exactness mode, trust UX; canary rollout playbook.
- Acceptance Criteria: safe staged release with rollback instructions.
- Dependencies: HYB-QA-005.

## Sequencing and Parallelization
- Sprint 1: HYB-DATA-001, HYB-AI-001, HYB-API-001, HYB-UI-001, HYB-QA-001
- Sprint 2: HYB-DATA-002/003, HYB-AI-002, HYB-API-002, HYB-UI-006, HYB-QA-002, HYB-OPS-001
- Sprint 3: HYB-DATA-004, HYB-AI-003/004/005, HYB-API-003/004, HYB-QA-003, HYB-OPS-002
- Sprint 4: HYB-AI-006, HYB-UI-002/003/004/005/007, HYB-QA-004
- Sprint 5: HYB-AI-007, HYB-DATA-005, HYB-API-005, HYB-QA-005, HYB-OPS-003

## Definition of Done (Program-Level)
- Hybrid Q/A answers are source-labeled, citation-backed, and exactness-governed.
- Exact numeric answers require deterministic evidence; otherwise abstain + recovery.
- Security model enforces tenant/role isolation across retrieval and evidence views.
- Benchmark gates pass with near-zero critical error rate before rollout.
