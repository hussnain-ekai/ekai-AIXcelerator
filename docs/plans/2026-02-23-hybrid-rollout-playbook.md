# Hybrid Intelligence Rollout Playbook (HYB-OPS-003)

## Feature Flags
- `feature_hybrid_planner` (ai-service): enables intent/route planning.
- `feature_exactness_guardrail` (ai-service): enforces deterministic exact-value policy.
- `feature_trust_ux_contract` (ai-service): emits answer-contract payloads to SSE.
- `NEXT_PUBLIC_TRUST_UX_ENABLED` (frontend): renders trust/evidence card.

## Canary Strategy
1. Stage 0 (shadow): enable planner + guardrail in staging only, trust UX off.
2. Stage 1 (internal): enable all flags for internal users and validate citation/open-evidence workflows.
3. Stage 2 (5% tenants): enable flags for selected customer data products; monitor critical error rate + citation-missing alerts.
4. Stage 3 (25% tenants): expand after 72h stability and no P0 regressions.
5. Stage 4 (100%): full rollout when benchmark gate passes and audit traces are complete.

## Rollback Controls
- Disable in this order for minimal blast radius:
  1. `NEXT_PUBLIC_TRUST_UX_ENABLED=false`
  2. `feature_trust_ux_contract=false`
  3. `feature_hybrid_planner=false`
  4. `feature_exactness_guardrail=false` (last resort only)
- Restart affected services after flag changes.
- Preserve `qa_evidence` and `doc_governance_audit` records for postmortem.
- Preserve `ops_alert_events` records for postmortem.

## Release Gate
- Block release when:
  - benchmark `critical_error_rate > 0.02`
  - citation-missing alerts persist >30 minutes
  - cross-tenant data leakage test fails
- Automation:
  - local: `scripts/run-hybrid-release-gate.sh [cases.jsonl] [predictions.json] [report.json]`
  - CI: `.github/workflows/hybrid-release-gate.yml` (fails on gate breach)

## Verification Checklist
- Route plan present in `qa_evidence.tool_calls`.
- Model hash present in audit payload.
- Exact-value asks without SQL/doc-fact evidence return `insufficient_evidence`.
- Evidence deep links resolve to SQL/doc chunk/doc fact context.
- Ops summary endpoint returns alert metrics:
  - `GET /documents/semantic/:dataProductId/ops/summary`
- Ops dashboard endpoint returns traces + alert events:
  - `GET /documents/semantic/:dataProductId/ops/dashboard`
- Ensure ops alert schema exists:
  - `psql "$DATABASE_URL" -f scripts/migrate-hybrid-ops-alert-events.sql`
- Staging chaos check command:
  - `scripts/run-hybrid-ops-chaos-check.sh <data_product_id> 24`
  - strict alerts mode: `CHAOS_REQUIRE_ALERTS=true CHAOS_REQUIRE_TRACES=true ...`
  - optional signal assertion: `CHAOS_EXPECT_SIGNAL=citation_missing_answers ...`
  - evidence capture helper: `scripts/run-hybrid-ops-staging-evidence.sh <data_product_id> 24 citation_missing_answers`
  - CI/manual workflow: `.github/workflows/hybrid-ops-staging-chaos.yml` (uploads `docs/evidence/hybrid-ops/*staging-ops-*`)
