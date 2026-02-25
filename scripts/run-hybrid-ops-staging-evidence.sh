#!/usr/bin/env bash
set -euo pipefail

DATA_PRODUCT_ID="${1:-}"
WINDOW_HOURS="${2:-24}"
EXPECT_SIGNAL="${3:-citation_missing_answers}"

if [[ -z "${DATA_PRODUCT_ID}" ]]; then
  echo "Usage: $0 <data_product_id> [window_hours] [expect_signal]" >&2
  exit 1
fi

STAGING_API_URL="${STAGING_API_URL:-${API_URL:-}}"
STAGING_E2E_USER="${STAGING_E2E_USER:-${E2E_USER:-dev@localhost}}"

if [[ -z "${STAGING_API_URL}" ]]; then
  echo "STAGING_API_URL (or API_URL) must be set for staging chaos checks." >&2
  exit 1
fi

OUT_DIR="docs/evidence/hybrid-ops"
mkdir -p "${OUT_DIR}"
STAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
CHAOS_TXT="${OUT_DIR}/${STAMP}-staging-ops-chaos-check.txt"
DASH_JSON="${OUT_DIR}/${STAMP}-staging-ops-dashboard.json"

echo "Running staging hybrid ops chaos check"
echo "  api_url: ${STAGING_API_URL}"
echo "  user:    ${STAGING_E2E_USER}"
echo "  product: ${DATA_PRODUCT_ID}"
echo "  signal:  ${EXPECT_SIGNAL}"

API_URL="${STAGING_API_URL}" \
E2E_USER="${STAGING_E2E_USER}" \
CHAOS_REQUIRE_ALERTS=true \
CHAOS_REQUIRE_TRACES=true \
CHAOS_EXPECT_SIGNAL="${EXPECT_SIGNAL}" \
./scripts/run-hybrid-ops-chaos-check.sh "${DATA_PRODUCT_ID}" "${WINDOW_HOURS}" \
  | tee "${CHAOS_TXT}"

curl -sS \
  -H "Sf-Context-Current-User: ${STAGING_E2E_USER}" \
  -H "X-Dev-User: ${STAGING_E2E_USER}" \
  "${STAGING_API_URL}/documents/semantic/${DATA_PRODUCT_ID}/ops/dashboard?window_hours=${WINDOW_HOURS}&trace_limit=25&alert_limit=100" \
  | jq --arg captured_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '. + {captured_at_utc: $captured_at}' \
  > "${DASH_JSON}"

echo "Staging ops evidence written:"
echo "  ${CHAOS_TXT}"
echo "  ${DASH_JSON}"
