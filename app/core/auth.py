"""PIL key authentication.

PIL keys live in the ``api_keys`` table. We never store the plaintext token,
only an argon2id hash. Verifying an inbound ``X-PIL-Key`` header requires
hashing-then-comparing against every active key — but argon2 is expensive,
so we shard candidates by ``key_suffix`` (last 4 chars of plaintext, kept
on the row for fast lookup; not secret).

Rotation grace: when a key is rotated, the new key gets ``rotated_from_id``
pointing at the old, and the old key's ``grace_expires_at`` is set to
``now() + key_rotation_grace_hours``. Both keys validate during the window;
after expiry, the old key is treated as revoked.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Final

from argon2 import PasswordHasher
from argon2 import exceptions as argon2_exceptions
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApiKey, Organization
from app.settings import get_settings

KEY_PREFIX: Final[str] = "pil-"
SUFFIX_LEN: Final[int] = 4

# Argon2id parameters tuned for laptop-class workers; OWASP 2024 minimum.
_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,  # 64 MiB
    parallelism=4,
    hash_len=32,
    salt_len=16,
)


@dataclass(frozen=True)
class IssuedKey:
    """Returned to the caller of :func:`generate_key` exactly once."""

    plaintext: str
    suffix: str
    hash: str


@dataclass(frozen=True)
class AuthenticatedKey:
    """Successful auth result. ``api_key`` is the resolved row."""

    api_key: ApiKey
    organization: Organization


class AuthError(Exception):
    """Auth failed. ``reason`` is a low-cardinality label for metrics."""

    def __init__(self, reason: str, *, message: str | None = None) -> None:
        super().__init__(message or reason)
        self.reason = reason


def generate_key() -> IssuedKey:
    """Mint a fresh ``pil-<urlsafe>`` token + its argon2 hash."""
    token = KEY_PREFIX + secrets.token_urlsafe(24)
    suffix = token[-SUFFIX_LEN:]
    return IssuedKey(plaintext=token, suffix=suffix, hash=_HASHER.hash(token))


def hash_key(plaintext: str) -> str:
    """Public wrapper for tests."""
    return _HASHER.hash(plaintext)


async def authenticate(session: AsyncSession, presented: str) -> AuthenticatedKey:
    """Resolve a presented ``X-PIL-Key`` to its row, or raise :class:`AuthError`.

    Raises with ``reason`` ∈ {malformed, unknown, revoked, grace_expired}.
    """
    if not presented or not presented.startswith(KEY_PREFIX) or len(presented) < len(KEY_PREFIX) + SUFFIX_LEN:
        raise AuthError("malformed")

    suffix = presented[-SUFFIX_LEN:]
    now = datetime.now(timezone.utc)

    rows = await session.execute(
        select(ApiKey).where(ApiKey.key_suffix == suffix)
    )
    candidates = list(rows.scalars())
    if not candidates:
        raise AuthError("unknown")

    matched: ApiKey | None = None
    for candidate in candidates:
        try:
            _HASHER.verify(candidate.key_hash, presented)
        except argon2_exceptions.VerifyMismatchError:
            continue
        except argon2_exceptions.InvalidHash:
            continue
        matched = candidate
        break

    if matched is None:
        raise AuthError("unknown")

    if matched.revoked_at is not None and matched.revoked_at <= now:
        # A revoked-then-rotated key may still serve traffic until the grace
        # window expires; that's expressed via grace_expires_at, not revoked_at.
        raise AuthError("revoked")
    if matched.grace_expires_at is not None and matched.grace_expires_at <= now:
        raise AuthError("grace_expired")

    org = await session.get(Organization, matched.org_id)
    if org is None:
        # Shouldn't happen — FK guarantees it — but guard anyway.
        raise AuthError("unknown")

    return AuthenticatedKey(api_key=matched, organization=org)


def grace_expiry_from_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(
        hours=get_settings().key_rotation_grace_hours
    )
