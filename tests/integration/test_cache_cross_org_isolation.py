"""Cross-org cache isolation — the cache must never leak across orgs.

Two orgs write semantically identical prompts to the same (provider, model)
partition. Lookups from each org's id must return only that org's entry,
never the other's, even when the embedding vectors are byte-identical.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import SemanticCache
from app.core.embeddings import init_embeddings

pytestmark = pytest.mark.integration


class _DeterministicEmbeddings:
    """Stand-in for the real embedding service. Returns a fixed vector so the
    cache lookup short-circuits on similarity = 1.0 — that way the test
    asserts isolation independently of model quality."""

    dim = 384

    async def embed_one(self, text: str) -> list[float]:
        # Vector is the same for every call. Cosine similarity to itself = 1.0.
        return [1.0] + [0.0] * (self.dim - 1)

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed_one(t) for t in texts]


async def test_orgs_do_not_see_each_others_cache(
    db_session: AsyncSession, two_orgs: tuple[UUID, UUID]
) -> None:
    org_a, org_b = two_orgs
    cache = SemanticCache(embeddings=_DeterministicEmbeddings())  # type: ignore[arg-type]

    # Both orgs write to (openai, gpt-4o) with semantically-identical prompts.
    await cache.store(
        db_session,
        org_id=org_a,
        provider="openai",
        model="gpt-4o",
        scrubbed_prompt="what is the meaning of life?",
        response_payload={"choices": [{"message": {"content": "for org A"}}]},
        ttl_seconds=3600,
    )
    await cache.store(
        db_session,
        org_id=org_b,
        provider="openai",
        model="gpt-4o",
        scrubbed_prompt="what is the meaning of life?",
        response_payload={"choices": [{"message": {"content": "for org B"}}]},
        ttl_seconds=3600,
    )

    hit_a = await cache.lookup(
        db_session,
        org_id=org_a,
        provider="openai",
        model="gpt-4o",
        scrubbed_prompt="what is the meaning of life?",
        similarity_threshold=0.90,
    )
    hit_b = await cache.lookup(
        db_session,
        org_id=org_b,
        provider="openai",
        model="gpt-4o",
        scrubbed_prompt="what is the meaning of life?",
        similarity_threshold=0.90,
    )

    assert hit_a is not None and hit_a.response_payload["choices"][0]["message"]["content"] == "for org A"
    assert hit_b is not None and hit_b.response_payload["choices"][0]["message"]["content"] == "for org B"


async def test_unknown_org_sees_nothing(
    db_session: AsyncSession, two_orgs: tuple[UUID, UUID]
) -> None:
    from uuid import uuid4

    org_a, _ = two_orgs
    cache = SemanticCache(embeddings=_DeterministicEmbeddings())  # type: ignore[arg-type]

    await cache.store(
        db_session,
        org_id=org_a,
        provider="openai",
        model="gpt-4o",
        scrubbed_prompt="confidential question",
        response_payload={"choices": [{"message": {"content": "secret answer"}}]},
        ttl_seconds=3600,
    )

    miss = await cache.lookup(
        db_session,
        org_id=uuid4(),
        provider="openai",
        model="gpt-4o",
        scrubbed_prompt="confidential question",
        similarity_threshold=0.90,
    )
    assert miss is None


async def test_partition_isolation_per_provider_model(
    db_session: AsyncSession, two_orgs: tuple[UUID, UUID]
) -> None:
    """Even within one org, a cache write to (openai, gpt-4o) must not surface
    when querying (anthropic, claude-3-5-sonnet)."""
    org_a, _ = two_orgs
    cache = SemanticCache(embeddings=_DeterministicEmbeddings())  # type: ignore[arg-type]

    await cache.store(
        db_session,
        org_id=org_a,
        provider="openai",
        model="gpt-4o",
        scrubbed_prompt="x",
        response_payload={"r": "openai"},
        ttl_seconds=3600,
    )

    hit = await cache.lookup(
        db_session,
        org_id=org_a,
        provider="anthropic",
        model="claude-3-5-sonnet-20241022",
        scrubbed_prompt="x",
        similarity_threshold=0.50,
    )
    assert hit is None
