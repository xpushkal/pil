"""``/metrics`` returns a Prometheus exposition payload."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_metrics_exposes_pil_counters(app_client: AsyncClient) -> None:
    resp = await app_client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    # Counters are declared at import time, so even with zero traffic they should
    # appear in the exposition (with a 0 value).
    for name in (
        "pil_requests_total",
        "pil_pii_detections_total",
        "pil_cache_hits_total",
        "pil_cache_misses_total",
        "pil_auth_failures_total",
    ):
        assert name in body, f"missing metric: {name}"
