"""Sprint 1 done-when condition: full pipeline e2e.

Drives a real curl-equivalent through PIL:

  1. Bootstrap an org + issue a PIL key.
  2. POST /openai/v1/chat/completions with a prompt containing an email,
     an OpenAI API key, and a GitHub token — with a mocked upstream so
     we can inspect the body PIL actually sent.
  3. Assert PIL scrubbed every tripwire from the body that hit "OpenAI".
  4. Assert the response carries X-PIL-PII-Entities, X-PIL-Cache-Hit=false,
     X-PIL-Latency-Ms, X-PIL-Request-Id.
  5. POST the same request again. Assert X-PIL-Cache-Hit=true and that
     the upstream was NOT called the second time.
  6. Assert two audit rows exist; the second has ``cache_hit=true``.

Uses real Postgres + Redis + Presidio (small spaCy model). Upstream
OpenAI is mocked with ``respx``.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


PII_PROMPT = (
    "Please email me at bob.tripwire@example.com. "
    "My OpenAI key is sk-" + "a" * 48 + " and my GitHub token is ghp_" + "Z" * 36 + ". "
    "Help me debug this."
)


FAKE_OPENAI_RESPONSE: dict[str, Any] = {
    "id": "chatcmpl-mock-001",
    "object": "chat.completion",
    "created": 1735689600,
    "model": "gpt-4o",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Sure, I can help. Reply to <EMAIL_ADDRESS_1> once tested.",
            },
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 50, "completion_tokens": 12, "total_tokens": 62},
}


@pytest_asyncio.fixture
async def app_client(alembic_db: None) -> AsyncIterator[AsyncClient]:
    """ASGI client against the real FastAPI app.

    ASGITransport doesn't run lifespan, so we initialize the pipeline
    singletons manually here. This mirrors what would happen at boot.
    """
    from app.core import pipeline as pipeline_mod
    from app.core.embeddings import _reset_for_tests as reset_embeddings

    pipeline_mod.reset_for_tests()
    reset_embeddings()
    # Boot the real Presidio + embeddings + httpx pool.
    await pipeline_mod.init_pipeline()

    from app.main import create_app

    application = create_app()
    transport = ASGITransport(app=application)
    try:
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            yield client
    finally:
        await pipeline_mod.shutdown_pipeline()


@pytest_asyncio.fixture
async def bootstrap_org_key(app_client: AsyncClient) -> tuple[str, str]:
    """Hit the admin-gated bootstrap endpoints to create an org + first key.

    Returns (org_id, pil_key_plaintext)."""
    admin = os.environ["PIL_MASTER_ENCRYPTION_KEY"]

    resp = await app_client.post(
        "/api/v1/orgs",
        json={"name": "E2E Corp", "raw_logging_opt_in": False},
        headers={"X-PIL-Admin-Token": admin},
    )
    assert resp.status_code == 201, resp.text
    org_id = resp.json()["id"]

    resp = await app_client.post(
        f"/api/v1/orgs/{org_id}/keys",
        json={"name": "e2e-bootstrap"},
        headers={"X-PIL-Admin-Token": admin},
    )
    assert resp.status_code == 201, resp.text
    plaintext = resp.json()["plaintext"]
    return org_id, plaintext


async def test_e2e_pii_scrubbed_then_cached(
    app_client: AsyncClient,
    bootstrap_org_key: tuple[str, str],
    db_session: AsyncSession,
) -> None:
    org_id, pil_key = bootstrap_org_key

    upstream_bodies: list[dict[str, Any]] = []

    def record_and_respond(request: httpx.Request) -> Response:
        upstream_bodies.append(json.loads(request.content.decode("utf-8")))
        return Response(200, json=FAKE_OPENAI_RESPONSE)

    # ----- first call: cache MISS, upstream hit -------------------------
    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://api.openai.com/v1/chat/completions").mock(
            side_effect=record_and_respond
        )

        body = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": PII_PROMPT}],
        }
        resp1 = await app_client.post(
            "/openai/v1/chat/completions",
            headers={
                "X-PIL-Key": pil_key,
                "Authorization": "Bearer sk-fake-upstream-key",
            },
            json=body,
        )

        assert resp1.status_code == 200, resp1.text
        assert resp1.headers.get("X-PIL-Cache-Hit") == "false"
        assert resp1.headers.get("X-PIL-Request-Id")
        assert resp1.headers.get("X-PIL-Latency-Ms")
        pii_header = resp1.headers.get("X-PIL-PII-Entities", "")
        # We expect *at least* one OPENAI_API_KEY + one GITHUB_TOKEN +
        # one EMAIL_ADDRESS to have been redacted from the upstream body.
        assert "OPENAI_API_KEY" in pii_header
        assert "GITHUB_TOKEN" in pii_header
        assert "EMAIL_ADDRESS" in pii_header

        assert len(upstream_bodies) == 1
        sent_to_openai = upstream_bodies[0]["messages"][0]["content"]
        # None of the raw tripwires reach OpenAI:
        assert "bob.tripwire@example.com" not in sent_to_openai
        assert "sk-" + "a" * 48 not in sent_to_openai
        assert "ghp_" + "Z" * 36 not in sent_to_openai
        # Placeholders should have replaced them:
        assert "<EMAIL_ADDRESS_1>" in sent_to_openai
        assert "<OPENAI_API_KEY_1>" in sent_to_openai
        assert "<GITHUB_TOKEN_1>" in sent_to_openai

        # ----- response body restored (reversible mode) -----------------
        body1 = resp1.json()
        msg = body1["choices"][0]["message"]["content"]
        # The mock returned a placeholder; PIL should have restored it.
        assert "<EMAIL_ADDRESS_1>" not in msg
        assert "bob.tripwire@example.com" in msg

    # ----- second call: cache HIT, upstream NOT called -----------------
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post("https://api.openai.com/v1/chat/completions").mock(
            side_effect=record_and_respond
        )

        resp2 = await app_client.post(
            "/openai/v1/chat/completions",
            headers={
                "X-PIL-Key": pil_key,
                "Authorization": "Bearer sk-fake-upstream-key",
            },
            json=body,
        )
        assert resp2.status_code == 200, resp2.text
        assert resp2.headers.get("X-PIL-Cache-Hit") == "true"
        # The upstream must NOT have been hit on the second call.
        assert route.called is False

    # ----- audit rows -------------------------------------------------
    from app.db.models import RequestLog

    rows = (
        await db_session.execute(
            select(RequestLog).order_by(RequestLog.created_at.asc())
        )
    ).scalars().all()
    assert len(rows) >= 2, f"expected ≥2 audit rows, got {len(rows)}"
    # First row was a miss; second was a hit
    assert rows[0].cache_hit is False
    assert rows[-1].cache_hit is True
    assert "EMAIL_ADDRESS" in rows[0].pii_categories
    assert "OPENAI_API_KEY" in rows[0].pii_categories
    assert "GITHUB_TOKEN" in rows[0].pii_categories
    assert rows[0].pii_entity_count >= 3
    assert rows[0].provider == "openai"
    assert rows[0].model == "gpt-4o"
    assert rows[0].latency_ms > 0
