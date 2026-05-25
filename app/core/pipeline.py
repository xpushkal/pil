"""Process-wide singletons + lifecycle for the proxy pipeline.

The expensive things (PII scrubber's spaCy model, the embedding model, the
shared httpx client) live here and are bound at startup. The proxy handlers
import these getters; tests can override via :func:`reset_for_tests`.
"""

from __future__ import annotations

import httpx

from app.core.cache import SemanticCache
from app.core.embeddings import EmbeddingService, get_embeddings, init_embeddings
from app.core.pii.reversible_store import ReversibleStore
from app.core.pii.scrubber import PIIScrubber
from app.observability.logging import get_logger
from app.utils.redis_client import get_redis

log = get_logger("pil.pipeline")

_SCRUBBER: PIIScrubber | None = None
_HTTP_CLIENT: httpx.AsyncClient | None = None
_CACHE: SemanticCache | None = None


async def init_pipeline() -> None:
    """Build every singleton. Called from ``app.main.lifespan``."""
    global _SCRUBBER, _HTTP_CLIENT, _CACHE
    if _SCRUBBER is None:
        log.info("pil.scrubber.loading")
        _SCRUBBER = PIIScrubber()
        log.info("pil.scrubber.ready", entities=list(_SCRUBBER.allowed_entities))

    embeddings: EmbeddingService = init_embeddings()
    if _CACHE is None:
        _CACHE = SemanticCache(embeddings=embeddings)

    if _HTTP_CLIENT is None:
        _HTTP_CLIENT = httpx.AsyncClient(
            http2=True,
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=10.0),
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        )


async def shutdown_pipeline() -> None:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is not None:
        await _HTTP_CLIENT.aclose()
        _HTTP_CLIENT = None


def get_scrubber() -> PIIScrubber:
    if _SCRUBBER is None:
        raise RuntimeError("pipeline not initialized")
    return _SCRUBBER


def get_http_client() -> httpx.AsyncClient:
    if _HTTP_CLIENT is None:
        raise RuntimeError("pipeline not initialized")
    return _HTTP_CLIENT


def get_semantic_cache() -> SemanticCache:
    if _CACHE is None:
        raise RuntimeError("pipeline not initialized")
    return _CACHE


def get_reversible_store() -> ReversibleStore:
    return ReversibleStore(get_redis())


def reset_for_tests() -> None:
    global _SCRUBBER, _HTTP_CLIENT, _CACHE
    _SCRUBBER = None
    _CACHE = None
    _HTTP_CLIENT = None
