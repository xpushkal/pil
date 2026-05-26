"""Shared integration-test fixtures.

These tests need real Postgres and Redis. The session-scoped ``alembic_db``
fixture applies migrations once per pytest session; per-test fixtures clean
the data tables (but keep the schema) between cases so suites stay fast.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from uuid import UUID

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


# Unit tests skip heavy init via PIL_SKIP_HEAVY_INIT=1. Integration tests
# explicitly opt back in so the real pipeline (Presidio + embeddings + httpx)
# initializes.
os.environ.pop("PIL_SKIP_HEAVY_INIT", None)
# Use the small spaCy model in CI/tests for speed.
os.environ.setdefault("PIL_SPACY_MODEL", "en_core_web_sm")


def _alembic_config(sync_url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", sync_url)
    return cfg


@pytest.fixture(scope="session")
def alembic_db() -> Iterator[None]:
    """Apply migrations once for the test session."""
    sync_url = os.environ["PIL_DATABASE_URL_SYNC"]
    cfg = _alembic_config(sync_url)
    command.upgrade(cfg, "head")
    yield
    # Don't downgrade on teardown — the suite may be parallel; leave the
    # schema in place. CI tears down the container regardless.


@pytest_asyncio.fixture
async def db_session(alembic_db: None) -> AsyncIterator[AsyncSession]:
    """Async session bound to a freshly-built engine. We TRUNCATE the mutable
    tables at the end of each test so suites stay deterministic."""
    engine = create_async_engine(os.environ["PIL_DATABASE_URL"], future=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session
    # Wipe everything the test may have written. Order respects FKs.
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE TABLE request_payloads, requests, semantic_cache, "
                "api_keys, organizations RESTART IDENTITY CASCADE"
            )
        )
    await engine.dispose()


@pytest_asyncio.fixture
async def two_orgs(db_session: AsyncSession) -> tuple[UUID, UUID]:
    """Seed two orgs and return their ids."""
    from app.db.models import Organization
    from app.utils.crypto import new_dek, wrap_dek

    org_a = Organization(name="A Corp", encryption_key_wrapped=wrap_dek(new_dek()))
    org_b = Organization(name="B Corp", encryption_key_wrapped=wrap_dek(new_dek()))
    db_session.add_all([org_a, org_b])
    await db_session.commit()
    await db_session.refresh(org_a)
    await db_session.refresh(org_b)
    return org_a.id, org_b.id
