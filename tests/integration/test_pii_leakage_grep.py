"""Grep harness: no fixture PII value may appear in any log line or span.

We run a prompt containing a curated set of "tripwire" values (real-looking
PII + dev secrets) through the full scrubber + audit path while capturing
both stdout (structlog JSON) and the OTEL in-process span exporter buffer.
After the run, we grep the captured text for every tripwire string and
assert zero hits.

Caveats:

* This test exercises the **scrubber's own logging + audit metadata writer**,
  not the full proxy. The proxy is exercised end-to-end in
  ``test_proxy_e2e.py``; this file isolates the *PII-must-not-leak*
  invariant from the rest of the moving parts.
* If a future change adds raw text to a log/span attribute, this test will
  flip red. That's the point.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout

import pytest
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core.audit import AuditEntry, write_audit
from app.core.auth import generate_key
from app.core.pii.scrubber import PIIScrubber
from app.db.models import ApiKey, Organization
from app.observability.logging import configure_logging, get_logger

pytestmark = pytest.mark.integration


# Tripwire values picked to be (a) clearly synthetic and (b) what a buggy
# logger might naïvely capture. The test does NOT depend on the scrubber
# actually detecting each one — it only asserts that the *raw* value never
# appears in logs/spans regardless of detection outcome.
TRIPWIRES = {
    "email": "alice.tripwire@example.com",
    "openai_key": "sk-" + "a" * 48,
    "github_token": "ghp_" + "Z" * 36,
    "aws_access_key": "AKIAIOSFODNN7EXAMPLE",
    "credit_card": "4532015112830366",
}


def _scrub_test_prompt() -> str:
    return (
        f"Email me at {TRIPWIRES['email']}. "
        f"My OpenAI key is {TRIPWIRES['openai_key']} and my GitHub token is "
        f"{TRIPWIRES['github_token']}. AWS access key {TRIPWIRES['aws_access_key']}. "
        f"Card on file: {TRIPWIRES['credit_card']}."
    )


def _setup_in_memory_tracing() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "pil-test"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return exporter


def _span_haystack(spans: list[ReadableSpan]) -> str:
    """Serialise every span (name, attributes, events) into one string blob."""
    parts: list[str] = []
    for span in spans:
        parts.append(span.name)
        for k, v in (span.attributes or {}).items():
            parts.append(f"{k}={v}")
        for event in span.events:
            parts.append(event.name)
            for k, v in (event.attributes or {}).items():
                parts.append(f"{k}={v}")
    return "\n".join(parts)


async def test_no_tripwire_appears_in_logs_or_spans(
    db_session, two_orgs
) -> None:
    org_a, _ = two_orgs
    org = await db_session.get(Organization, org_a)
    assert org is not None

    # Create a real API key so the audit FK resolves.
    issued = generate_key()
    api_key = ApiKey(
        org_id=org.id,
        name="tripwire-test",
        key_hash=issued.hash,
        key_suffix=issued.suffix,
    )
    db_session.add(api_key)
    await db_session.commit()
    await db_session.refresh(api_key)

    # Capture stdout from structlog + an in-memory span exporter
    configure_logging()
    log = get_logger("pii.leakage.test")
    span_exporter = _setup_in_memory_tracing()
    tracer = trace.get_tracer("pii.leakage.test")

    buf = io.StringIO()
    with redirect_stdout(buf):
        # Run scrubber
        scrubber = PIIScrubber()
        with tracer.start_as_current_span("pil.test.scrub") as span:
            result = scrubber.scrub(_scrub_test_prompt(), mode="reversible")
            # Defensive: log only counts/categories, NEVER the original text.
            log.info(
                "pii.scrub.done",
                entity_count=result.total_entities,
                categories=result.categories,
            )
            span.set_attribute("pil.pii.entity_count", result.total_entities)
            span.set_attribute("pil.pii.categories", ",".join(result.categories))

        # Run audit write with the same tripwire prompt (zero-retention org,
        # so no encrypted payload either)
        entry = AuditEntry(
            org_id=org.id,
            api_key_id=api_key.id,
            provider="openai",
            model="gpt-4o",
            scrubbed_prompt=result.scrubbed_text,
            response_body={"choices": [{"message": {"content": "ok"}}]},
            prompt_tokens=10,
            completion_tokens=2,
            estimated_cost_usd=0.0,
            latency_ms=42,
            cache_hit=False,
            pii_categories=result.categories,
            pii_entity_count=result.total_entities,
            error_code=None,
            trace_id=None,
        )
        await write_audit(db_session, org, entry)

    captured_stdout = buf.getvalue()
    span_blob = _span_haystack(span_exporter.get_finished_spans())
    haystack = captured_stdout + "\n" + span_blob

    leaks: list[tuple[str, str]] = []
    for label, value in TRIPWIRES.items():
        if value in haystack:
            leaks.append((label, value))

    assert not leaks, (
        "PII tripwire(s) leaked into logs/spans:\n"
        + "\n".join(f"  - {label}: {value!r}" for label, value in leaks)
        + "\n\nFull captured output for triage:\n"
        + haystack[:4000]
    )
