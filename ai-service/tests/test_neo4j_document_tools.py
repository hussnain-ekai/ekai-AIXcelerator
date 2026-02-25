"""Tests for Neo4j document graph tools."""

import json
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# Create a mock neo4j service module with AsyncMock callables so that
# await expressions work correctly when the tools call execute_write/read.
_mock_neo4j = MagicMock()
_mock_neo4j.execute_write = AsyncMock(return_value=[])
_mock_neo4j.execute_read = AsyncMock(return_value=[])
_mock_neo4j._driver = AsyncMock()

sys.modules.setdefault("services.neo4j", _mock_neo4j)

import tools.neo4j_document_tools as mod  # noqa: E402
from tools.neo4j_document_tools import (  # noqa: E402
    find_facts_for_entity,
    link_fact_to_entity,
    query_document_graph,
    upsert_document_chunks,
    upsert_document_facts,
    upsert_document_node,
)


@pytest.fixture(autouse=True)
def _reset_mocks() -> None:
    """Reset mock call counts and set safe defaults before each test."""
    mock_svc = mod.neo4j_service
    mock_svc.execute_write.reset_mock()
    mock_svc.execute_read.reset_mock()
    mock_svc.execute_write.return_value = [{"document_id": "doc-1"}]
    mock_svc.execute_read.return_value = []
    mock_svc._driver = AsyncMock()


@pytest.mark.asyncio
async def test_upsert_document_node() -> None:
    mod.neo4j_service.execute_write.return_value = [{"document_id": "doc-1"}]
    result = await upsert_document_node.ainvoke({
        "data_product_id": "dp-1",
        "document_id": "doc-1",
        "title": "test.pdf",
        "mime_type": "application/pdf",
    })
    parsed = json.loads(result)
    assert parsed["status"] == "ok"
    assert parsed["document_id"] == "doc-1"
    mod.neo4j_service.execute_write.assert_called_once()


@pytest.mark.asyncio
async def test_upsert_document_chunks() -> None:
    mod.neo4j_service.execute_write.return_value = [{"upserted": 2}]
    chunks = json.dumps([
        {"chunk_id": "c1", "text": "First section", "position": 0},
        {"chunk_id": "c2", "text": "Second section", "position": 1},
    ])
    result = await upsert_document_chunks.ainvoke({
        "document_id": "doc-1",
        "chunks": chunks,
    })
    parsed = json.loads(result)
    assert parsed["status"] == "ok"
    assert parsed["chunks_upserted"] == 2


@pytest.mark.asyncio
async def test_upsert_document_facts() -> None:
    mod.neo4j_service.execute_write.return_value = [{"upserted": 1}]
    facts = json.dumps([
        {"fact_id": "f1", "statement": "Revenue grew 10%", "category": "metric"},
    ])
    result = await upsert_document_facts.ainvoke({
        "chunk_id": "c1",
        "facts": facts,
    })
    parsed = json.loads(result)
    assert parsed["status"] == "ok"
    assert parsed["facts_upserted"] == 1


@pytest.mark.asyncio
async def test_link_fact_to_entity() -> None:
    mod.neo4j_service.execute_write.return_value = [{"entity_name": "Revenue"}]
    result = await link_fact_to_entity.ainvoke({
        "fact_id": "f1",
        "entity_name": "Revenue",
        "entity_type": "metric",
    })
    parsed = json.loads(result)
    assert parsed["status"] == "ok"
    assert parsed["entity_name"] == "Revenue"
    assert parsed["linked"] is True


@pytest.mark.asyncio
async def test_query_document_graph() -> None:
    mod.neo4j_service.execute_read.return_value = [
        {
            "document_id": "doc-1",
            "title": "test.pdf",
            "mime_type": "application/pdf",
            "chunks": [],
            "facts": [],
            "entities": [],
        }
    ]
    result = await query_document_graph.ainvoke({"data_product_id": "dp-1"})
    parsed = json.loads(result)
    assert "documents" in parsed
    assert len(parsed["documents"]) == 1


@pytest.mark.asyncio
async def test_find_facts_for_entity() -> None:
    mod.neo4j_service.execute_read.return_value = [
        {
            "entity_name": "Revenue",
            "entity_type": "metric",
            "citations": [
                {
                    "fact_id": "f1",
                    "statement": "Revenue grew 10%",
                    "document_title": "Q4 Report",
                }
            ],
        }
    ]
    result = await find_facts_for_entity.ainvoke({"entity_name": "Revenue"})
    parsed = json.loads(result)
    assert "results" in parsed
    assert parsed["results"][0]["entity_name"] == "Revenue"
