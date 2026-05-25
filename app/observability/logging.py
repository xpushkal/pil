"""Structured JSON logging via structlog.

Every log record is bound to the current request_id and trace_id (if any).
No file logging — stdout only — so the host's log shipper has a single source.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from opentelemetry import trace

from app.settings import get_settings
from app.utils.request_id import current_request_id


def _inject_request_id(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    rid = current_request_id()
    if rid is not None:
        event_dict.setdefault("request_id", rid)
    return event_dict


def _inject_trace_context(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    span = trace.get_current_span()
    span_context = span.get_span_context() if span else None
    if span_context and span_context.is_valid:
        event_dict.setdefault("trace_id", f"{span_context.trace_id:032x}")
        event_dict.setdefault("span_id", f"{span_context.span_id:016x}")
    return event_dict


def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
        force=True,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _inject_request_id,
            _inject_trace_context,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
