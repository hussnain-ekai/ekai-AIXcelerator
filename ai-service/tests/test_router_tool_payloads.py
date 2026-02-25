"""Unit tests for router tool-result helpers."""

from routers.agent import (
    _build_answer_contract_payload,
    _build_query_route_plan,
    _classify_query_intent,
    _coerce_tool_result_payload,
    _extract_tool_error_from_payload,
    _is_analysis_only_no_publish_intent,
    _is_internal_reasoning_leak,
    _is_non_fatal_publish_tool_error,
    _is_tool_payload_success,
)


def test_coerce_tool_result_payload_parses_json_string() -> None:
    payload = _coerce_tool_result_payload('{"status":"success","value":1}')
    assert payload == {"status": "success", "value": 1}


def test_coerce_tool_result_payload_rejects_non_json() -> None:
    assert _coerce_tool_result_payload("not-json") is None


def test_extract_tool_error_prefers_error_field() -> None:
    err = _extract_tool_error_from_payload({"error": "SQL compilation error"})
    assert err == "SQL compilation error"


def test_extract_tool_error_from_failed_status() -> None:
    err = _extract_tool_error_from_payload({"status": "failed", "message": "deployment failed"})
    assert err == "deployment failed"


def test_is_tool_payload_success_accepts_success_statuses() -> None:
    assert _is_tool_payload_success({"status": "success"}) is True
    assert _is_tool_payload_success({"status": "ok"}) is True
    assert _is_tool_payload_success({"status": "completed"}) is True
    assert _is_tool_payload_success({"status": "failed"}) is False


def test_internal_reasoning_leak_blocks_orchestration_phrase() -> None:
    assert _is_internal_reasoning_leak("Execution status from orchestration events")


def test_analysis_only_no_publish_intent_detection() -> None:
    assert _is_analysis_only_no_publish_intent(
        "Do not execute any publishing tools. Stay in analysis mode only and answer now with citations."
    )
    assert not _is_analysis_only_no_publish_intent("Publish now and proceed.")


def test_non_fatal_publish_error_for_grant_access() -> None:
    assert _is_non_fatal_publish_tool_error(
        tool_name="grant_agent_access",
        error_text="SQL compilation error: schema does not exist",
        publish_completed=True,
    )
    assert not _is_non_fatal_publish_tool_error(
        tool_name="create_semantic_view",
        error_text="validation failed",
        publish_completed=False,
    )


def test_build_answer_contract_uses_tool_hint_for_exact_document_fact() -> None:
    payload = _build_answer_contract_payload(
        phase="explorer",
        assistant_text="The invoice total is 1250.",
        failure_message=None,
        last_tool="query_document_facts",
        tool_contract_hints=[
            {
                "source_mode": "document",
                "exactness_state": "validated_exact",
                "confidence_decision": "high",
                "trust_state": "answer_ready",
                "evidence_summary": "Deterministic document facts were found.",
                "citations": [
                    {
                        "citation_type": "document_fact",
                        "reference_id": "fact-1",
                        "label": "invoice.pdf (page 2)",
                        "page": 2,
                        "score": 0.91,
                    }
                ],
            }
        ],
    )
    assert payload["source_mode"] == "document"
    assert payload["exactness_state"] == "validated_exact"
    assert payload["confidence_decision"] == "high"
    assert payload["trust_state"] == "answer_ready"
    assert len(payload["citations"]) == 1
    assert payload["citations"][0]["citation_type"] == "document_fact"


def test_build_answer_contract_merges_document_and_structured_hints_to_hybrid() -> None:
    payload = _build_answer_contract_payload(
        phase="explorer",
        assistant_text="Hybrid answer",
        failure_message=None,
        last_tool="execute_rcr_query",
        tool_contract_hints=[
            {
                "source_mode": "document",
                "exactness_state": "not_applicable",
                "confidence_decision": "medium",
                "trust_state": "answer_ready",
                "citations": [
                    {
                        "citation_type": "document_chunk",
                        "reference_id": "chunk-1",
                        "label": "report.pdf (page 1)",
                    }
                ],
            },
            {
                "source_mode": "structured",
                "exactness_state": "not_applicable",
                "confidence_decision": "medium",
                "trust_state": "answer_ready",
                "citations": [
                    {
                        "citation_type": "sql",
                        "reference_id": "sql-a1",
                        "label": "Structured query result",
                    }
                ],
            },
        ],
    )
    assert payload["source_mode"] == "hybrid"
    assert len(payload["citations"]) == 2


def test_classify_query_intent_transaction_lookup() -> None:
    intent, rationale = _classify_query_intent(
        "What is the exact invoice number and amount for this spare part purchase?"
    )
    assert intent == "transaction_lookup"
    assert "transactional" in rationale.lower() or "identifier" in rationale.lower()


def test_classify_query_intent_hybrid() -> None:
    intent, _ = _classify_query_intent(
        "Which countries saw GDP decline while the outlook report mentions infrastructure growth?"
    )
    assert intent == "hybrid"


def test_build_query_route_plan_for_hybrid_question() -> None:
    plan = _build_query_route_plan(
        "Which countries saw GDP decline while the outlook report mentions infrastructure growth?",
        current_phase="explorer",
        already_published=True,
    )
    assert plan["intent"] == "hybrid"
    assert "document_facts" in plan["lanes"]
    assert "document_chunks" in plan["lanes"]
    assert "structured_agent" in plan["lanes"]
    assert plan["version"] == "hyb-ai-003-v1"


def test_exactness_guardrail_abstains_without_deterministic_citations() -> None:
    payload = _build_answer_contract_payload(
        phase="explorer",
        assistant_text="The exact amount is 1200.",
        failure_message=None,
        last_tool="search_document_chunks",
        tool_contract_hints=[
            {
                "source_mode": "document",
                "exactness_state": "validated_exact",
                "confidence_decision": "high",
                "trust_state": "answer_ready",
                "evidence_summary": "Found amount mentions in text snippets.",
                "citations": [
                    {
                        "citation_type": "document_chunk",
                        "reference_id": "chunk-001",
                        "label": "invoice-notes.pdf (page 1)",
                    }
                ],
            }
        ],
        query_route_plan={
            "intent": "transaction_lookup",
            "requires_exact_evidence": True,
        },
    )
    assert payload["exactness_state"] == "insufficient_evidence"
    assert payload["confidence_decision"] == "abstain"
    assert payload["trust_state"] == "abstained_missing_evidence"
    assert any(
        action["action"] == "provide_deterministic_source" for action in payload["recovery_actions"]
    )


def test_exactness_guardrail_allows_document_fact_citations() -> None:
    payload = _build_answer_contract_payload(
        phase="explorer",
        assistant_text="The exact amount is 1200.",
        failure_message=None,
        last_tool="query_document_facts",
        tool_contract_hints=[
            {
                "source_mode": "document",
                "exactness_state": "validated_exact",
                "confidence_decision": "high",
                "trust_state": "answer_ready",
                "evidence_summary": "Deterministic facts were found.",
                "citations": [
                    {
                        "citation_type": "document_fact",
                        "reference_id": "fact-123",
                        "label": "invoice.pdf (page 2)",
                    }
                ],
            }
        ],
        query_route_plan={
            "intent": "transaction_lookup",
            "requires_exact_evidence": True,
        },
    )
    assert payload["exactness_state"] == "validated_exact"
    assert payload["confidence_decision"] == "high"
    assert payload["trust_state"] == "answer_ready"
