"""AES-256-GCM helpers + per-org envelope encryption.

Architecture
------------
``PIL_MASTER_ENCRYPTION_KEY`` is the KEK (key-encryption key). Each org gets
its own DEK (data-encryption key), wrapped with the KEK and stored in
``organizations.encryption_key_wrapped``. Raw payloads (when an org opts in)
are encrypted with the DEK and stored in ``request_payloads`` alongside the
nonce. The DEK never leaves the process in plaintext.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.settings import get_settings

NONCE_SIZE = 12
DEK_SIZE = 32  # AES-256


def _master_key() -> bytes:
    """Derive a 32-byte key from PIL_MASTER_ENCRYPTION_KEY.

    Accepts both raw bytes (urlsafe-b64) and arbitrary strings; pads/truncates
    via SHA-256 normalization so dev defaults like ``"change-me-..."`` still
    yield a valid 32-byte key (callers in production must override with a
    real urlsafe-b64 32-byte value).
    """
    raw = get_settings().master_encryption_key.get_secret_value().encode("utf-8")
    try:
        decoded = base64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4))
        if len(decoded) == 32:
            return decoded
    except Exception:  # noqa: BLE001 — fall through to hash path
        pass
    # Deterministic 32-byte derivation for non-conforming inputs.
    from hashlib import sha256

    return sha256(raw).digest()


@dataclass(frozen=True)
class Envelope:
    """An AEAD ciphertext + the 96-bit nonce used to produce it."""

    nonce: bytes
    ciphertext: bytes


def new_dek() -> bytes:
    """Generate a fresh 256-bit data-encryption key."""
    return AESGCM.generate_key(bit_length=256)


def wrap_dek(dek: bytes) -> bytes:
    """Encrypt a DEK with the master key. Returns ``nonce || ciphertext``."""
    if len(dek) != DEK_SIZE:
        raise ValueError("DEK must be 32 bytes")
    nonce = os.urandom(NONCE_SIZE)
    aead = AESGCM(_master_key())
    ct = aead.encrypt(nonce, dek, associated_data=b"pil:dek-wrap:v1")
    return nonce + ct


def unwrap_dek(wrapped: bytes) -> bytes:
    """Reverse of :func:`wrap_dek`."""
    if len(wrapped) < NONCE_SIZE + 1:
        raise ValueError("wrapped DEK is malformed")
    nonce, ct = wrapped[:NONCE_SIZE], wrapped[NONCE_SIZE:]
    aead = AESGCM(_master_key())
    return aead.decrypt(nonce, ct, associated_data=b"pil:dek-wrap:v1")


def encrypt_with_dek(dek: bytes, plaintext: bytes, *, aad: bytes = b"") -> Envelope:
    if len(dek) != DEK_SIZE:
        raise ValueError("DEK must be 32 bytes")
    nonce = os.urandom(NONCE_SIZE)
    aead = AESGCM(dek)
    return Envelope(nonce=nonce, ciphertext=aead.encrypt(nonce, plaintext, aad))


def decrypt_with_dek(dek: bytes, envelope: Envelope, *, aad: bytes = b"") -> bytes:
    aead = AESGCM(dek)
    return aead.decrypt(envelope.nonce, envelope.ciphertext, aad)
