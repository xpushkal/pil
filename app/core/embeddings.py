"""Embedding service.

Loads ``sentence-transformers/all-MiniLM-L6-v2`` once at startup and exposes a
small async-friendly API. Sprint 2's RAG retrieval and intent classifier both
call the same instance — don't create a second one.

Device selection (``PIL_EMBEDDING_DEVICE=auto``):

* ``mps`` if available (M1/M2/M3/M4 Macs)
* ``cuda`` otherwise if available
* ``cpu`` fallback

Encoding runs the underlying SBERT model in a thread (it releases the GIL
during inference), so callers can ``await embed_one(...)`` without blocking
the event loop.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.observability.logging import get_logger
from app.settings import get_settings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

log = get_logger("pil.embeddings")

EMBEDDING_DIM = 384


@dataclass
class EmbeddingService:
    """Wraps a SentenceTransformer instance with PIL-specific glue."""

    model: SentenceTransformer
    device: str
    dim: int = EMBEDDING_DIM

    async def embed_one(self, text: str) -> list[float]:
        result = await self.embed_many([text])
        return result[0]

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # ``encode`` is CPU/GPU-bound; run it off the event loop.
        loop = asyncio.get_running_loop()
        vectors = await loop.run_in_executor(
            None, self._encode_sync, texts
        )
        return vectors

    def _encode_sync(self, texts: list[str]) -> list[list[float]]:
        # normalize_embeddings=True → cosine similarity == dot product.
        result = self.model.encode(
            texts,
            batch_size=64,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [vec.tolist() for vec in result]


_INSTANCE: EmbeddingService | None = None


def _resolve_device(preference: str) -> str:
    if preference != "auto":
        return preference
    try:
        import torch  # local import keeps cold start fast in non-ML codepaths

        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except Exception:  # noqa: BLE001
        log.warning("embeddings.device_probe_failed", fallback="cpu")
    return "cpu"


def init_embeddings() -> EmbeddingService:
    """Load the model. Safe to call once at startup; idempotent."""
    global _INSTANCE
    if _INSTANCE is not None:
        return _INSTANCE

    from sentence_transformers import SentenceTransformer

    settings = get_settings()
    device = _resolve_device(settings.embedding_device)
    log.info("embeddings.loading", model=settings.embedding_model, device=device)
    model = SentenceTransformer(settings.embedding_model, device=device)
    log.info("embeddings.loaded", dim=model.get_sentence_embedding_dimension())
    _INSTANCE = EmbeddingService(model=model, device=device, dim=EMBEDDING_DIM)
    return _INSTANCE


def get_embeddings() -> EmbeddingService:
    if _INSTANCE is None:
        raise RuntimeError("embeddings service not initialized; call init_embeddings() at startup")
    return _INSTANCE


def _reset_for_tests() -> None:
    """Test-only helper. Don't call in production code."""
    global _INSTANCE
    _INSTANCE = None
