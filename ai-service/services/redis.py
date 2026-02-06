"""Redis async client management.

Provides connection lifecycle management for Redis, used for:
    - Active agent session state (agent:session:{id})
    - LangGraph checkpoints (checkpoint:{session}:{id})
    - ERD cache (cache:erd:{id}) with 1-hour TTL
    - Profile cache (cache:profile:{fqn}) with 1-hour TTL
"""

import json
from typing import Any

import redis.asyncio as redis

_client: redis.Redis | None = None


async def get_client(redis_url: str) -> redis.Redis:
    """Create or return the singleton async Redis client.

    Args:
        redis_url: Redis connection URL (e.g. redis://localhost:6379/0).

    Returns:
        The shared async Redis client.
    """
    global _client
    if _client is None:
        _client = redis.from_url(redis_url, decode_responses=True)
    return _client


async def get_json(client: redis.Redis, key: str) -> dict[str, Any] | None:
    """Retrieve and deserialize a JSON value from Redis.

    Returns None if the key does not exist.
    """
    val = await client.get(key)
    if val is None:
        return None
    return json.loads(val)


async def set_json(
    client: redis.Redis,
    key: str,
    value: dict[str, Any],
    ttl: int | None = None,
) -> None:
    """Serialize a dict as JSON and store it in Redis.

    Args:
        client: The async Redis client.
        key: Redis key.
        value: Dict to serialize.
        ttl: Optional time-to-live in seconds.
    """
    serialized = json.dumps(value)
    if ttl:
        await client.setex(key, ttl, serialized)
    else:
        await client.set(key, serialized)


async def health_check(client: redis.Redis) -> bool:
    """Return True if Redis responds to PING."""
    try:
        return await client.ping()
    except Exception:
        return False


async def close() -> None:
    """Close the global Redis client if it exists."""
    global _client
    if _client:
        await _client.aclose()
        _client = None
