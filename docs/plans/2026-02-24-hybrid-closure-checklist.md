# Hybrid Closure Checklist (2026-02-24)

## Goal
Close all outstanding hybrid plan gaps with verifiable evidence (code + tests + ops artifacts), and stop status-only reporting.

## Checklist

- [x] `HYB-UI-002` per-answer trust metadata is persisted/rendered for live answers.
  - Evidence: `frontend/src/stores/chatStore.ts`, `frontend/src/components/chat/MessageThread.tsx`

- [x] `HYB-UI-002` per-answer trust metadata is preserved on history/session recovery.
  - Evidence: `ai-service/routers/agent.py`, `frontend/src/hooks/useSessionRecovery.ts`, `ai-service/tests/test_router_history_contracts.py`

- [x] `HYB-UI-002` includes recency indicator on trust card.
  - Evidence: `frontend/src/components/chat/MessageThread.tsx` (`formatRecencyLabel`)

- [x] Discovery no longer stalls before requirements when readiness conditions are met.
  - Evidence: `ai-service/routers/agent.py` (discovery auto-transition gate + regression suppression)
  - Runtime proof: `docs/evidence/hybrid-ops/2026-02-24-discovery-phase-transition-check.txt`

- [x] `HYB-QA-004` trust UX suite passes on desktop and mobile.
  - Evidence: `frontend/e2e/hybrid-trust.spec.ts` (8/8 pass)

- [x] `HYB-OPS-001` ops dashboard exposes traces and recent alert events.
  - Evidence: `backend/src/routes/documents.ts` (`/semantic/:dataProductId/ops/dashboard`)

- [x] `HYB-OPS-002` citation-missing alert signal validated through strict chaos check.
  - Evidence: `docs/evidence/hybrid-ops/2026-02-24-ops-chaos-check.txt`

- [x] `HYB-OPS-002` cross-tenant query violation alert path validated.
  - Evidence: strict chaos run with `CHAOS_PROBE_CROSS_TENANT=true` and `CHAOS_EXPECT_SIGNAL=cross_tenant_query_violation`

- [x] Staging chaos check is automated and evidence is artifacted for release ops.
  - Evidence: `scripts/run-hybrid-ops-staging-evidence.sh`, `.github/workflows/hybrid-ops-staging-chaos.yml`
  - Local runner validation artifact: `docs/evidence/hybrid-ops/2026-02-24T09-15-31Z-staging-ops-chaos-check.txt`

## Validation Commands (Run)

- `cd ai-service && ./venv/bin/python -m pytest -q`
- `cd backend && npm run typecheck && npm test -- --run`
- `cd frontend && npx tsc --noEmit`
- `cd frontend && npm test -- --run src/stores/chatStore.test.ts`
- `cd frontend && npm run test:e2e -- e2e/hybrid-trust.spec.ts --reporter=line`

## Notes

- Staging execution now has a single command contract:
  - `scripts/run-hybrid-ops-staging-evidence.sh <data_product_id> [window_hours] [expect_signal]`
- Workflow dispatch requires:
  - `STAGING_API_URL` secret
  - `STAGING_E2E_USER` secret
