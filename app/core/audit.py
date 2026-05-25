"""Audit log writer.

Default policy is **zero-retention of content** — write a ``requests`` row
with metadata only. If the owning org has ``raw_logging_opt_in = true``, we
also write a ``request_payloads`` row with AES-256-GCM ciphertext of the
*scrubbed* prompt and the response under the org's wrapped DEK. Each
ciphertext bytea is ``nonce || ct_with_tag`` so the prompt and response use
independently-random nonces.

We never log raw plaintext to traces, metrics, or stdout. Counts and
categories only.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import orjson
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Organization, RequestLog, RequestPayload
from app.observability.logging import get_logger
from app.utils.crypto import encrypt_with_dek, unwrap_dek

log = get_logger("pil.audit")


@dataclass
class AuditEntry:
    org_id: UUID
    api_key_id: UUID
    provider: str
    model: str
    scrubbed_prompt: str
    response_body: dict[str, Any]
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float
    latency_ms: int
    cache_hit: bool
    pii_categories: list[str]
    pii_entity_count: int
    error_code: str | None
    trace_id: str | None


def _seal(dek: bytes, plaintext: bytes, *, aad: bytes) -> bytes:
    env = encrypt_with_dek(dek, plaintext, aad=aad)
    return env.nonce + env.ciphertext


async def write_audit(
    session: AsyncSession,
    org: Organization,
    entry: AuditEntry,
) -> UUID:
    """Persist a request audit row + (opt-in) encrypted payload row.

    Returns the new ``requests.id``.
    """
    request_hash = hashlib.sha256(entry.scrubbed_prompt.encode("utf-8")).digest()

    row = RequestLog(
        org_id=entry.org_id,
        api_key_id=entry.api_key_id,
        request_hash=request_hash,
        provider=entry.provider,
        model=entry.model,
        prompt_tokens=entry.prompt_tokens,
        completion_tokens=entry.completion_tokens,
        estimated_cost_usd=entry.estimated_cost_usd,
        latency_ms=entry.latency_ms,
        cache_hit=entry.cache_hit,
        pii_categories=list(entry.pii_categories),
        pii_entity_count=entry.pii_entity_count,
        error_code=entry.error_code,
        trace_id=entry.trace_id,
    )
    session.add(row)
    await session.flush()  # populate row.id

    if org.raw_logging_opt_in and org.encryption_key_wrapped:
        try:
            dek = unwrap_dek(bytes(org.encryption_key_wrapped))
            payload = RequestPayload(
                request_id=row.id,
                prompt_ciphertext=_seal(
                    dek, entry.scrubbed_prompt.encode("utf-8"), aad=b"pil:prompt:v1"
                ),
                response_ciphertext=_seal(
                    dek, orjson.dumps(entry.response_body), aad=b"pil:response:v1"
                ),
            )
            session.add(payload)
        except Exception:  # noqa: BLE001 — audit must never fail the user request
            log.warning("audit.payload_encrypt_failed", request_id=str(row.id))

    await session.commit()
    return row.id
