# ekaiX Hybrid Intelligence Test Matrix (Sprint 1 Baseline)

## Scope
Initial QA matrix for `HYB-QA-001` covering core answer paths:
- Structured-only Q/A.
- Document-only Q/A.
- Hybrid Q/A.
- Failure and recovery behavior.
- Access and isolation behavior.

## Scenario Grid
| ID | Category | Scenario | Expected Result | Priority |
|---|---|---|---|---|
| HYB-TM-001 | Structured | KPI question resolved from semantic model only | `source_mode=structured`, SQL citation present | P0 |
| HYB-TM-002 | Structured | Numeric metric with date filter | exact value from structured query, no hallucinated number | P0 |
| HYB-TM-003 | Document | Policy/procedure answer from document chunks | `source_mode=document`, chunk citations included | P0 |
| HYB-TM-004 | Document | Invoice line lookup from doc facts | exact numeric value from `doc_facts` + page citation | P0 |
| HYB-TM-005 | Document | Invoice lookup with missing field | abstain with explicit recovery action | P0 |
| HYB-TM-006 | Hybrid | Question requiring invoice + warehouse dimension context | `source_mode=hybrid`, both evidence classes included | P0 |
| HYB-TM-007 | Hybrid | Conflicting values across doc and warehouse | conflict surfaced, no silent merge | P0 |
| HYB-TM-008 | Exactness | Fuzzy chunk evidence for exact-number query | `insufficient_evidence`, no guessed number | P0 |
| HYB-TM-009 | Recovery | Extraction failure path | `failed_recoverable` UX state + rerun guidance | P1 |
| HYB-TM-010 | Recovery | Long-running operation with delayed updates | no limbo, progress + details available | P1 |
| HYB-TM-011 | Security | Cross-tenant document access attempt | denied; no leaked metadata/content | P0 |
| HYB-TM-012 | Security | Role-based restricted document query | restricted rows filtered in registry/chunks/facts/evidence APIs | P0 |
| HYB-TM-013 | Context Delta | Document delete affecting requirements/modeling context | stale impact surfaced with targeted rerun options | P1 |
| HYB-TM-014 | UX Trust | Evidence drawer open on answer card | deep links resolve to SQL/doc references | P1 |
| HYB-TM-015 | UX Trust | Abstain messaging clarity | one-line reason + one-line recovery action | P1 |

## Dataset Requirements
- Structured dataset with at least 3 related tables and known KPI answers.
- Document corpus:
  - 3 invoices with overlapping part numbers.
  - 2 policy documents.
  - 1 low-quality OCR scan.
  - 1 intentionally conflicting/stale document.

## Acceptance Gates (Sprint 1)
- All P0 scenarios passing in local/staging.
- No P0 case returns confident wrong exact numeric output.
- No P0 case leaks cross-tenant evidence.
- All abstain flows include at least one actionable recovery step.

## Automation Mapping
- Backend API integration tests: registry/facts/chunks/evidence endpoints.
- AI contract tests: source mode, exactness state, trust state transitions.
- Playwright E2E: evidence visibility, abstain UX, long-running status behavior.
- Benchmark gate (HYB-QA-005):
  - `cd ai-service && venv/bin/python scripts/run_hybrid_benchmark.py --cases .benchmarks/gold_cases_300.jsonl --predictions .benchmarks/gold_predictions_300.json --fail-on-gate`
