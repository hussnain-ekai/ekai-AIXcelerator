"""LangChain tools for Neo4j graph operations.

Tools manage the ERD graph representing the discovered data model:
    - Create/update nodes (Database, Schema, Table, Column)
    - Create relationships (HAS_SCHEMA, HAS_TABLE, HAS_COLUMN, FK references)
    - Query graph structure for ERD visualization
    - Classify tables as FACT or DIMENSION
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
async def query_erd_graph(data_product_id: str) -> str:
    """Retrieve the full ERD graph (nodes and edges) for a data product.

    Returns a JSON object with 'nodes' and 'edges' arrays suitable for
    rendering in the frontend ERD visualization.

    Args:
        data_product_id: UUID of the data product.
    """
    driver = await _get_driver()

    nodes_cypher = """
    MATCH (t:Table {data_product_id: $dp_id})
    OPTIONAL MATCH (t)-[:HAS_COLUMN]->(c:Column)
    RETURN t.fqn AS table_fqn,
           t.classification AS classification,
           t.row_count AS row_count,
           collect({
               name: c.name,
               data_type: c.data_type,
               nullable: c.nullable,
               is_pk: c.is_pk
           }) AS columns
    """

    edges_cypher = """
    MATCH (src:Table {data_product_id: $dp_id})-[r:FK_REFERENCES]->(tgt:Table)
    RETURN src.fqn AS source,
           tgt.fqn AS target,
           r.confidence AS confidence,
           r.cardinality AS cardinality,
           r.source_column AS source_column,
           r.target_column AS target_column
    """

    nodes = await neo4j_service.execute_read(driver, nodes_cypher, dp_id=data_product_id)
    edges = await neo4j_service.execute_read(driver, edges_cypher, dp_id=data_product_id)

    return json.dumps({"nodes": nodes, "edges": edges}, default=str)


@tool
async def update_erd(data_product_id: str, nodes: str, edges: str) -> str:
    """Upsert nodes and edges into the ERD graph for a data product.

    Merges Table and Column nodes and creates FK_REFERENCES relationships.

    Args:
        data_product_id: UUID of the data product.
        nodes: JSON array of node objects with fqn, classification, columns.
        edges: JSON array of edge objects with source, target, confidence, cardinality.
    """
    driver = await _get_driver()
    parsed_nodes: list[dict[str, Any]] = json.loads(nodes)
    parsed_edges: list[dict[str, Any]] = json.loads(edges)

    for node in parsed_nodes:
        table_cypher = """
        MERGE (t:Table {fqn: $fqn})
        SET t.data_product_id = $dp_id,
            t.classification = $classification,
            t.row_count = $row_count
        """
        await neo4j_service.execute_write(
            driver,
            table_cypher,
            fqn=node["fqn"],
            dp_id=data_product_id,
            classification=node.get("classification", "UNKNOWN"),
            row_count=node.get("row_count", 0),
        )

        for col in node.get("columns", []):
            # Handle both dict format {"name": "col1", ...} and string format "col1"
            if isinstance(col, str):
                col_name = col
                col_data_type = "VARCHAR"
                col_nullable = True
                col_is_pk = False
            else:
                col_name = col.get("name", col.get("column_name", "unknown"))
                col_data_type = col.get("data_type", "VARCHAR")
                col_nullable = col.get("nullable", True)
                col_is_pk = col.get("is_pk", False)

            col_cypher = """
            MATCH (t:Table {fqn: $table_fqn})
            MERGE (c:Column {name: $name, table_fqn: $table_fqn})
            SET c.data_type = $data_type,
                c.nullable = $nullable,
                c.is_pk = $is_pk
            MERGE (t)-[:HAS_COLUMN]->(c)
            """
            await neo4j_service.execute_write(
                driver,
                col_cypher,
                table_fqn=node["fqn"],
                name=col_name,
                data_type=col_data_type,
                nullable=col_nullable,
                is_pk=col_is_pk,
            )

    for edge in parsed_edges:
        edge_cypher = """
        MATCH (src:Table {fqn: $source})
        MATCH (tgt:Table {fqn: $target})
        MERGE (src)-[r:FK_REFERENCES]->(tgt)
        SET r.confidence = $confidence,
            r.cardinality = $cardinality,
            r.source_column = $source_column,
            r.target_column = $target_column
        """
        await neo4j_service.execute_write(
            driver,
            edge_cypher,
            source=edge["source"],
            target=edge["target"],
            confidence=edge.get("confidence", 0.0),
            cardinality=edge.get("cardinality", "MANY_TO_ONE"),
            source_column=edge.get("source_column", ""),
            target_column=edge.get("target_column", ""),
        )

    return json.dumps({
        "status": "ok",
        "nodes_upserted": len(parsed_nodes),
        "edges_upserted": len(parsed_edges),
    })


@tool
async def get_relationship_path(table_a: str, table_b: str) -> str:
    """Find the shortest join path between two tables in the ERD graph.

    Returns the sequence of tables and FK relationships connecting
    table_a to table_b. Used by the Generation Agent to determine
    join clauses for semantic views.

    Args:
        table_a: Fully qualified name of the first table.
        table_b: Fully qualified name of the second table.
    """
    driver = await _get_driver()

    cypher = """
    MATCH path = shortestPath(
        (a:Table {fqn: $table_a})-[:FK_REFERENCES*]-(b:Table {fqn: $table_b})
    )
    RETURN [n IN nodes(path) | n.fqn] AS tables,
           [r IN relationships(path) | {
               source: startNode(r).fqn,
               target: endNode(r).fqn,
               source_column: r.source_column,
               target_column: r.target_column,
               cardinality: r.cardinality
           }] AS joins
    """

    records = await neo4j_service.execute_read(
        driver,
        cypher,
        table_a=table_a,
        table_b=table_b,
    )

    if not records:
        return json.dumps({"error": f"No path found between {table_a} and {table_b}"})

    return json.dumps(records[0], default=str)


@tool
async def classify_entity(table_fqn: str, classification: str) -> str:
    """Set the FACT or DIMENSION classification for a table.

    Args:
        table_fqn: Fully qualified table name.
        classification: Either 'FACT' or 'DIMENSION'.
    """
    if classification not in ("FACT", "DIMENSION"):
        return json.dumps({"error": f"Invalid classification: {classification}. Must be FACT or DIMENSION."})

    driver = await _get_driver()

    cypher = """
    MATCH (t:Table {fqn: $fqn})
    SET t.classification = $classification
    RETURN t.fqn AS fqn, t.classification AS classification
    """

    records = await neo4j_service.execute_write(
        driver,
        cypher,
        fqn=table_fqn,
        classification=classification,
    )

    if not records:
        return json.dumps({"error": f"Table not found: {table_fqn}"})

    return json.dumps(records[0], default=str)
