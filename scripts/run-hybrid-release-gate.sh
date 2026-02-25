#!/usr/bin/env bash
set -euo pipefail

CASES_PATH="${1:-ai-service/.benchmarks/gold_cases_300.jsonl}"
PREDICTIONS_PATH="${2:-ai-service/.benchmarks/gold_predictions_300.json}"
OUT_PATH="${3:-ai-service/.benchmarks/latest_report.json}"

if [[ ! -f "${CASES_PATH}" ]]; then
  echo "Cases file not found: ${CASES_PATH}" >&2
  exit 1
fi

if [[ ! -f "${PREDICTIONS_PATH}" ]]; then
  echo "Predictions file not found: ${PREDICTIONS_PATH}" >&2
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "${PYTHON_BIN}" ]]; then
  if [[ -x "ai-service/venv/bin/python" ]]; then
    PYTHON_BIN="ai-service/venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

echo "Running hybrid release gate"
echo "  cases:       ${CASES_PATH}"
echo "  predictions: ${PREDICTIONS_PATH}"
echo "  out:         ${OUT_PATH}"

"${PYTHON_BIN}" ai-service/scripts/run_hybrid_benchmark.py \
  --cases "${CASES_PATH}" \
  --predictions "${PREDICTIONS_PATH}" \
  --out "${OUT_PATH}" \
  --fail-on-gate

echo "Hybrid release gate passed."
