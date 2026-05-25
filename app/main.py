"""FastAPI entrypoint for the PIL proxy.

Sprint 1 wires the bones: settings, logging, tracing, metrics, health probes.
The proxy/PII/cache pipeline lands in Phase 2 of this sprint.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app import __version__
from app.api.health import router as health_router
from app.observability.logging import configure_logging, get_logger
from app.observability.metrics import render_latest
from app.observability.tracing import configure_tracing
from app.settings import get_settings
from app.utils.redis_client import close_redis
from app.utils.request_id import new_request_id, request_id_ctx


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Bind a per-request UUID into a contextvar and echo it back to the client."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        rid = request.headers.get("x-pil-request-id") or new_request_id()
        token = request_id_ctx.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token)
        response.headers["X-PIL-Request-Id"] = rid
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    configure_tracing(app)
    log = get_logger("pil.lifespan")
    log.info("pil.startup", version=__version__, env=get_settings().env)
    try:
        yield
    finally:
        await close_redis()
        log.info("pil.shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="PIL",
        version=__version__,
        description="Privacy-preserving LLM proxy.",
        lifespan=lifespan,
        default_response_class=Response,
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.add_middleware(RequestIdMiddleware)
    app.include_router(health_router)

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        payload, content_type = render_latest()
        return Response(content=payload, media_type=content_type)

    return app


app = create_app()
