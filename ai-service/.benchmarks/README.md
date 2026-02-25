# Hybrid Benchmark Harness

Run benchmark scoring:

```bash
cd ai-service
venv/bin/python scripts/run_hybrid_benchmark.py \
  --cases .benchmarks/gold_cases_300.jsonl \
  --predictions .benchmarks/gold_predictions_300.json \
  --out .benchmarks/sample_report.json \
  --fail-on-gate
```

Primary release-gate corpus:
- `.benchmarks/gold_cases_300.jsonl` (300 cases: structured/document/hybrid)
- `.benchmarks/gold_predictions_300.json` (reference predictions for pipeline verification)

Input formats:
- `sample_cases.jsonl`: one JSON object per line (`case_id`, `category`, `question`, expected outputs).
- `sample_predictions.json`: map or list keyed by `case_id` with `answer_text`, `confidence_decision`, and `citations`.

Release gate behavior:
- Script exits with status `2` when `critical_error_rate > 0.02` and `--fail-on-gate` is set.
