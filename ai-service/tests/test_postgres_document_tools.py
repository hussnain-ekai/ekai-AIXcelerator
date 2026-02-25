"""Unit tests for document semantic retrieval tools in postgres_tools."""

import json

from tools import postgres_tools


def test_exact_value_question_heuristic() -> None:
    assert postgres_tools._is_exact_value_question("What is the exact invoice total?")
    assert postgres_tools._is_exact_value_question("How much was the spare part price?")
    assert not postgres_tools._is_exact_value_question("Summarize the policy context.")


def test_publish_payload_requires_success_and_no_error() -> None:
    assert postgres_tools._is_successful_publish_payload(
        {"status": "success", "published": True}
    )
    assert not postgres_tools._is_successful_publish_payload(
        {"status": "failed", "error": "grant failed"}
    )
    assert not postgres_tools._is_successful_publish_payload({"status": "ok", "error": "boom"})


def test_publish_payload_rejects_non_dict() -> None:
    assert not postgres_tools._is_successful_publish_payload(None)
    assert not postgres_tools._is_successful_publish_payload("status=success")


async def test_query_document_facts_missing_schema_error(monkeypatch) -> None:
    async def _fake_get_pool():
        return object()

    async def _fake_table_exists(_pool, _table_name: str) -> bool:
        return False

    monkeypatch.setattr(postgres_tools, "_get_pool", _fake_get_pool)
    monkeypatch.setattr(postgres_tools, "_table_exists", _fake_table_exists)

    payload = json.loads(
        await postgres_tools.query_document_facts.coroutine(
            "11111111-1111-1111-1111-111111111111",
            "exact invoice amount",
        )
    )
    assert payload["status"] == "error"
    assert payload["error_type"] == "missing_schema"


async def test_search_document_chunks_missing_schema_error(monkeypatch) -> None:
    async def _fake_get_pool():
        return object()

    async def _fake_table_exists(_pool, _table_name: str) -> bool:
        return False

    monkeypatch.setattr(postgres_tools, "_get_pool", _fake_get_pool)
    monkeypatch.setattr(postgres_tools, "_table_exists", _fake_table_exists)

    payload = json.loads(
        await postgres_tools.search_document_chunks.coroutine(
            "11111111-1111-1111-1111-111111111111",
            "infrastructure investment outlook",
        )
    )
    assert payload["status"] == "error"
    assert payload["error_type"] == "missing_schema"


async def test_query_document_facts_marks_conflicting_exact_values(monkeypatch) -> None:
    async def _fake_get_pool():
        return object()

    async def _fake_table_exists(_pool, _table_name: str) -> bool:
        return True

    async def _fake_scope(_pool, _data_product_id: str):
        return (None, "requirements")

    async def _fake_query(_pool, sql: str, *args):
        if "FROM doc_facts" in sql:
            return [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "document_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    "filename": "invoice-a.pdf",
                    "fact_type": "monetary_amount",
                    "subject_key": "invoice_total",
                    "predicate": "reported_amount",
                    "object_value": "1200",
                    "object_unit": None,
                    "numeric_value": 1200,
                    "currency": "USD",
                    "event_time": None,
                    "source_page": 1,
                    "confidence": 0.9,
                    "metadata": {},
                },
                {
                    "id": "22222222-2222-2222-2222-222222222222",
                    "document_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                    "filename": "invoice-b.pdf",
                    "fact_type": "monetary_amount",
                    "subject_key": "invoice_total",
                    "predicate": "reported_amount",
                    "object_value": "1300",
                    "object_unit": None,
                    "numeric_value": 1300,
                    "currency": "USD",
                    "event_time": None,
                    "source_page": 3,
                    "confidence": 0.88,
                    "metadata": {},
                },
            ]
        raise AssertionError(f"Unexpected SQL in test stub: {sql[:120]}")

    monkeypatch.setattr(postgres_tools, "_get_pool", _fake_get_pool)
    monkeypatch.setattr(postgres_tools, "_table_exists", _fake_table_exists)
    monkeypatch.setattr(postgres_tools, "_resolve_active_document_scope", _fake_scope)
    monkeypatch.setattr(postgres_tools.pg_service, "query", _fake_query)

    payload = json.loads(
        await postgres_tools.query_document_facts.coroutine(
            "11111111-1111-1111-1111-111111111111",
            "What is the exact invoice amount?",
        )
    )
    hint = payload["answer_contract_hint"]
    assert payload["status"] == "success"
    assert hint["trust_state"] == "abstained_conflicting_evidence"
    assert hint["confidence_decision"] == "abstain"
    assert hint["exactness_state"] == "insufficient_evidence"
    assert len(hint["conflict_notes"]) >= 1
