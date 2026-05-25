"""Shared pytest fixtures.

Sprint 1 keeps this lean. Phase 2 adds DB-backed fixtures (per-test orgs +
API keys) and the cross-org cache isolation fixture.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest.fixture(scope="session", autouse=True)
def _test_env() -> Iterator[None]:
    """Force a deterministic, dependency-free env for unit tests.

    Integration tests override these via the ``--integration`` flag and
    docker-compose-provided services.
    """
    os.environ.setdefault("PIL_ENV", "ci")
    os.environ.setdefault("PIL_DATABASE_URL", "postgresql+asyncpg://pil:pil@localhost:5432/pil_test")
    os.environ.setdefault("PIL_DATABASE_URL_SYNC", "postgresql+psycopg://pil:pil@localhost:5432/pil_test")
    os.environ.setdefault("PIL_REDIS_URL", "redis://localhost:6379/15")
    os.environ.setdefault("PIL_OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    os.environ.setdefault("PIL_MASTER_ENCRYPTION_KEY", "test-master-key-not-secret-32-bytes!!")
    # Skip Presidio + sentence-transformers + httpx pool init for unit tests
    # that don't actually exercise the pipeline. Integration tests unset this.
    os.environ.setdefault("PIL_SKIP_HEAVY_INIT", "1")
    yield


@pytest_asyncio.fixture
async def app_client() -> AsyncIterator[AsyncClient]:
    """ASGI in-process HTTPX client. Use for unit-level FastAPI tests that
    don't need real Postgres or Redis."""
    from app.main import create_app

    application = create_app()
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
