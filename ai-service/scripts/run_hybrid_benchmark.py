#!/usr/bin/env python3
"""Run hybrid benchmark scoring and emit release-gate report (HYB-AI-007)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SERVICE_ROOT = SCRIPT_DIR.parent
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from services.hybrid_eval import load_cases_jsonl, load_predictions_json, run_benchmark


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score hybrid Q/A benchmark predictions.")
    parser.add_argument("--cases", required=True, help="Path to benchmark cases JSONL file.")
    parser.add_argument(
        "--predictions",
        required=True,
        help="Path to prediction JSON (map or list keyed by case_id).",
    )
    parser.add_argument("--out", help="Optional output report JSON path.")
    parser.add_argument(
        "--numeric-tolerance",
        type=float,
        default=0.0,
        help="Absolute tolerance for numeric expected values (default: 0).",
    )
    parser.add_argument(
        "--fail-on-gate",
        action="store_true",
        help="Exit non-zero when release gate fails.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    cases = load_cases_jsonl(args.cases)
    if not cases:
        parser.error("No valid benchmark cases were loaded.")

    predictions = load_predictions_json(args.predictions)
    report = run_benchmark(cases, predictions, numeric_tolerance=float(args.numeric_tolerance))

    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)

    if args.out:
        output_path = Path(args.out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")

    if args.fail_on_gate and not bool(report.get("release_gate_passed")):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
