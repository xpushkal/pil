"""AES-GCM helpers + envelope encryption round-trip."""

from __future__ import annotations

import pytest

from app.utils.crypto import (
    decrypt_with_dek,
    encrypt_with_dek,
    new_dek,
    unwrap_dek,
    wrap_dek,
)


def test_dek_round_trip() -> None:
    dek = new_dek()
    wrapped = wrap_dek(dek)
    assert wrapped != dek
    assert unwrap_dek(wrapped) == dek


def test_envelope_round_trip_with_aad() -> None:
    dek = new_dek()
    payload = b"sensitive prompt body"
    env = encrypt_with_dek(dek, payload, aad=b"pil:prompt:v1")
    assert env.ciphertext != payload
    assert decrypt_with_dek(dek, env, aad=b"pil:prompt:v1") == payload


def test_envelope_aad_mismatch_rejects() -> None:
    from cryptography.exceptions import InvalidTag

    dek = new_dek()
    env = encrypt_with_dek(dek, b"x", aad=b"pil:prompt:v1")
    with pytest.raises(InvalidTag):
        decrypt_with_dek(dek, env, aad=b"pil:response:v1")


def test_two_encryptions_use_independent_nonces() -> None:
    dek = new_dek()
    a = encrypt_with_dek(dek, b"x")
    b = encrypt_with_dek(dek, b"x")
    assert a.nonce != b.nonce, "nonces must be random per encryption"
    assert a.ciphertext != b.ciphertext
