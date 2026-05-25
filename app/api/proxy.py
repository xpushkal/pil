"""Provider-passthrough proxy endpoints.

The full Sprint 1 pipeline runs here for every inbound request:

    auth -> rate-limit -> PII scrub -> cache lookup -> upstream forward
         -> PII restore -> audit log -> response with X-PIL-* headers

The four headers PIL guarantees per request:

* ``X-PIL-Request-Id``  — UUID for correlating with traces/logs.
* ``X-PIL-PII-Entities`` — comma-separated entity categories detected.
* ``X-PIL-Cache-Hit``    — ``true`` | ``false``.
* ``X-PIL-Latency-Ms``   — total time the proxy spent on this request.

Sprint 2 adds X-PIL-Tokens-In/Out/Saved/Compression-Ratio.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import orjson
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import db_session, enforce_rate_limit
from app.core.audit import AuditEntry, write_audit
from app.core.auth import AuthenticatedKey
from app.core.pii.scrubber import ScrubResult
from app.core.pipeline import (
    get_http_client,
    get_reversible_store,
    get_scrubber,
    get_semantic_cache,
)
from app.core.providers.base import ProviderAdapter
from app.core.providers.registry import get_adapter, known_providers
from app.observability.logging import get_logger
from app.observability.metrics import request_duration_seconds, requests_total
from app.settings import get_settings
from app.utils.request_id import current_request_id

log = get_logger("pil.proxy")
router = APIRouter(tags=["proxy"])
tracer = trace.get_tracer("pil.proxy")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _classify_provider_from_path(path: str) -> tuple[str, str]:
    """Map an inbound path to ``(provider, upstream_path)``.

    ``/openai/v1/chat/completions`` -> ``("openai", "/v1/chat/completions")``.
    """
    for provider in known_providers():
        prefix = f"/{provider}/"
        if path.startswith(prefix):
            return provider, path[len(provider) + 1 :]
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "UNKNOWN_PROVIDER_PATH", "path": path},
    )


def _is_stream_request(provider: str, body: dict, path: str) -> bool:
    if provider == "gemini":
        return "streamGenerateContent" in path or body.get("stream") is True
    return bool(body.get("stream"))


def _pii_header_value(scrub: ScrubResult) -> str:
    if not scrub.category_counts:
        return ""
    return ",".join(f"{cat}:{cnt}" for cat, cnt in sorted(scrub.category_counts.items()))


async def _run_pipeline(
    *,
    request: Request,
    auth: AuthenticatedKey,
    session: AsyncSession,
    adapter: ProviderAdapter,
    upstream_path: str,
) -> Response:
    settings = get_settings()
    request_id = current_request_id() or "unknown"
    started = time.perf_counter()

    try:
        body_bytes = await request.body()
        body = orjson.loads(body_bytes) if body_bytes else {}
    except orjson.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "INVALID_JSON", "message": str(exc)},
        ) from exc

    model = body.get("model")
    if not model and adapter.name == "gemini":
        # Model is in the path for Gemini: /gemini/v1beta/models/<model>:generateContent
        for segment in upstream_path.split("/"):
            if ":" in segment:
                model = segment.split(":", 1)[0]
                break
            if segment.startswith("gemini-"):
                model = segment
    if not isinstance(model, str) or not model:
        raise HTTPException(
            status_code=400,
            detail={"error": "MODEL_REQUIRED"},
        )

    stream = _is_stream_request(adapter.name, body, upstream_path)
    pii_mode = settings.pii_mode

    # --- PII scrub -------------------------------------------------------
    scrubber = get_scrubber()
    with tracer.start_as_current_span("pil.pii_scrub") as span:
        scrub_started = time.perf_counter()
        try:
            scrubbed_body, scrub = adapter.scrub_request(scrubber, body, mode=pii_mode)
        except Exception as exc:  # noqa: BLE001
            log.error("pil.pii_scrub_failed", error_type=type(exc).__name__)
            if settings.pii_fail_closed:
                raise HTTPException(
                    status_code=502,
                    detail={"error": "PII_SCRUBBER_UNAVAILABLE"},
                ) from exc
            scrubbed_body, scrub = body, ScrubResult("", {}, [])
        span.set_attribute("pil.pii.entity_count", scrub.total_entities)
        span.set_attribute("pil.pii.categories", ",".join(scrub.categories))
        request_duration_seconds.labels(stage="pii_scrub").observe(
            time.perf_counter() - scrub_started
        )

    # --- Cache lookup ----------------------------------------------------
    cache = get_semantic_cache()
    org = auth.organization
    threshold = org.cache_similarity_threshold or settings.cache_similarity_threshold
    ttl = org.cache_ttl_seconds if org.cache_ttl_seconds is not None else settings.cache_ttl_seconds

    cache_hit_payload = None
    cache_hit_flag = False

    # We cache only non-streaming responses in Sprint 1; streaming is too
    # complex to serialise for now. TODO(sprint-2): cache the assembled stream.
    if not stream and ttl > 0:
        from app.core.tokenization import extract_prompt_text

        cache_key_text = extract_prompt_text(adapter.name, scrubbed_body)
        with tracer.start_as_current_span("pil.cache_lookup") as span:
            cache_started = time.perf_counter()
            cache_hit = await cache.lookup(
                session,
                org_id=org.id,
                provider=adapter.name,
                model=model,
                scrubbed_prompt=cache_key_text,
                similarity_threshold=threshold,
            )
            request_duration_seconds.labels(stage="cache_lookup").observe(
                time.perf_counter() - cache_started
            )
            if cache_hit is not None:
                cache_hit_flag = True
                cache_hit_payload = cache_hit.response_payload
                span.set_attribute("pil.cache.similarity", cache_hit.similarity)

    # Stage reversible mapping in Redis for restore-on-response.
    rev_store = get_reversible_store()
    if pii_mode == "reversible" and scrub.placeholders_to_originals:
        await rev_store.put(request_id, scrub.placeholders_to_originals)

    # --- Cache hit short-circuit ----------------------------------------
    if cache_hit_payload is not None:
        restored = (
            adapter.restore_response_body(cache_hit_payload, scrub.placeholders_to_originals)
            if pii_mode == "reversible"
            else cache_hit_payload
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        await _emit_audit(
            session, auth, adapter, model, scrubbed_body, restored,
            scrub=scrub, cache_hit=True, latency_ms=latency_ms,
            error_code=None,
        )
        await rev_store.drop(request_id)
        request_duration_seconds.labels(stage="total").observe(latency_ms / 1000)
        requests_total.labels(provider=adapter.name, model=model, status="200").inc()
        return _json_response(restored, 200, scrub=scrub, cache_hit=True, started_at=started)

    # --- Forward to upstream --------------------------------------------
    upstream_url = adapter.upstream_url(upstream_path)
    upstream_headers = adapter.upstream_headers(dict(request.headers))
    client = get_http_client()

    if stream:
        return await _stream_response(
            request=request, session=session, adapter=adapter, model=model,
            scrubbed_body=scrubbed_body, scrub=scrub,
            upstream_url=upstream_url, upstream_headers=upstream_headers,
            auth=auth, started=started, pii_mode=pii_mode, request_id=request_id,
        )

    with tracer.start_as_current_span("pil.llm_forward"):
        forward_started = time.perf_counter()
        result = await adapter.forward(
            client, url=upstream_url, headers=upstream_headers, body=scrubbed_body
        )
        request_duration_seconds.labels(stage="llm_forward").observe(
            time.perf_counter() - forward_started
        )

    error_code: str | None = None
    if result.status_code >= 400:
        error_code = f"upstream_{result.status_code}"

    # Restore PII in the response body.
    restored_body = (
        adapter.restore_response_body(result.body, scrub.placeholders_to_originals)
        if pii_mode == "reversible"
        else result.body
    )

    # Persist to cache (only on success).
    if (
        result.status_code < 300
        and not stream
        and ttl > 0
    ):
        try:
            from app.core.tokenization import extract_prompt_text

            await cache.store(
                session,
                org_id=org.id,
                provider=adapter.name,
                model=model,
                scrubbed_prompt=extract_prompt_text(adapter.name, scrubbed_body),
                response_payload=result.body,  # store the upstream (placeholder-bearing) version
                ttl_seconds=ttl,
            )
        except Exception:  # noqa: BLE001 — cache write is best-effort
            log.warning("pil.cache_write_failed", request_id=request_id)

    latency_ms = int((time.perf_counter() - started) * 1000)
    await _emit_audit(
        session, auth, adapter, model, scrubbed_body, result.body,
        scrub=scrub, cache_hit=False, latency_ms=latency_ms,
        error_code=error_code,
    )
    await rev_store.drop(request_id)
    request_duration_seconds.labels(stage="total").observe(latency_ms / 1000)
    requests_total.labels(provider=adapter.name, model=model, status=str(result.status_code)).inc()
    return _json_response(restored_body, result.status_code, scrub=scrub, cache_hit=False, started_at=started)


async def _stream_response(
    *,
    request: Request,
    session: AsyncSession,
    adapter: ProviderAdapter,
    model: str,
    scrubbed_body: dict,
    scrub: ScrubResult,
    upstream_url: str,
    upstream_headers: dict[str, str],
    auth: AuthenticatedKey,
    started: float,
    pii_mode: str,
    request_id: str,
) -> StreamingResponse:
    client = get_http_client()
    mapping = scrub.placeholders_to_originals if pii_mode == "reversible" else {}

    async def gen() -> AsyncIterator[bytes]:
        try:
            async for chunk in adapter.forward_stream(
                client, url=upstream_url, headers=upstream_headers, body=scrubbed_body
            ):
                yield adapter.restore_stream_chunk(chunk, mapping)
        finally:
            latency_ms = int((time.perf_counter() - started) * 1000)
            # Audit even for streaming — body is not captured (would require
            # SSE parsing); we record metadata only.
            try:
                await _emit_audit(
                    session, auth, adapter, model, scrubbed_body, {},
                    scrub=scrub, cache_hit=False, latency_ms=latency_ms,
                    error_code=None,
                )
            except Exception:  # noqa: BLE001
                log.warning("pil.audit_streaming_failed", request_id=request_id)
            await get_reversible_store().drop(request_id)
            request_duration_seconds.labels(stage="total").observe(latency_ms / 1000)
            requests_total.labels(provider=adapter.name, model=model, status="stream").inc()

    headers = _pil_headers(scrub=scrub, cache_hit=False, started_at=started)
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


async def _emit_audit(
    session: AsyncSession,
    auth: AuthenticatedKey,
    adapter: ProviderAdapter,
    model: str,
    scrubbed_body: dict,
    response_body: dict,
    *,
    scrub: ScrubResult,
    cache_hit: bool,
    latency_ms: int,
    error_code: str | None,
) -> None:
    from opentelemetry import trace as _trace

    span = _trace.get_current_span()
    span_ctx = span.get_span_context() if span else None
    trace_id = f"{span_ctx.trace_id:032x}" if span_ctx and span_ctx.is_valid else None

    prompt_tokens = adapter.count_request_tokens(model, scrubbed_body)
    completion_tokens = adapter.count_response_tokens(model, response_body) if response_body else 0

    entry = AuditEntry(
        org_id=auth.organization.id,
        api_key_id=auth.api_key.id,
        provider=adapter.name,
        model=model,
        scrubbed_prompt=orjson.dumps(scrubbed_body).decode("utf-8"),
        response_body=response_body,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        estimated_cost_usd=0.0,  # TODO(sprint-2): wire a price table
        latency_ms=latency_ms,
        cache_hit=cache_hit,
        pii_categories=sorted(scrub.category_counts),
        pii_entity_count=scrub.total_entities,
        error_code=error_code,
        trace_id=trace_id,
    )
    with tracer.start_as_current_span("pil.audit_write"):
        await write_audit(session, auth.organization, entry)


def _pil_headers(*, scrub: ScrubResult, cache_hit: bool, started_at: float) -> dict[str, str]:
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    return {
        "X-PIL-PII-Entities": _pii_header_value(scrub),
        "X-PIL-Cache-Hit": "true" if cache_hit else "false",
        "X-PIL-Latency-Ms": str(elapsed_ms),
    }


def _json_response(
    body: dict, status_code: int, *, scrub: ScrubResult, cache_hit: bool, started_at: float
) -> Response:
    return Response(
        content=orjson.dumps(body),
        status_code=status_code,
        media_type="application/json",
        headers=_pil_headers(scrub=scrub, cache_hit=cache_hit, started_at=started_at),
    )


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
@router.post("/openai/{path:path}")
async def openai_proxy(
    path: str,
    request: Request,
    auth: AuthenticatedKey = Depends(enforce_rate_limit),
    session: AsyncSession = Depends(db_session),
) -> Response:
    adapter = get_adapter("openai")
    return await _run_pipeline(
        request=request, auth=auth, session=session,
        adapter=adapter, upstream_path=f"/{path}",
    )


@router.post("/anthropic/{path:path}")
async def anthropic_proxy(
    path: str,
    request: Request,
    auth: AuthenticatedKey = Depends(enforce_rate_limit),
    session: AsyncSession = Depends(db_session),
) -> Response:
    adapter = get_adapter("anthropic")
    return await _run_pipeline(
        request=request, auth=auth, session=session,
        adapter=adapter, upstream_path=f"/{path}",
    )


@router.post("/gemini/{path:path}")
async def gemini_proxy(
    path: str,
    request: Request,
    auth: AuthenticatedKey = Depends(enforce_rate_limit),
    session: AsyncSession = Depends(db_session),
) -> Response:
    adapter = get_adapter("gemini")
    return await _run_pipeline(
        request=request, auth=auth, session=session,
        adapter=adapter, upstream_path=f"/{path}",
    )


@router.post("/v1/messages")
async def generic_proxy(
    request: Request,
    auth: AuthenticatedKey = Depends(enforce_rate_limit),
    session: AsyncSession = Depends(db_session),
    x_pil_provider: str | None = Header(default=None, alias="X-PIL-Provider"),
) -> Response:
    """Generic endpoint that picks the upstream via ``X-PIL-Provider``.

    Body shape must match the chosen provider's native body shape — PIL is
    a drop-in, not a translator.
    """
    if not x_pil_provider:
        raise HTTPException(
            status_code=400,
            detail={"error": "PROVIDER_HEADER_REQUIRED", "header": "X-PIL-Provider"},
        )
    try:
        adapter = get_adapter(x_pil_provider)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "UNKNOWN_PROVIDER", "provider": x_pil_provider},
        ) from exc

    # Map this generic endpoint to the adapter's native path.
    native_path = {
        "openai": "/v1/chat/completions",
        "anthropic": "/v1/messages",
        "gemini": "/v1beta/models",  # caller must include model in the body via ``model``
    }[adapter.name]
    return await _run_pipeline(
        request=request, auth=auth, session=session,
        adapter=adapter, upstream_path=native_path,
    )
