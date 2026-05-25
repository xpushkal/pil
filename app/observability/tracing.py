"""OpenTelemetry tracing bootstrap.

A single TracerProvider is configured at startup. Sprint 1 only wires the
plumbing; Sprint 2's pipeline emits the per-stage spans (pil.auth,
pil.pii_scrub, pil.cache_lookup, pil.llm_forward, pil.audit_write).

The collector applies an extra ``attributes/strip_payloads`` processor that
deletes any ``gen_ai.prompt``/``gen_ai.completion`` attributes that might
leak through, so even a buggy span has a defence-in-depth scrub.
"""

from __future__ import annotations

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.semconv.resource import ResourceAttributes

from app import __version__
from app.settings import get_settings

_PROVIDER_INSTALLED = False


def configure_tracing(app: FastAPI) -> None:
    """Install a global TracerProvider and instrument frameworks.

    Safe to call multiple times — only installs once per process.
    """
    global _PROVIDER_INSTALLED
    if _PROVIDER_INSTALLED:
        return

    settings = get_settings()

    resource = Resource.create(
        {
            ResourceAttributes.SERVICE_NAME: settings.otel_service_name,
            ResourceAttributes.SERVICE_VERSION: __version__,
            ResourceAttributes.DEPLOYMENT_ENVIRONMENT: settings.env,
        }
    )
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # excluded_urls keeps the /health spam out of traces.
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health/.*,metrics")
    HTTPXClientInstrumentor().instrument()
    RedisInstrumentor().instrument()
    SQLAlchemyInstrumentor().instrument(enable_commenter=False)

    _PROVIDER_INSTALLED = True


def get_tracer(name: str = "pil") -> trace.Tracer:
    return trace.get_tracer(name, __version__)
