"""Redis-backed sliding-window rate limit.

Uses sorted sets where the score is the request's epoch-millisecond timestamp;
we trim entries older than ``window`` on every check and reject if the
remaining count would exceed the configured limit. A small Lua script makes
the check-and-increment atomic.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from redis.asyncio import Redis

WINDOW_SECONDS = 3600
# KEYS[1] = the bucket key
# ARGV[1] = window ms, ARGV[2] = limit, ARGV[3] = now ms, ARGV[4] = unique member
_SCRIPT = """
local key = KEYS[1]
local window_ms = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local now_ms = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, 0, now_ms - window_ms)
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry_ms = window_ms - (now_ms - tonumber(oldest[2]))
  return {0, count, retry_ms}
end
redis.call('ZADD', key, now_ms, member)
redis.call('PEXPIRE', key, window_ms)
return {1, count + 1, 0}
"""


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    current: int
    limit: int
    retry_after_ms: int


class RateLimiter:
    """Per-key sliding window. ``key`` should be opaque to the limiter
    (we hash by ``api_key_id`` not by the secret)."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._sha: str | None = None

    async def _ensure_script(self) -> str:
        if self._sha is None:
            self._sha = await self._redis.script_load(_SCRIPT)
        return self._sha

    async def check(self, scope: str, limit: int) -> RateLimitResult:
        sha = await self._ensure_script()
        now_ms = int(time.time() * 1000)
        member = uuid.uuid4().hex
        bucket = f"pil:rl:{scope}"
        result = await self._redis.evalsha(
            sha, 1, bucket, WINDOW_SECONDS * 1000, limit, now_ms, member
        )
        allowed_flag, count, retry_ms = result
        return RateLimitResult(
            allowed=bool(allowed_flag),
            current=int(count),
            limit=limit,
            retry_after_ms=int(retry_ms),
        )
