"""CompositeBackend combining Redis, PostgreSQL, MinIO, and Neo4j.

Provides a unified interface for agent state management that routes
operations to the appropriate backing store:
    - Redis: Active session state and LangGraph checkpoints
    - PostgreSQL: Durable persistence of completed sessions
    - MinIO: Artifact binary storage
    - Neo4j: ERD graph queries and mutations
"""

from dataclasses import dataclass

from backends.filesystem_backend import MinIOFilesystemBackend
from backends.neo4j_backend import Neo4jBackend
from backends.state_backend import RedisStateBackend
from backends.store_backend import PostgresStoreBackend


@dataclass
class CompositeBackend:
    """Unified storage backend combining all four data stores.

    Agents interact with this single object rather than managing
    individual database connections. The composite routes each
    operation to the appropriate backend.
    """

    state: RedisStateBackend
    store: PostgresStoreBackend
    filesystem: MinIOFilesystemBackend
    graph: Neo4jBackend
