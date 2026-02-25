"""Hybrid Q/A benchmark scoring utilities (HYB-AI-007)."""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _extract_numbers(value: str) -> list[float]:
    nums: list[float] = []
    for match in re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", value or ""):
        try:
            nums.append(float(match.replace(",", "")))
        except ValueError:
            continue
    return nums


def _float_close(left: float, right: float, tolerance: float) -> bool:
    return math.isfinite(left) and math.isfinite(right) and abs(left - right) <= tolerance


@dataclass
class EvalCase:
    case_id: str
    category: str
    question: str
    expected_answer: str | None = None
    expected_numbers: list[float] | None = None
    required_citations: int = 0
    allow_abstain: bool = False


@dataclass
class CaseResult:
    case_id: str
    category: str
    correctness: float
    citation_valid: float
    abstain_valid: float
    critical_error: float


def score_case(
    case: EvalCase,
    prediction: dict[str, Any],
    *,
    numeric_tolerance: float = 0.0,
) -> CaseResult:
    """Score one benchmark case against a normalized model prediction."""
    answer_text = str(prediction.get("answer_text") or "")
    confidence = str(prediction.get("confidence_decision") or "").lower()
    citations = prediction.get("citations")
    citation_count = len(citations) if isinstance(citations, list) else 0

    expected_numbers = case.expected_numbers or []
    predicted_numbers = _extract_numbers(answer_text)

    if expected_numbers:
        has_match = any(
            _float_close(pred, exp, numeric_tolerance)
            for pred in predicted_numbers
            for exp in expected_numbers
        )
        correctness = 1.0 if has_match else 0.0
    elif case.expected_answer:
        normalized_expected = _normalize_text(case.expected_answer)
        normalized_answer = _normalize_text(answer_text)
        correctness = (
            1.0 if normalized_expected and normalized_expected in normalized_answer else 0.0
        )
    else:
        correctness = 1.0

    citation_valid = 1.0 if citation_count >= max(0, case.required_citations) else 0.0

    is_abstain = confidence == "abstain"
    abstain_valid = 1.0 if (not is_abstain or case.allow_abstain) else 0.0

    critical_error = 0.0
    if not case.allow_abstain and confidence != "abstain" and correctness < 1.0:
        critical_error = 1.0

    return CaseResult(
        case_id=case.case_id,
        category=case.category,
        correctness=correctness,
        citation_valid=citation_valid,
        abstain_valid=abstain_valid,
        critical_error=critical_error,
    )


def aggregate_results(results: list[CaseResult]) -> dict[str, Any]:
    """Aggregate benchmark scores globally and by category."""
    grouped: dict[str, list[CaseResult]] = defaultdict(list)
    for result in results:
        grouped[result.category].append(result)

    def _summary(rows: list[CaseResult]) -> dict[str, float]:
        if not rows:
            return {
                "count": 0.0,
                "correctness": 0.0,
                "citation_validity": 0.0,
                "abstain_validity": 0.0,
                "critical_error_rate": 0.0,
            }
        count = float(len(rows))
        return {
            "count": count,
            "correctness": sum(row.correctness for row in rows) / count,
            "citation_validity": sum(row.citation_valid for row in rows) / count,
            "abstain_validity": sum(row.abstain_valid for row in rows) / count,
            "critical_error_rate": sum(row.critical_error for row in rows) / count,
        }

    by_category = {category: _summary(rows) for category, rows in grouped.items()}
    overall = _summary(results)
    release_gate_passed = overall["critical_error_rate"] <= 0.02

    return {
        "overall": overall,
        "by_category": by_category,
        "release_gate_passed": release_gate_passed,
    }


def load_cases_jsonl(path: str | Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            cases.append(
                EvalCase(
                    case_id=str(payload.get("case_id") or payload.get("id") or ""),
                    category=str(payload.get("category") or "unknown"),
                    question=str(payload.get("question") or ""),
                    expected_answer=(
                        str(payload.get("expected_answer"))
                        if payload.get("expected_answer") is not None
                        else None
                    ),
                    expected_numbers=[
                        float(value)
                        for value in (payload.get("expected_numbers") or [])
                        if value is not None
                    ]
                    or None,
                    required_citations=int(payload.get("required_citations") or 0),
                    allow_abstain=bool(payload.get("allow_abstain") or False),
                )
            )
    return [case for case in cases if case.case_id]


def load_predictions_json(path: str | Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return {
            str(case_id): value for case_id, value in payload.items() if isinstance(value, dict)
        }
    if isinstance(payload, list):
        mapped: dict[str, dict[str, Any]] = {}
        for row in payload:
            if not isinstance(row, dict):
                continue
            case_id = str(row.get("case_id") or row.get("id") or "").strip()
            if not case_id:
                continue
            mapped[case_id] = row
        return mapped
    return {}


def run_benchmark(
    cases: list[EvalCase],
    predictions: dict[str, dict[str, Any]],
    *,
    numeric_tolerance: float = 0.0,
) -> dict[str, Any]:
    """Score all cases and return report payload."""
    results: list[CaseResult] = []
    missing_case_ids: list[str] = []

    for case in cases:
        prediction = predictions.get(case.case_id)
        if not prediction:
            missing_case_ids.append(case.case_id)
            prediction = {"answer_text": "", "confidence_decision": "abstain", "citations": []}
        results.append(score_case(case, prediction, numeric_tolerance=numeric_tolerance))

    report = aggregate_results(results)
    report["cases_total"] = len(cases)
    report["missing_predictions"] = missing_case_ids
    report["results"] = [result.__dict__ for result in results]
    return report
