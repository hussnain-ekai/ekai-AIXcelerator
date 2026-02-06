"""PostgreSQL async connection pool management.

Provides an asyncpg-based connection pool for application state operations.
Handles pool creation, health checks, and graceful shutdown. All queries
execute within workspace-isolated RLS contexts.
"""

from typing import Any

import asyncpg

_pool: asyncpg.Pool | None = None


async def get_pool(database_url: str) -> asyncpg.Pool:
    """Create or return the singleton asyncpg connection pool.

    Args:
        database_url: PostgreSQL connection string (e.g. postgresql://user:pass@host:5432/db).

    Returns:
        The shared asyncpg pool instance.
    """
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(database_url, min_size=2, max_size=10)
    return _pool


async def query(
    pool: asyncpg.Pool,
    sql: str,
    *args: Any,
    current_user: str | None = None,
) -> list[asyncpg.Record]:
    """Execute a read query and return all rows.

    When *current_user* is provided the session variable ``app.current_user``
    is set before the query runs so that PostgreSQL RLS policies can enforce
    workspace isolation.
    """
    async with pool.acquire() as conn:
        if current_user:
            await conn.execute("SELECT set_config('app.current_user', $1, false)", current_user)
        return await conn.fetch(sql, *args)


async def execute(
    pool: asyncpg.Pool,
    sql: str,
    *args: Any,
    current_user: str | None = None,
) -> str:
    """Execute a write statement and return the status string.

    Sets ``app.current_user`` when provided for RLS enforcement.
    """
    async with pool.acquire() as conn:
        if current_user:
            await conn.execute("SELECT set_config('app.current_user', $1, false)", current_user)
        return await conn.execute(sql, *args)


async def health_check(pool: asyncpg.Pool) -> bool:
    """Return True if a trivial query succeeds against the pool."""
    try:
        await pool.fetchval("SELECT 1")
        return True
    except Exception:
        return False


async def close() -> None:
    """Close the global connection pool if it exists."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
