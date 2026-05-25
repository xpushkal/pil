"""Prometheus metrics.

Exposed at /metrics on the FastAPI app. The OTEL collector also exports a
mirrored set on :8889 for the compose stack — both are fine to scrape.

Labels are deliberately low-cardinality. Anything user-derived (prompt text,
request id) MUST NOT be used as a label.
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)

# Single registry so /metrics doesn't accidentally expose the default global
# registry's process collectors twice when uvicorn forks.
REGISTRY = CollectorRegistry(auto_describe=True)

requests_total = Counter(
    "pil_requests_total",
    "Total requests processed by PIL, labelled by upstream provider/model/status.",
    labelnames=("provider", "model", "status"),
    registry=REGISTRY,
)

request_duration_seconds = Histogram(
    "pil_request_duration_seconds",
    "Per-stage latency. ``stage`` is one of auth|pii_scrub|cache_lookup|llm_forward|audit_write|total.",
    labelnames=("stage",),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=REGISTRY,
)

pii_detections_total = Counter(
    "pil_pii_detections_total",
    "PII entities detected by the scrubber. Label is the entity category, never the value.",
    labelnames=("category",),
    registry=REGISTRY,
)

cache_hits_total = Counter(
    "pil_cache_hits_total",
    "Semantic cache hits.",
    labelnames=("provider", "model"),
    registry=REGISTRY,
)

cache_misses_total = Counter(
    "pil_cache_misses_total",
    "Semantic cache misses.",
    labelnames=("provider", "model"),
    registry=REGISTRY,
)

auth_failures_total = Counter(
    "pil_auth_failures_total",
    "Failed PIL key auth attempts grouped by reason.",
    labelnames=("reason",),
    registry=REGISTRY,
)


def render_latest() -> tuple[bytes, str]:
    """Return (payload, content_type) for the /metrics endpoint."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
