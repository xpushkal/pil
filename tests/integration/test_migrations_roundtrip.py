"""Forward + backward migration round-trip against a real Postgres.

Asserts:
  * Alembic upgrade head succeeds against an empty DB.
  * All Sprint 1 tables exist (incl. the parent partitioned semantic_cache
    and its provider-level partitions).
  * Pre-warmed model leaf partitions exist for each known provider/model pair.
  * Downgrade base wipes the schema.

Marked ``integration`` — runs in CI with a service Postgres on :5432.
"""

from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

pytestmark = pytest.mark.integration


REQUIRED_TABLES = {
    "organizations",
    "api_keys",
    "requests",
    "request_payloads",
    "semantic_cache",
    "semantic_cache_openai",
    "semantic_cache_anthropic",
    "semantic_cache_gemini",
    "semantic_cache_default",
}

EXPECTED_LEAF_PARTITIONS = {
    "semantic_cache_openai_gpt_4o",
    "semantic_cache_openai_gpt_4o_mini",
    "semantic_cache_openai_gpt_4_turbo",
    "semantic_cache_anthropic_claude_3_5_sonnet_20241022",
    "semantic_cache_anthropic_claude_3_5_haiku_20241022",
    "semantic_cache_anthropic_claude_3_opus_20240229",
    "semantic_cache_gemini_gemini_1_5_pro",
    "semantic_cache_gemini_gemini_1_5_flash",
}


def _alembic_config() -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option(
        "sqlalchemy.url",
        os.environ["PIL_DATABASE_URL_SYNC"],
    )
    return cfg


def test_upgrade_then_downgrade_roundtrip() -> None:
    cfg = _alembic_config()

    command.upgrade(cfg, "head")

    sync_url = os.environ["PIL_DATABASE_URL_SYNC"]
    engine = create_engine(sync_url)
    try:
        inspector = inspect(engine)
        existing = set(inspector.get_table_names())
        missing = REQUIRED_TABLES - existing
        assert not missing, f"required tables missing after upgrade: {missing}"

        # Pre-warmed leaf partitions
        missing_leaves = EXPECTED_LEAF_PARTITIONS - existing
        assert not missing_leaves, f"pre-warmed leaf partitions missing: {missing_leaves}"

        # Extensions live
        with engine.connect() as conn:
            exts = {row[0] for row in conn.execute(text("SELECT extname FROM pg_extension"))}
            assert "vector" in exts
            assert "pgcrypto" in exts
    finally:
        engine.dispose()

    command.downgrade(cfg, "base")

    engine = create_engine(sync_url)
    try:
        inspector = inspect(engine)
        remaining = set(inspector.get_table_names())
        assert REQUIRED_TABLES.isdisjoint(remaining), (
            f"downgrade left tables behind: {REQUIRED_TABLES & remaining}"
        )
    finally:
        engine.dispose()
