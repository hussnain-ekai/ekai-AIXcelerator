#!/usr/bin/env bash
set -euo pipefail

DATA_PRODUCT_ID="${1:-}"
WINDOW_HOURS="${2:-24}"
API_URL="${API_URL:-http://localhost:8000}"
E2E_USER="${E2E_USER:-dev@localhost}"
TRACE_LIMIT="${TRACE_LIMIT:-25}"
ALERT_LIMIT="${ALERT_LIMIT:-100}"

if [[ -z "${DATA_PRODUCT_ID}" ]]; then
  echo "Usage: $0 <data_product_id> [window_hours]" >&2
  exit 1
fi

CHAOS_PROBE_CROSS_TENANT="${CHAOS_PROBE_CROSS_TENANT:-false}"
CHAOS_ALT_DATA_PRODUCT_ID="${CHAOS_ALT_DATA_PRODUCT_ID:-}"
CHAOS_REFERENCE_ID="${CHAOS_REFERENCE_ID:-}"
CHAOS_CITATION_TYPE="${CHAOS_CITATION_TYPE:-sql}"

if [[ "${CHAOS_PROBE_CROSS_TENANT}" == "true" ]]; then
  if [[ -z "${CHAOS_ALT_DATA_PRODUCT_ID}" || -z "${CHAOS_REFERENCE_ID}" ]]; then
    echo "CHAOS_PROBE_CROSS_TENANT=true requires CHAOS_ALT_DATA_PRODUCT_ID and CHAOS_REFERENCE_ID" >&2
    exit 1
  fi
  PROBE_ENDPOINT="${API_URL}/documents/semantic/${CHAOS_ALT_DATA_PRODUCT_ID}/evidence/link?citation_type=${CHAOS_CITATION_TYPE}&reference_id=${CHAOS_REFERENCE_ID}"
  echo "Running cross-tenant probe:"
  echo "  endpoint: ${PROBE_ENDPOINT}"
  curl -sS -o /tmp/hybrid_ops_probe.json -w "  status: %{http_code}\n" \
    -H "Sf-Context-Current-User: ${E2E_USER}" \
    -H "X-Dev-User: ${E2E_USER}" \
    "${PROBE_ENDPOINT}" || true
  sleep 1
fi

ENDPOINT="${API_URL}/documents/semantic/${DATA_PRODUCT_ID}/ops/dashboard?window_hours=${WINDOW_HOURS}&trace_limit=${TRACE_LIMIT}&alert_limit=${ALERT_LIMIT}"
echo "Running hybrid ops chaos check"
echo "  endpoint: ${ENDPOINT}"
echo "  user:     ${E2E_USER}"

RAW_RESPONSE="$(curl -sS \
  -H "Sf-Context-Current-User: ${E2E_USER}" \
  -H "X-Dev-User: ${E2E_USER}" \
  "${ENDPOINT}")"

echo "${RAW_RESPONSE}" | node -e '
const fs = require("fs");
const input = fs.readFileSync(0, "utf8");
let parsed;
try {
  parsed = JSON.parse(input);
} catch (err) {
  console.error("Invalid JSON response:", input);
  process.exit(2);
}

if (!parsed || typeof parsed !== "object") {
  console.error("Ops dashboard response was empty.");
  process.exit(2);
}
if (!parsed.summary || typeof parsed.summary !== "object") {
  console.error("Missing summary object in ops response.");
  process.exit(2);
}
if (!Array.isArray(parsed.recent_traces)) {
  console.error("Missing recent_traces array in ops response.");
  process.exit(2);
}
if (!Array.isArray(parsed.recent_alert_events)) {
  console.error("Missing recent_alert_events array in ops response.");
  process.exit(2);
}

const required = [
  "total_answers",
  "abstained_answers",
  "conflicting_answers",
  "citation_missing_answers",
  "avg_citations",
  "avg_tool_calls",
];
for (const key of required) {
  if (!(key in parsed.summary)) {
    console.error(`Missing summary key: ${key}`);
    process.exit(2);
  }
}

const alerts = Array.isArray(parsed.alerts) ? parsed.alerts : [];
const traceCount = parsed.recent_traces.length;
const alertEventCount = parsed.recent_alert_events.length;
const requireTraces = process.env.CHAOS_REQUIRE_TRACES !== "false";
const requireAlerts = process.env.CHAOS_REQUIRE_ALERTS === "true";
const expectSignal = process.env.CHAOS_EXPECT_SIGNAL || "";

if (requireTraces && traceCount === 0) {
  console.error("Expected recent_traces to contain data but received none.");
  process.exit(3);
}

if (requireAlerts && alerts.length === 0 && alertEventCount === 0) {
  console.error("CHAOS_REQUIRE_ALERTS=true but no alerts were returned.");
  process.exit(3);
}

if (expectSignal) {
  const alertSignals = alerts
    .map((item) => (item && typeof item === "object" ? String(item.signal || "") : ""))
    .filter(Boolean);
  const eventSignals = parsed.recent_alert_events
    .map((item) => (item && typeof item === "object" ? String(item.signal || "") : ""))
    .filter(Boolean);
  const allSignals = new Set([...alertSignals, ...eventSignals]);
  if (!allSignals.has(expectSignal)) {
    console.error(`Expected signal ${expectSignal} not found. Signals: ${Array.from(allSignals).join(", ")}`);
    process.exit(3);
  }
}

console.log(JSON.stringify({
  data_product_id: parsed.data_product_id,
  window_hours: parsed.window_hours,
  summary: parsed.summary,
  alerts_count: alerts.length,
  trace_count: traceCount,
  alert_event_count: alertEventCount,
  generated_at: parsed.generated_at,
}, null, 2));
'

echo "Hybrid ops chaos check passed."
