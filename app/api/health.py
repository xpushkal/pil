"""Liveness and readiness probes.

``/health/live`` returns 200 as long as the process is running. ``/health/ready``
additionally pings Postgres and Redis so orchestrators can route traffic only
once dependencies are reachable.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.db.session import get_engine
from app.utils.redis_client import get_redis

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(response: Response) -> dict[str, Any]:
    checks: dict[str, str] = {}
    overall_ok = True

    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001 — readiness probe must never raise
        checks["postgres"] = f"error: {type(exc).__name__}"
        overall_ok = False

    try:
        redis = get_redis()
        pong = await redis.ping()
        checks["redis"] = "ok" if pong else "error: no-pong"
        if not pong:
            overall_ok = False
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {type(exc).__name__}"
        overall_ok = False

    if not overall_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if overall_ok else "degraded", "checks": checks}
