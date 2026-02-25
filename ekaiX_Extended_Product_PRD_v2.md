# ekaiX Extended Product Requirements Document

**Product:** ekai AIXelerator (ekaiX)  
**Version:** 2.1  
**Date:** February 24, 2026  
**Document Type:** Extended PRD (new; does not replace `ekaiX_Technical_PRD.md`)

---

## 1. Executive Summary

ekaiX is being built as an enterprise-grade, source-agnostic intelligence product where a business user asks one question in one chat and receives one accountable answer.

The answer may come from:

- Structured warehouse data
- Unstructured enterprise documents
- Both together

The user should not need to know where the answer came from. That is ekaiX’s responsibility.

The product must prioritize **confident correctness**, not “always answer.” For exact-value questions, ekaiX must use deterministic evidence (SQL row-level evidence or normalized document facts). If deterministic evidence is unavailable or conflicting, ekaiX must abstain clearly and provide a concrete recovery path.

This PRD extends the original product direction to support full hybrid intelligence while maintaining strict governance, explainability, and business-user-first experience.

---

## 2. Problem Statement

Business users currently use generic AI tools that often fail in enterprise contexts because:

- They are not grounded in governed enterprise data
- They do not preserve permissions and tenant boundaries
- They return plausible but unverifiable answers
- They cannot reliably combine warehouse truth with document truth

In parallel, semantic modeling and BI enablement workflows are fragmented across teams, tools, and technical handoffs. This slows delivery and reduces trust.

ekaiX solves this by combining semantic modeling, document understanding, and hybrid Q/A in a single guided agentic workflow with strict evidence policies.

---

## 3. Product Vision and End Goal

### Vision
A business analyst can create and use a trusted data product through natural conversation, without SQL or data engineering expertise, and still receive accurate, explainable answers.

### End Goal
Deliver a production-ready enterprise service where:

1. Users can start with structured sources, unstructured sources, or both.
2. ekaiX builds and maintains semantic intelligence artifacts for those sources.
3. Users can ask operational and analytical questions in one chat.
4. Every answer is evidence-backed and permission-safe.
5. Cortex Agent serving is publishable and governed in Snowflake.

### Success Definition
The product is successful when business users prefer ekaiX over generic copilots for enterprise questions because they can trust the answers and audit why each answer was produced.

---

## 4. Product Principles

1. **Single business chat, multiple engines under the hood**
2. **Evidence first, narrative second**
3. **No deterministic evidence, no deterministic answer**
4. **Abstain safely instead of hallucinating**
5. **Business language by default, technical detail on demand**
6. **No limbo states; always show status and next action**
7. **User-controlled context; system-explained impact of context changes**

---

## 5. Personas

### Primary Persona: Business Analyst

- Understands business domain deeply
- Does not want SQL, data model internals, or orchestration complexity
- Needs accurate metrics, transaction details, and policy context
- Needs to adjust requirements iteratively in chat

### Secondary Persona: Analytics/Product Owner

- Owns metric definitions and business semantics
- Needs reviewable, reusable semantic artifacts
- Needs confidence before publishing and rollout

### Governance Persona: Data/Platform Steward

- Enforces security, permissions, compliance, and auditability
- Needs clear operational visibility, traceability, and controls

---

## 6. Scope

### In Scope

- Source-agnostic data product creation (structured-only, unstructured-only, hybrid)
- Mission-control workflow from discovery to publish
- Semantic modeling over structured and document-derived facts
- Hybrid question planning and execution
- Cortex-based serving in Snowflake
- Evidence and trust UX (confidence, exactness, citations, recovery)
- No-limbo execution states and failure recovery
- Governance, audit, and cross-tenant safety controls

### Out of Scope

- Open-ended, unguided AI assistant with no governance
- Replacing enterprise warehousing/MDM systems
- Autonomous policy overrides without admin controls

---

## 7. Product Experience and Workflow

### 7.1 Data Product Concept

A data product in ekaiX is a governed intelligence unit containing:

- Source registrations (tables, documents, or both)
- Requirements artifacts (business intent and rules)
- Semantic artifacts (structured semantic views and/or document semantic structures)
- Validation artifacts and trust evidence
- Published serving configuration

### 7.2 Entry Modes

Users can create a data product with:

- Tables only
- Documents only
- Tables + documents

At least one source is required. Table selection is **not mandatory**.

### 7.3 Mission Control Stages

### Discovery

Purpose: understand selected sources and produce grounded context.

- Structured discovery: schema/profile/relationships
- Document discovery: extraction diagnostics, entities, candidate facts, document quality
- Neo4j graph update with discovered entities and links

Output:

- Discovery summary in business language
- Data/document quality diagnostics
- ERD and graph context
- Recommended next actions

### Requirements

Purpose: capture business intent deeply and precisely.

- Agent asks focused, high-signal follow-ups
- Uses discovered context to avoid generic questions
- Captures objective, metric logic, dimensions, filters, constraints, definitions
- Supports iterative edits at any time

Output:

- Business Requirements Document (BRD) and requirement graph nodes

### Modeling

Purpose: convert requirements into executable semantic intelligence.

- Structured path: semantic view definition over warehouse assets
- Document path: normalized fact model and retrieval surfaces
- Hybrid path: linking rules between document facts and warehouse entities

Output:

- Semantic modeling artifacts with traceability back to requirements

### Validation

Purpose: prevent incorrect or unverifiable answers before publish.

- Structured validation: SQL compile/runtime sanity checks
- Document validation: extraction quality, fact confidence, citation integrity
- Hybrid validation: cross-source consistency and join/link confidence
- Exactness validation: deterministic-evidence checks for exact asks

Output:

- Validation report and publish readiness state

### Publishing

Purpose: deploy serving configuration for business Q/A.

- Structured-only publish: semantic view + structured answer lane
- Document-only publish: document search/fact-serving lane
- Hybrid publish: both lanes + route policy and synthesis rules

Output:

- Published Cortex serving setup and access metadata

### Explorer

Purpose: ongoing business Q/A and artifact refinement.

- User asks questions in plain language
- ekaiX routes to structured/unstructured/hybrid lanes automatically
- Returns trusted answer or abstain+recovery
- Allows direct edits to artifacts and revalidation loops

---

## 8. Target Answer Behavior (Trust Contract)

Every answer must include a machine and UI contract with:

- Source mode (`structured`, `document`, `hybrid`, or `unknown`)
- Exactness state (`validated exact`, `estimated`, `insufficient evidence`, `not applicable`)
- Confidence decision (`high`, `medium`, `abstain`)
- Trust state (`ready`, `warnings`, `abstained missing evidence`, `abstained conflicting evidence`, `blocked`, `failed`)
- Citations (SQL refs and/or document refs)
- Recovery actions when not ready

### Exactness Policy

For exact-value questions:

- Allowed: SQL-backed or normalized document-fact-backed answers
- Not allowed: pure semantic-similarity/chunk-only final numbers
- On missing/conflicting deterministic evidence: abstain with explicit next step

---

## 9. Technical Architecture

ekaiX architecture has two planes:

1. **Control Plane** for orchestration, state, and workflow governance
2. **Data/Serving Plane** for retrieval, semantics, and answer execution

### 9.1 Control Plane Components

- Frontend (Next.js): business chat and mission-control UX
- Backend API (Fastify): contracts, auth context, orchestration endpoints
- AI Service (FastAPI): supervisor, planning, tool routing, answer synthesis
- Redis: in-flight orchestration state and streaming coordination
- PostgreSQL: persistent product/workflow/evidence metadata

### 9.2 Data/Serving Plane Components

- Snowflake warehouse assets (structured source of truth)
- Snowflake Cortex services (serving and AI functions)
- Document object storage and extraction outputs
- Document semantic persistence (facts/chunks/entities)
- Neo4j knowledge graph (cross-source semantic grounding)

### 9.3 Storage Responsibilities

- **PostgreSQL:** canonical app state, versions, artifacts, QA evidence, audit
- **Neo4j:** semantic graph, relationships, lineage, evidence paths
- **Redis:** ephemeral session and streaming state
- **MinIO/Object Store:** raw uploads and binary artifacts

---

## 10. Snowflake Architecture (Cortex-Centric)

This section defines how Snowflake services are used in the final product.

### 10.1 Structured Intelligence Lane

Primary components:

- Snowflake semantic views
- Cortex Analyst-compatible query path
- Governed SQL execution with caller permissions

Usage:

- Requirements-driven semantic definitions are generated and validated
- Metric and dimensional questions are answered through semantic abstractions
- SQL lineage (query references) is attached as evidence

### 10.2 Unstructured Intelligence Lane

Primary components:

- Document ingestion pipeline
- `AI_PARSE_DOCUMENT` and `AI_EXTRACT` for extraction
- Normalized chunk/entity/fact persistence
- Cortex Search service for retrieval

Usage:

- Policy/explanatory asks use chunk retrieval + citations
- Exact document transaction asks use normalized facts when available
- Extraction diagnostics inform confidence and abstain decisions

### 10.3 Hybrid Intelligence Lane

Primary components:

- Route planner
- Dual-lane execution (structured + unstructured)
- Consistency and conflict policy
- Synthesis with evidence union

Usage:

- Query requires warehouse metrics + document context
- Planner resolves entities and executes both lanes
- Final answer must include both evidence classes where applicable

### 10.4 Cortex Agent Serving Model

Published serving should support:

- Structured-only product serving
- Document-only product serving
- Hybrid serving with route policy

The published agent configuration must explicitly encode:

- Tool/resource bindings
- Routing policy
- Evidence/citation policy
- Abstain policy for weak/conflicting evidence

---

## 11. Neo4j Knowledge Graph Strategy (Hallucination Reduction)

Neo4j is a core grounding system, not just an ERD visualization store.

### 11.1 Why Neo4j is Required

Without graph grounding, hybrid answers degrade into weak similarity-based synthesis.

Neo4j provides:

- Entity resolution across structured and unstructured assets
- Explicit relationship paths for claim validation
- Conflict and staleness tracking
- Requirement-to-model-to-answer traceability

### 11.2 Graph Model (Conceptual)

Core nodes include:

- Data products
- Tables and columns
- Documents and chunks
- Extracted entities and facts
- Requirements, metrics, dimensions
- Semantic artifacts and published agent objects
- Evidence packets and answered questions

Core edges include:

- Structural lineage (`HAS_TABLE`, `HAS_COLUMN`, `FK_REFERENCES`)
- Document provenance (`HAS_DOCUMENT`, `HAS_CHUNK`, `STATES_FACT`)
- Semantic alignment (`SATISFIES_REQUIREMENT`, `DEFINES_METRIC`)
- Cross-source links (`LINKS_TO_ENTITY`, `LINKS_TO_COLUMN`)
- Trust semantics (`CITED_IN`, `CONFLICTS_WITH`, `VERSION_OF`)

### 11.3 Runtime Graph Usage Rules

Before answer finalization:

1. Build a question subgraph for detected business entities/metrics
2. Validate that each material claim has an evidence path
3. Check for conflicting/stale nodes and edges
4. Downgrade confidence or abstain if graph support is insufficient

### 11.4 Hallucination Guardrails via Graph

- No metric claim without requirement and semantic mapping path
- No document claim without provenance path to source chunk/fact
- No hybrid claim without explicit cross-source entity link
- No fallback to unsupported free-text “best guess”

---

## 12. Document and Context Management

### 12.1 Upload and Registration

Documents can enter via:

- Initial product creation
- Chat attachment
- Documents panel

All uploads must register:

- Source metadata
- Version identity
- Extraction status and diagnostics
- Context impact hints

### 12.2 Context Activation

Not every uploaded document must be active for every stage.

ekaiX must support stage-aware context activation states and show:

- Active evidence
- Candidate evidence
- Excluded evidence

### 12.3 Change Impact

On add/update/delete:

- Mark impacted artifacts as stale
- Show targeted rerun options by stage
- Avoid unnecessary full-pipeline resets

---

## 13. UX Requirements (Business Persona First)

### 13.1 Core UX Contract

- One primary conversational thread
- Mission-control stage visibility
- No internal tool/event noise in primary chat

### 13.2 Trust Presentation

Per assistant answer:

- Source mode badge
- Confidence and exactness cue
- Evidence summary
- Expandable evidence drawer
- Recovery action controls when needed

### 13.3 Long-Running Experience

- Human-readable progress narrative
- Optional technical details panel
- No generic “still processing” dead-end language
- Explicit failure state + next action if blocked

### 13.4 Language Rules

- Business labels by default
- Technical internals in “details” only
- Abstain messaging in two clear lines:
  - Why answer is abstained
  - What user can do next

---

## 14. Security, Governance, and Compliance

### 14.1 Data Access and Identity

- Enforce caller-context permissions for data access paths
- Respect source ACLs for documents and retrieval
- Tenant isolation across all stores and APIs

### 14.2 Auditability

Persist per-answer trace:

- Query intent and route plan
- Tool calls and outcomes
- Evidence references used
- Model/version identifiers
- Confidence and trust decision

### 14.3 Compliance Controls

- Retention and deletion workflows
- Legal hold support for documents
- Cross-tenant leakage probes and alerts

---

## 15. Reliability and Error Handling

### 15.1 No-Limbo State Machine

Every operation must emit an explicit lifecycle state:

- Running
- Waiting
- Blocked
- Failed recoverable
- Failed admin
- Recovered
- Completed

### 15.2 Recovery Behavior

On failure, ekaiX must provide actionable options such as:

- Upload/activate missing evidence
- Rerun extraction for specific documents
- Repair entity links
- Refresh source context
- Retry from last stable stage

### 15.3 Operational Observability

- Structured logs with trace IDs
- Planner and tool telemetry
- Trust outcome dashboards
- Chaos checks for alert behavior and isolation violations

---

## 16. Non-Functional Requirements

### Performance

- Fast conversational start for normal operations
- Progressive streaming for long jobs
- Deterministic timeout and retry policies

### Scalability

- Large-schema and large-document-library support
- Concurrent conversations with stable isolation

### Reliability

- High availability and resumable workflows
- Graceful degradation on dependency outages

### Usability

- Business-first writing quality
- Accessible and responsive interface

### Safety

- Hallucination minimization through evidence and graph checks
- Explicit abstention in low-confidence scenarios

---

## 17. Accuracy Strategy and Release Gates

### 17.1 Benchmarking

Maintain domain-specific benchmark sets including:

- Structured-only cases
- Unstructured-only cases
- Hybrid cases
- Conflict and adversarial cases

### 17.2 Mandatory Gates

Release is blocked if thresholds fail on:

- Critical error rate (confident but wrong)
- Exactness correctness for deterministic asks
- Citation validity
- Abstention precision
- Cross-tenant leakage (must be zero)

### 17.3 Product Positioning Constraint

ekaiX can outperform generic LLM chat on enterprise data only if it keeps strict evidence discipline and does not optimize for answering every prompt.

---

## 18. Delivery Roadmap (Conceptual)

### Phase A: Source-Agnostic Foundation

- Remove table-only gating
- Make discovery valid for docs-only, tables-only, hybrid
- Align stage transitions with real completion criteria

### Phase B: Full Cortex + Document Serving

- Harden structured and unstructured lanes
- Ensure publish model supports all three product modes
- Stabilize extraction diagnostics and context-impact handling

### Phase C: Neo4j Grounded Hybrid Intelligence

- Expand graph model to include document and requirement semantics
- Enforce runtime graph checks before answer finalization
- Add contradiction/staleness-aware confidence downgrades

### Phase D: Trust UX and Hardening

- Finalize answer trust UI and evidence interaction
- Eliminate internal noise in user stream
- Complete release-gate automation and compliance checks

---

## 19. Risks and Mitigations

### Risk: Similarity-only retrieval returns plausible but wrong exact values
Mitigation: deterministic-evidence policy and forced abstain on weak evidence.

### Risk: Graph and relational stores diverge
Mitigation: versioned writes, reconciliation jobs, and consistency checks.

### Risk: Operational complexity overwhelms business user trust
Mitigation: business-first UX, strong defaults, and guided recovery actions.

### Risk: Security leakage in hybrid retrieval
Mitigation: strict tenant/ACL filters and continuous chaos tests.

---

## 20. Final Product Statement

ekaiX is not a generic chatbot and not only a semantic-model generator.

It is a governed enterprise intelligence system that:

- Builds semantic intelligence from structured and unstructured sources
- Serves accurate answers through one agentic chat experience
- Uses Snowflake Cortex services for execution
- Uses Neo4j knowledge graph grounding to reduce hallucinations
- Refuses to fabricate certainty when evidence is weak

That is the product we are building.
