"""``/health/live`` must not depend on Postgres or Redis."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_live_returns_ok(app_client: AsyncClient) -> None:
    resp = await app_client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert "X-PIL-Request-Id" in resp.headers
