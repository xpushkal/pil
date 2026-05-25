"""Per-request ID generation and a contextvar binding used by logging/metrics.

The request ID is also surfaced to clients as the ``X-PIL-Request-Id`` response
header so we can correlate a curl trace with a span in Jaeger.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar

request_id_ctx: ContextVar[str | None] = ContextVar("pil_request_id", default=None)


def new_request_id() -> str:
    return uuid.uuid4().hex


def current_request_id() -> str | None:
    return request_id_ctx.get()
