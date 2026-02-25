"""LangChain tools for Neo4j document graph operations.

Tools manage the document intelligence graph:
    - Upsert Document, DocumentChunk, DocumentFact, Entity nodes
    - Create relationships (HAS_DOCUMENT, HAS_CHUNK, STATES_FACT, LINKS_TO_ENTITY)
    - Query document subgraph for evidence and citation chains
    - Cross-link document facts to structured ERD entities
"""

import json
import logging
from typing import Any

from langchain.tools import tool

from services import neo4j as neo4j_service

logger = logging.getLogger(__name__)


async def _get_driver() -> Any:
    """Return the global Neo4j driver, raising if not initialized."""
    if neo4j_service._driver is None:
        raise RuntimeError("Neo4j driver not initialized. Start the application first.")
    return neo4j_service._driver


@tool
async def upsert_document_node(
    data_product_id: str,
    document_id: str,
    title: str,
    mime_type: str,
) -> str:
    """Create or update a Document node and link it to its DataProduct.

    Merges a Document node by document_id, sets properties, and ensures
    the HAS_DOCUMENT relationship from the DataProduct exists.

    Args:
        data_product_id: UUID of the data product.
        document_id: UUID of the uploaded document.
        title: Document filename or title.
        mime_type: MIME type of the document.
    """
    driver = await _get_driver()

    cypher = """
    MERGE (dp:DataProduct {data_product_id: $dp_id})
    MERGE (d:Document {document_id: $doc_id})
    SET d.title = $title,
        d.mime_type = $mime_type,
        d.data_product_id = $dp_id,
        d.updated_at = datetime()
    MERGE (dp)-[:HAS_DOCUMENT]->(d)
    RETURN d.document_id AS document_id
    """

    result = await neo4j_service.execute_write(
        driver, cypher, dp_id=data_product_id, doc_id=document_id,
        title=title, mime_type=mime_type,
    )

    return json.dumps({"status": "ok", "document_id": document_id, "records": len(result)})


@tool
async def upsert_document_chunks(
    document_id: str,
    chunks: str,
) -> str:
    """Batch create or update DocumentChunk nodes linked to a Document.

    Each chunk represents a section of extracted text with positional metadata.

    Args:
        document_id: UUID of the parent document.
        chunks: JSON array of objects with keys: chunk_id, text, position (int).
    """
    driver = await _get_driver()
    chunk_list = json.loads(chunks) if isinstance(chunks, str) else chunks

    cypher = """
    UNWIND $chunks AS chunk
    MERGE (d:Document {document_id: $doc_id})
    MERGE (c:DocumentChunk {chunk_id: chunk.chunk_id})
    SET c.text = chunk.text,
        c.position = chunk.position,
        c.document_id = $doc_id,
        c.updated_at = datetime()
    MERGE (d)-[:HAS_CHUNK]->(c)
    RETURN count(c) AS upserted
    """

    result = await neo4j_service.execute_write(
        driver, cypher, doc_id=document_id, chunks=chunk_list,
    )
    count = result[0]["upserted"] if result else 0

    return json.dumps({"status": "ok", "document_id": document_id, "chunks_upserted": count})


@tool
async def upsert_document_facts(
    chunk_id: str,
    facts: str,
) -> str:
    """Batch create or update DocumentFact nodes linked to a DocumentChunk.

    Each fact is a structured assertion extracted from the chunk text.

    Args:
        chunk_id: UUID of the parent chunk.
        facts: JSON array of objects with keys: fact_id, statement, category (optional).
    """
    driver = await _get_driver()
    fact_list = json.loads(facts) if isinstance(facts, str) else facts

    cypher = """
    UNWIND $facts AS fact
    MERGE (c:DocumentChunk {chunk_id: $chunk_id})
    MERGE (f:DocumentFact {fact_id: fact.fact_id})
    SET f.statement = fact.statement,
        f.category = fact.category,
        f.chunk_id = $chunk_id,
        f.updated_at = datetime()
    MERGE (c)-[:STATES_FACT]->(f)
    RETURN count(f) AS upserted
    """

    result = await neo4j_service.execute_write(
        driver, cypher, chunk_id=chunk_id, facts=fact_list,
    )
    count = result[0]["upserted"] if result else 0

    return json.dumps({"status": "ok", "chunk_id": chunk_id, "facts_upserted": count})


@tool
async def link_fact_to_entity(
    fact_id: str,
    entity_name: str,
    entity_type: str,
) -> str:
    """Link a DocumentFact to a named Entity (person, metric, concept, etc.).

    Merges the Entity node by name and creates the LINKS_TO_ENTITY relationship.

    Args:
        fact_id: UUID of the document fact.
        entity_name: Name of the entity (e.g. "Revenue", "John Smith").
        entity_type: Category of the entity (e.g. "metric", "person", "concept").
    """
    driver = await _get_driver()

    cypher = """
    MERGE (f:DocumentFact {fact_id: $fact_id})
    MERGE (e:Entity {name: $entity_name})
    SET e.entity_type = $entity_type,
        e.updated_at = datetime()
    MERGE (f)-[:LINKS_TO_ENTITY]->(e)
    RETURN e.name AS entity_name
    """

    result = await neo4j_service.execute_write(
        driver, cypher, fact_id=fact_id, entity_name=entity_name,
        entity_type=entity_type,
    )

    return json.dumps({
        "status": "ok",
        "fact_id": fact_id,
        "entity_name": entity_name,
        "linked": len(result) > 0,
    })


@tool
async def query_document_graph(data_product_id: str) -> str:
    """Retrieve the full document subgraph for a data product.

    Returns all Document, Chunk, Fact, and Entity nodes with their
    relationships for use in evidence-backed answers.

    Args:
        data_product_id: UUID of the data product.
    """
    driver = await _get_driver()

    cypher = """
    MATCH (dp:DataProduct {data_product_id: $dp_id})-[:HAS_DOCUMENT]->(d:Document)
    OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:DocumentChunk)
    OPTIONAL MATCH (c)-[:STATES_FACT]->(f:DocumentFact)
    OPTIONAL MATCH (f)-[:LINKS_TO_ENTITY]->(e:Entity)
    RETURN d.document_id AS document_id,
           d.title AS title,
           d.mime_type AS mime_type,
           collect(DISTINCT {
               chunk_id: c.chunk_id,
               text: c.text,
               position: c.position
           }) AS chunks,
           collect(DISTINCT {
               fact_id: f.fact_id,
               statement: f.statement,
               category: f.category,
               chunk_id: f.chunk_id
           }) AS facts,
           collect(DISTINCT {
               name: e.name,
               entity_type: e.entity_type
           }) AS entities
    """

    result = await neo4j_service.execute_read(driver, cypher, dp_id=data_product_id)
    return json.dumps({"documents": result}, default=str)


@tool
async def find_facts_for_entity(entity_name: str) -> str:
    """Find all document facts and their source chunks for a named entity.

    Traverses Entity -> Facts -> Chunks to build a citation chain
    back to the original source text.

    Args:
        entity_name: Name of the entity to search for.
    """
    driver = await _get_driver()

    cypher = """
    MATCH (e:Entity {name: $entity_name})<-[:LINKS_TO_ENTITY]-(f:DocumentFact)
    OPTIONAL MATCH (c:DocumentChunk)-[:STATES_FACT]->(f)
    OPTIONAL MATCH (d:Document)-[:HAS_CHUNK]->(c)
    RETURN e.name AS entity_name,
           e.entity_type AS entity_type,
           collect({
               fact_id: f.fact_id,
               statement: f.statement,
               category: f.category,
               chunk_text: c.text,
               chunk_position: c.position,
               document_id: d.document_id,
               document_title: d.title
           }) AS citations
    """

    result = await neo4j_service.execute_read(driver, cypher, entity_name=entity_name)
    return json.dumps({"results": result}, default=str)
