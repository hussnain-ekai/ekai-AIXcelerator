"""Neo4j-backed graph storage for ERD data models.

Manages the entity-relationship graph with nodes and relationships:
    - Nodes: Database, Schema, Table, Column
    - Hierarchy: HAS_SCHEMA, HAS_TABLE, HAS_COLUMN
    - Table classification: FACT or DIMENSION
    - FK relationships with confidence scores and cardinality
"""

from dataclasses import dataclass
from typing import Any

from neo4j import AsyncDriver

from services import neo4j as neo4j_service


@dataclass
class Neo4jBackend:
    """ERD graph storage backed by Neo4j."""

    driver: AsyncDriver

    async def get_erd(self, data_product_id: str) -> dict[str, Any]:
        """Retrieve the full ERD graph for a data product.

        Returns a dict with 'nodes' and 'edges' lists.
        """
        nodes = await neo4j_service.execute_read(
            self.driver,
            """
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
            """,
            dp_id=data_product_id,
        )

        edges = await neo4j_service.execute_read(
            self.driver,
            """
            MATCH (src:Table {data_product_id: $dp_id})-[r:FK_REFERENCES]->(tgt:Table)
            RETURN src.fqn AS source,
                   tgt.fqn AS target,
                   r.confidence AS confidence,
                   r.cardinality AS cardinality,
                   r.source_column AS source_column,
                   r.target_column AS target_column
            """,
            dp_id=data_product_id,
        )

        return {"nodes": nodes, "edges": edges}

    async def upsert_table(
        self,
        data_product_id: str,
        fqn: str,
        classification: str = "UNKNOWN",
        row_count: int = 0,
    ) -> None:
        """Merge a Table node into the graph."""
        await neo4j_service.execute_write(
            self.driver,
            """
            MERGE (t:Table {fqn: $fqn})
            SET t.data_product_id = $dp_id,
                t.classification = $classification,
                t.row_count = $row_count
            """,
            fqn=fqn,
            dp_id=data_product_id,
            classification=classification,
            row_count=row_count,
        )

    async def upsert_column(
        self,
        table_fqn: str,
        name: str,
        data_type: str = "VARCHAR",
        nullable: bool = True,
        is_pk: bool = False,
    ) -> None:
        """Merge a Column node and attach it to its parent Table."""
        await neo4j_service.execute_write(
            self.driver,
            """
            MATCH (t:Table {fqn: $table_fqn})
            MERGE (c:Column {name: $name, table_fqn: $table_fqn})
            SET c.data_type = $data_type,
                c.nullable = $nullable,
                c.is_pk = $is_pk
            MERGE (t)-[:HAS_COLUMN]->(c)
            """,
            table_fqn=table_fqn,
            name=name,
            data_type=data_type,
            nullable=nullable,
            is_pk=is_pk,
        )

    async def upsert_relationship(
        self,
        source_fqn: str,
        target_fqn: str,
        confidence: float = 0.0,
        cardinality: str = "MANY_TO_ONE",
        source_column: str = "",
        target_column: str = "",
    ) -> None:
        """Merge an FK_REFERENCES relationship between two tables."""
        await neo4j_service.execute_write(
            self.driver,
            """
            MATCH (src:Table {fqn: $source})
            MATCH (tgt:Table {fqn: $target})
            MERGE (src)-[r:FK_REFERENCES]->(tgt)
            SET r.confidence = $confidence,
                r.cardinality = $cardinality,
                r.source_column = $source_column,
                r.target_column = $target_column
            """,
            source=source_fqn,
            target=target_fqn,
            confidence=confidence,
            cardinality=cardinality,
            source_column=source_column,
            target_column=target_column,
        )

    async def classify_table(self, fqn: str, classification: str) -> None:
        """Set the FACT or DIMENSION classification on a Table node."""
        await neo4j_service.execute_write(
            self.driver,
            """
            MATCH (t:Table {fqn: $fqn})
            SET t.classification = $classification
            """,
            fqn=fqn,
            classification=classification,
        )

    async def find_join_path(self, table_a: str, table_b: str) -> dict[str, Any] | None:
        """Find the shortest join path between two tables.

        Returns a dict with 'tables' and 'joins' lists, or None if no path exists.
        """
        records = await neo4j_service.execute_read(
            self.driver,
            """
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
            """,
            table_a=table_a,
            table_b=table_b,
        )

        if not records:
            return None
        return records[0]
