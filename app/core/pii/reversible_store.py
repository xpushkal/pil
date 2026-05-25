"""Per-request reversible placeholder ↔ original mapping.

Lives in Redis under ``pii:map:{request_id}`` with a short TTL (default 300s,
configurable via ``PIL_PII_MAP_TTL_SECONDS``). When the upstream provider's
response comes back, the proxy reads the mapping and restores originals.

Plaintext originals leave the process **only** to/from Redis, and only for the
window the original request is in flight. They are never logged or persisted
to Postgres.
"""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis

from app.settings import get_settings


def _key(request_id: str) -> str:
    return f"pii:map:{request_id}"


@dataclass(frozen=True)
class StoredMapping:
    """A (placeholder -> original) lookup table for one in-flight request."""

    mapping: dict[str, str]


class ReversibleStore:
    """Wraps Redis hash operations for PII mappings."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def put(self, request_id: str, mapping: dict[str, str]) -> None:
        if not mapping:
            return
        ttl = get_settings().pii_map_ttl_seconds
        key = _key(request_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.delete(key)
            pipe.hset(key, mapping=mapping)
            pipe.expire(key, ttl)
            await pipe.execute()

    async def get(self, request_id: str) -> StoredMapping:
        raw = await self._redis.hgetall(_key(request_id))
        mapping = {
            (k.decode() if isinstance(k, bytes) else k): (
                v.decode() if isinstance(v, bytes) else v
            )
            for k, v in (raw or {}).items()
        }
        return StoredMapping(mapping=mapping)

    async def drop(self, request_id: str) -> None:
        await self._redis.delete(_key(request_id))
