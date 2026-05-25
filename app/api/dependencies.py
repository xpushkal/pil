"""Shared FastAPI dependencies: auth, rate limit, DB session."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthError, AuthenticatedKey, authenticate
from app.core.rate_limit import RateLimiter
from app.db.session import get_sessionmaker
from app.observability.metrics import auth_failures_total
from app.utils.redis_client import get_redis


async def db_session() -> AsyncIterator[AsyncSession]:
    """One session per request."""
    async with get_sessionmaker()() as session:
        yield session


async def require_pil_key(
    request: Request,
    x_pil_key: str | None = Header(default=None, alias="X-PIL-Key"),
    session: AsyncSession = Depends(db_session),
) -> AuthenticatedKey:
    if x_pil_key is None:
        auth_failures_total.labels(reason="missing").inc()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "AUTH_REQUIRED", "message": "X-PIL-Key header is required"},
            headers={"WWW-Authenticate": "PIL"},
        )
    try:
        result = await authenticate(session, x_pil_key)
    except AuthError as exc:
        auth_failures_total.labels(reason=exc.reason).inc()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "AUTH_FAILED", "reason": exc.reason},
            headers={"WWW-Authenticate": "PIL"},
        ) from exc

    # Stash on request.state so downstream code can read it without re-doing
    # the work (e.g. audit logger).
    request.state.pil_auth = result
    return result


async def enforce_rate_limit(
    auth: AuthenticatedKey = Depends(require_pil_key),
) -> AuthenticatedKey:
    limiter = RateLimiter(get_redis())
    result = await limiter.check(
        scope=str(auth.api_key.id),
        limit=auth.api_key.rate_limit_per_hour,
    )
    if not result.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "RATE_LIMITED",
                "limit": result.limit,
                "current": result.current,
            },
            headers={"Retry-After": str(max(1, result.retry_after_ms // 1000))},
        )
    return auth
