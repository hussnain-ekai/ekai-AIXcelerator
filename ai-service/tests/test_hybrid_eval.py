"""Tests for hybrid benchmark scoring utilities."""

from services.hybrid_eval import EvalCase, run_benchmark, score_case


def test_score_case_detects_numeric_exact_match() -> None:
    case = EvalCase(
        case_id="c1",
        category="hybrid",
        question="Exact invoice amount?",
        expected_numbers=[1200.0],
        required_citations=1,
    )
    prediction = {
        "answer_text": "The amount is 1200.",
        "confidence_decision": "high",
        "citations": [{"citation_type": "document_fact", "reference_id": "fact-1"}],
    }
    result = score_case(case, prediction, numeric_tolerance=0.0)
    assert result.correctness == 1.0
    assert result.citation_valid == 1.0
    assert result.critical_error == 0.0


def test_score_case_flags_critical_error_when_confident_and_wrong() -> None:
    case = EvalCase(
        case_id="c2",
        category="structured",
        question="What is GDP?",
        expected_numbers=[999.0],
        required_citations=1,
        allow_abstain=False,
    )
    prediction = {
        "answer_text": "GDP is 1001.",
        "confidence_decision": "high",
        "citations": [],
    }
    result = score_case(case, prediction, numeric_tolerance=0.0)
    assert result.correctness == 0.0
    assert result.critical_error == 1.0


def test_run_benchmark_aggregates_categories_and_missing_predictions() -> None:
    cases = [
        EvalCase(
            case_id="s1",
            category="structured",
            question="Q1",
            expected_answer="ok",
            required_citations=1,
        ),
        EvalCase(
            case_id="d1",
            category="document",
            question="Q2",
            expected_numbers=[10.0],
            required_citations=1,
        ),
    ]
    predictions = {
        "s1": {
            "answer_text": "ok",
            "confidence_decision": "medium",
            "citations": [{"citation_type": "sql", "reference_id": "sql-1"}],
        },
    }
    report = run_benchmark(cases, predictions)
    assert report["cases_total"] == 2
    assert report["missing_predictions"] == ["d1"]
    assert "structured" in report["by_category"]
    assert "document" in report["by_category"]
