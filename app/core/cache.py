"""Semantic response cache.

* Lookup: embed the *scrubbed* prompt, query pgvector with a cosine-similarity
  filter over (org_id, provider, model). If the best match's similarity
  exceeds the configured threshold (default ``0.92``), return its response.
* Write: store (embedding, response_payload) keyed by org/provider/model with
  ``expires_at = now() + cache_ttl_seconds``. TTL = 0 disables caching for
  that org (enterprise tier).
* Partitions: the table is declarative-partitioned in
  ``0001_initial.py``. Unknown ``(provider, model)`` pairs land in the
  provider's default leaf — Phase 2 cache writer is happy with that;
  partition-on-demand can be a Sprint 3 optimization.

We never cache **raw** PII-containing prompts. The proxy invokes
:meth:`SemanticCache.lookup` only after PII scrubbing, and writes only the
scrubbed-prompt embedding and the upstream response. Restoring PII in the
response happens upstream of the cache and again on serve.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.embeddings import EmbeddingService
from app.observability.metrics import cache_hits_total, cache_misses_total


@dataclass(frozen=True)
class CacheHit:
    response_payload: dict[str, Any]
    similarity: float
    cached_at: datetime


def hash_prompt(scrubbed_prompt: str) -> bytes:
    return hashlib.sha256(scrubbed_prompt.encode("utf-8")).digest()


def _vector_literal(vec: list[float]) -> str:
    """pgvector expects ``'[0.1,0.2,...]'`` as a string literal."""
    return "[" + ",".join(f"{v:.7f}" for v in vec) + "]"


class SemanticCache:
    """Wraps the ``semantic_cache`` table."""

    def __init__(self, embeddings: EmbeddingService) -> None:
        self._embeddings = embeddings

    async def lookup(
        self,
        session: AsyncSession,
        *,
        org_id: UUID,
        provider: str,
        model: str,
        scrubbed_prompt: str,
        similarity_threshold: float,
    ) -> CacheHit | None:
        """Return the closest matching cached response if cosine similarity
        is at or above the threshold; otherwise ``None``.
        """
        embedding = await self._embeddings.embed_one(scrubbed_prompt)
        vec = _vector_literal(embedding)

        # Cosine similarity = 1 - cosine distance. Filter expired entries.
        row = await session.execute(
            text(
                """
                SELECT response_payload, created_at,
                       1 - (embedding <=> (:vec)::vector) AS similarity
                  FROM semantic_cache
                 WHERE org_id = :org_id
                   AND provider = :provider
                   AND model = :model
                   AND expires_at > now()
                 ORDER BY embedding <=> (:vec)::vector ASC
                 LIMIT 1
                """
            ),
            {"vec": vec, "org_id": org_id, "provider": provider, "model": model},
        )
        record = row.first()
        if record is None:
            cache_misses_total.labels(provider=provider, model=model).inc()
            return None

        payload, created_at, similarity = record
        if similarity is None or float(similarity) < similarity_threshold:
            cache_misses_total.labels(provider=provider, model=model).inc()
            return None

        cache_hits_total.labels(provider=provider, model=model).inc()
        return CacheHit(
            response_payload=payload,
            similarity=float(similarity),
            cached_at=created_at,
        )

    async def store(
        self,
        session: AsyncSession,
        *,
        org_id: UUID,
        provider: str,
        model: str,
        scrubbed_prompt: str,
        response_payload: dict[str, Any],
        ttl_seconds: int,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            return  # caching disabled for this caller

        embedding = await self._embeddings.embed_one(scrubbed_prompt)
        vec = _vector_literal(embedding)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

        await session.execute(
            text(
                """
                INSERT INTO semantic_cache
                  (provider, model, org_id, prompt_hash, embedding,
                   response_payload, prompt_tokens, completion_tokens, expires_at)
                VALUES
                  (:provider, :model, :org_id, :prompt_hash, (:vec)::vector,
                   CAST(:response_payload AS jsonb), :prompt_tokens, :completion_tokens, :expires_at)
                """
            ),
            {
                "provider": provider,
                "model": model,
                "org_id": org_id,
                "prompt_hash": hash_prompt(scrubbed_prompt),
                "vec": vec,
                "response_payload": _as_json(response_payload),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "expires_at": expires_at,
            },
        )
        await session.commit()


def _as_json(value: dict[str, Any]) -> str:
    # orjson is available; use it for determinism.
    import orjson

    return orjson.dumps(value).decode("utf-8")
