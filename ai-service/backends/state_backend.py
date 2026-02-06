"""Redis-backed state management for active agent sessions.

Stores agent session state, LangGraph checkpoints, and interrupt flags.
Keys follow the pattern:
    - agent:session:{id} -- session state
    - checkpoint:{session}:{id} -- LangGraph checkpoints
Cache entries use a 1-hour TTL.
"""

from dataclasses import dataclass
from typing import Any

import redis.asyncio as redis

from config import get_settings
from services import redis as redis_service


def _get_session_ttl() -> int:
    return get_settings().session_ttl_seconds


def _get_cache_ttl() -> int:
    return get_settings().cache_ttl_seconds


@dataclass
class RedisStateBackend:
    """Manage active agent session state in Redis."""

    client: redis.Redis

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Load an active agent session by ID."""
        return await redis_service.get_json(self.client, f"agent:session:{session_id}")

    async def save_session(self, session_id: str, state: dict[str, Any]) -> None:
        """Persist agent session state with a TTL."""
        await redis_service.set_json(
            self.client,
            f"agent:session:{session_id}",
            state,
            ttl=_get_session_ttl(),
        )

    async def delete_session(self, session_id: str) -> None:
        """Remove a session from Redis."""
        await self.client.delete(f"agent:session:{session_id}")

    async def get_checkpoint(self, session_id: str, checkpoint_id: str) -> dict[str, Any] | None:
        """Load a LangGraph checkpoint."""
        return await redis_service.get_json(
            self.client,
            f"checkpoint:{session_id}:{checkpoint_id}",
        )

    async def save_checkpoint(
        self,
        session_id: str,
        checkpoint_id: str,
        data: dict[str, Any],
    ) -> None:
        """Persist a LangGraph checkpoint."""
        await redis_service.set_json(
            self.client,
            f"checkpoint:{session_id}:{checkpoint_id}",
            data,
            ttl=_get_session_ttl(),
        )

    async def set_interrupt(self, session_id: str, reason: str = "") -> None:
        """Signal an interrupt for a running session."""
        await redis_service.set_json(
            self.client,
            f"interrupt:{session_id}",
            {"interrupted": True, "reason": reason},
            ttl=_get_session_ttl(),
        )

    async def check_interrupt(self, session_id: str) -> bool:
        """Return True if an interrupt has been signalled for this session."""
        data = await redis_service.get_json(self.client, f"interrupt:{session_id}")
        if data is None:
            return False
        return data.get("interrupted", False)

    async def clear_interrupt(self, session_id: str) -> None:
        """Clear any pending interrupt flag."""
        await self.client.delete(f"interrupt:{session_id}")

    async def cache_get(self, key: str) -> dict[str, Any] | None:
        """Retrieve a cached value."""
        return await redis_service.get_json(self.client, f"cache:{key}")

    async def cache_set(self, key: str, value: dict[str, Any]) -> None:
        """Store a value in the cache with the default TTL."""
        await redis_service.set_json(self.client, f"cache:{key}", value, ttl=_get_cache_ttl())
