# Hybrid QA Execution Status (2026-02-24)

All tickets in `docs/plans/2026-02-23-hybrid-qa-implementation-tickets.md` are now implemented in codebase scope.

## Completed by Track

- `DATA` (`HYB-DATA-001`..`005`)
  - Document semantic schema, versioning/soft-delete semantics, search views, fact-link persistence, governance controls.
  - Files: `scripts/migrate-hybrid-document-semantic-layer.sql`, `scripts/migrate-hybrid-doc-search-views.sql`, `scripts/migrate-hybrid-governance-controls.sql`, `backend/src/routes/documents.ts`.

- `AI` (`HYB-AI-001`..`007`)
  - Unified answer contract, extraction quality/provenance flow, hybrid route planner, exactness/conflict guardrails, no-limbo lifecycle, benchmark harness.
  - Files: `ai-service/routers/agent.py`, `ai-service/tools/postgres_tools.py`, `ai-service/models/schemas.py`, `ai-service/services/hybrid_eval.py`, `ai-service/scripts/run_hybrid_benchmark.py`.

- `API` (`HYB-API-001`..`005`)
  - Semantic layer endpoints, context delta/stale artifacts, normalized answer envelope in API + SSE status events, evidence deep links, audit trace APIs.
  - Files: `backend/src/routes/documents.ts`, `backend/src/routes/agent.ts`, `backend/src/schemas/document.ts`.

- `UI` (`HYB-UI-001`..`007`)
  - UX state handling, trust/evidence card (flagged), exactness/recovery rendering, document panel context impact badges, long-running progress + details UX, reduced duplicate status noise.
  - Files: `frontend/src/components/chat/MessageThread.tsx`, `frontend/src/hooks/useAgent.ts`, `frontend/src/components/panels/DocumentsPanel.tsx`, `frontend/src/app/data-products/[id]/page.tsx`.

- `QA` (`HYB-QA-001`..`005`)
  - Matrix + coverage: extraction regressions, exactness guardrail tests, Playwright trust/progress suite (desktop+mobile projects), benchmark release-gate automation.
  - Files: `docs/plans/2026-02-23-hybrid-qa-test-matrix.md`, `backend/src/services/documentExtractionService.test.ts`, `backend/src/routes/agent.test.ts`, `ai-service/tests/test_router_tool_payloads.py`, `frontend/e2e/hybrid-trust.spec.ts`, `frontend/playwright.config.ts`, `scripts/run-hybrid-release-gate.sh`, `.github/workflows/hybrid-release-gate.yml`.

- `OPS` (`HYB-OPS-001`..`003`)
  - Planner/tool/evidence trace capture, persisted `ops_alert_events`, dashboard endpoint for traces + alerts, rollout feature flags + playbook.
  - Files: `ai-service/routers/agent.py`, `backend/src/routes/agent.ts`, `backend/src/routes/documents.ts`, `backend/src/schemas/document.ts`, `scripts/migrate-hybrid-ops-alert-events.sql`, `scripts/run-hybrid-ops-chaos-check.sh`, `docs/plans/2026-02-23-hybrid-rollout-playbook.md`.

## Verification

- `cd ai-service && venv/bin/python -m pytest -q` -> passed (`42 passed`).
- `cd backend && npm run typecheck` -> passed.
- `cd backend && npm test -- --run` -> passed (`8 passed`).
- `cd frontend && npm run test -- --run src/stores/chatStore.test.ts` -> passed (`2 passed`).
- `cd frontend && npm run test:e2e -- e2e/hybrid-trust.spec.ts --reporter=line` -> passed (`8 passed` desktop + mobile).
- `scripts/run-hybrid-release-gate.sh` -> passed (release gate status `true` on 300-case default corpus).
- `scripts/run-hybrid-ops-chaos-check.sh <data_product_id> 24` -> passed (dashboard payload includes summary, traces, and alert events).

## Closure of Previously Open Gaps

- HYB-UI-002 strict requirement:
  - Trust contract now persists on each assistant message and renders inline per message, not latest-only state.
  - Files: `frontend/src/stores/chatStore.ts`, `frontend/src/hooks/useAgent.ts`, `frontend/src/components/chat/MessageThread.tsx`, `frontend/src/stores/chatStore.test.ts`.
  - History replay now preserves per-answer trust contracts after refresh:
    - `ai-service/routers/agent.py`
    - `frontend/src/hooks/useSessionRecovery.ts`
    - `ai-service/tests/test_router_history_contracts.py`
  - Trust card now includes recency indicator for user-visible freshness cues.
  - Discovery progression guard:
    - Auto-transition to requirements when discovery readiness is satisfied.
    - Suppress noisy regression from requirements back to discovery during the same run.
    - Runtime evidence: `docs/evidence/hybrid-ops/2026-02-24-discovery-phase-transition-check.txt`.

- HYB-OPS-001 / HYB-OPS-002 evidence capture:
  - Dashboard and chaos-check proof artifacts captured under:
    - `docs/evidence/hybrid-ops/2026-02-24-ops-dashboard-check.json`
    - `docs/evidence/hybrid-ops/2026-02-24-ops-chaos-check.txt`
    - `docs/evidence/hybrid-ops/2026-02-24-ops-chaos-cross-tenant.txt`
    - `docs/evidence/hybrid-ops/2026-02-24T09-15-31Z-staging-ops-chaos-check.txt`
    - `docs/evidence/hybrid-ops/2026-02-24T09-15-31Z-staging-ops-dashboard.json`
  - Strict chaos assertion used:
    - `CHAOS_REQUIRE_ALERTS=true CHAOS_REQUIRE_TRACES=true CHAOS_EXPECT_SIGNAL=citation_missing_answers ./scripts/run-hybrid-ops-chaos-check.sh <data_product_id> 24`
    - `CHAOS_PROBE_CROSS_TENANT=true CHAOS_EXPECT_SIGNAL=cross_tenant_query_violation ./scripts/run-hybrid-ops-chaos-check.sh <data_product_id> 24`
  - Staging evidence automation:
    - `scripts/run-hybrid-ops-staging-evidence.sh`
    - `.github/workflows/hybrid-ops-staging-chaos.yml`

## Single Tracking Checklist

- Canonical closure tracker: `docs/plans/2026-02-24-hybrid-closure-checklist.md`
