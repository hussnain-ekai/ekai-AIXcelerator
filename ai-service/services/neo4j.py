"""Neo4j async driver management and Cypher query execution.

Provides connection lifecycle management and typed query helpers
for the ERD graph. Uses the neo4j async driver with bolt protocol.
"""

from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase

_driver: AsyncDriver | None = None


async def get_driver(uri: str, user: str, password: str) -> AsyncDriver:
    """Create or return the singleton Neo4j async driver.

    Args:
        uri: Bolt connection URI (e.g. bolt://localhost:7687).
        user: Neo4j username.
        password: Neo4j password.

    Returns:
        The shared AsyncDriver instance.
    """
    global _driver
    if _driver is None:
        _driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
    return _driver


async def execute_read(
    driver: AsyncDriver,
    cypher: str,
    **params: Any,
) -> list[dict[str, Any]]:
    """Run a read-only Cypher query inside a managed read transaction.

    Returns a list of dicts, one per record.
    """
    async with driver.session() as session:
        result = await session.run(cypher, **params)
        records = await result.data()
        return records


async def execute_write(
    driver: AsyncDriver,
    cypher: str,
    **params: Any,
) -> list[dict[str, Any]]:
    """Run a write Cypher query inside a managed write transaction.

    Returns a list of dicts, one per record.
    """
    async with driver.session() as session:
        result = await session.run(cypher, **params)
        records = await result.data()
        return records


async def health_check(driver: AsyncDriver) -> bool:
    """Return True if a trivial Cypher query succeeds."""
    try:
        async with driver.session() as session:
            await session.run("RETURN 1")
        return True
    except Exception:
        return False


async def close() -> None:
    """Close the global Neo4j driver if it exists."""
    global _driver
    if _driver:
        await _driver.close()
        _driver = None
