"""Unit tests for history trust-contract replay helpers."""

from routers.agent import _build_history_answer_contract


def test_build_history_answer_contract_normalizes_citations() -> None:
    row = {
        "query_id": "session-1:query-1",
        "source_mode": "hybrid",
        "confidence": "high",
        "exactness_state": "validated_exact",
        "final_decision": "answer_ready",
        "sql_refs": [{"reference_id": "sql-1", "label": "SQL"}],
        "fact_refs": [{"id": "fact-42", "label": "Fact row"}],
        "chunk_refs": [{"reference_id": "chunk-7", "citation_type": "document_chunk"}],
        "conflicts": ["none"],
        "recovery_plan": {"actions": [{"action": "rerun", "description": "Rerun extraction"}]},
        "created_at": "2026-02-24T00:00:00Z",
    }

    contract = _build_history_answer_contract(row)
    assert contract["source_mode"] == "hybrid"
    assert contract["confidence_decision"] == "high"
    assert contract["exactness_state"] == "validated_exact"
    assert contract["trust_state"] == "answer_ready"
    assert len(contract["citations"]) == 3
    assert contract["citations"][0]["citation_type"] == "sql"
    assert contract["citations"][1]["citation_type"] == "document_fact"
    assert contract["citations"][1]["reference_id"] == "fact-42"
    assert contract["metadata"]["query_id"] == "session-1:query-1"


def test_build_history_answer_contract_sets_abstained_summary() -> None:
    row = {
        "query_id": "session-2:query-2",
        "source_mode": "document",
        "confidence": "abstain",
        "exactness_state": "insufficient_evidence",
        "final_decision": "abstained_missing_evidence",
        "sql_refs": [],
        "fact_refs": [],
        "chunk_refs": [],
        "conflicts": [],
        "recovery_plan": {"actions": []},
        "created_at": "2026-02-24T00:00:00Z",
    }

    contract = _build_history_answer_contract(row)
    assert contract["trust_state"] == "abstained_missing_evidence"
    assert "abstained" in contract["evidence_summary"].lower()
