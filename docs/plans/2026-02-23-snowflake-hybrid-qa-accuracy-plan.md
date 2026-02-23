# ekaiX Snowflake Hybrid Q/A Accuracy Plan (Feb 23, 2026)

## Objective
Build ekaiX so enterprise users get reliably correct answers from:
1. Structured warehouse data (semantic models).
2. Unstructured documents (SharePoint, Office, PDFs, email exports, uploads).
3. Hybrid questions that require both.

Accuracy is a product requirement, not a best-effort outcome.

## What "More Accurate Than Generic Assistants" Should Mean
Do not claim absolute superiority over ChatGPT/Claude/Notion in general.  
Do target higher accuracy on customer-specific data by enforcing grounding:

- Every answer must be traceable to SQL result rows, cited document chunks, or both.
- Numeric answers come from structured query results only (not free-form generation).
- If evidence is insufficient or conflicting, the agent must abstain and return a recovery path.

## Snowflake-Native Architecture
### Structured lane (warehouse truth)
- Cortex Analyst over Snowflake semantic views for business Q/A.
- ekaiX keeps semantic models aligned to mission-control artifacts (BRD, metrics, dimensions, joins).

### Unstructured lane (document truth)
- Ingest docs via direct upload and SharePoint connector (Openflow/connector path).
- Parse and extract with `AI_PARSE_DOCUMENT` and `AI_EXTRACT`.
- Build searchable chunk index with Cortex Search, with document metadata and ACL attributes.

### Hybrid lane (combined truth)
- Cortex Agents route across Analyst (structured) + Search (unstructured).
- Supervisor enforces answer assembly policy:
  - Structured facts: SQL-grounded.
  - Policy/context facts: document-grounded with citations.
  - Final response includes source list and confidence rationale.

## Accuracy Contract (No-Limbo + No-Hallucination Bias)
- Explicit execution states: running, waiting, blocked, failed, recovered.
- On failure, return: what failed, impact, and next best recovery action.
- Evidence-first response policy:
  - No evidence -> no definitive answer.
  - Conflicting evidence -> show conflict and request user decision or rerun scope.
- Context-delta policy for document add/delete:
  - Mark affected artifacts and prompt targeted rerun options.

## Evaluation and Benchmarking
Create a recurring benchmark pack per customer domain:

- 250-500 questions split across structured, unstructured, and hybrid.
- Include hard cases: synonyms, partial docs, stale docs, policy edge cases, conflicting definitions.
- Score each system (ekaiX, ChatGPT, Claude, Notion) on:
  - Answer correctness (business-judge rubric).
  - Numeric exactness (tolerance-based for metrics).
  - Citation validity (supports claim or not).
  - Abstention quality (correctly says "insufficient evidence").
  - Critical error rate (confident wrong answers).

Go-live gate: approve only when critical error rate is near-zero and abstention behavior is reliable.

## Implementation Phases
1. Foundation (week 1): unify document registry, metadata, ACL fields, and evidence schema.
2. Unstructured Q/A (week 2): retrieval + citation response path in supervisor.
3. Hybrid Q/A (week 3): dual-tool plans (Analyst + Search) with deterministic synthesis rules.
4. Hardening (weeks 4-6): eval harness, regression automation, failure recovery UX, audit traces.

## Immediate Repo Actions
- Backend: add dedicated unstructured/hybrid answer endpoints and evidence persistence.
- AI service: enforce evidence-aware answer policy in supervisor orchestration.
- Frontend: show source-backed answers, uncertainty states, and rerun guidance.

## References
- Cortex Agents: https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents
- Cortex Search query/filter model: https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-search/query-cortex-search-service
- Cortex Analyst semantic models: https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/semantic-model-spec
- AI_EXTRACT: https://docs.snowflake.com/en/sql-reference/functions/ai_extract
- AI_PARSE_DOCUMENT image extraction update (Jan 26, 2026): https://docs.snowflake.com/en/release-notes/2026/other/2026-01-26-ai-parse-document-images-preview
- Openflow SharePoint connector: https://docs.snowflake.com/en/user-guide/data-integration/openflow/connectors/sharepoint/about
