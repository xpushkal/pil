"""Shared async Redis client. One pool per process."""

from __future__ import annotations

from functools import lru_cache

from redis.asyncio import Redis, from_url

from app.settings import get_settings


@lru_cache(maxsize=1)
def get_redis() -> Redis:
    settings = get_settings()
    return from_url(settings.redis_url, encoding="utf-8", decode_responses=False)


async def close_redis() -> None:
    """Tear down the pool at shutdown."""
    if get_redis.cache_info().currsize == 0:
        return
    client = get_redis()
    await client.aclose()
    get_redis.cache_clear()
