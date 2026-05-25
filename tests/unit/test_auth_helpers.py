"""Pure-Python auth helpers — key generation + verify (no DB)."""

from __future__ import annotations

import pytest
from argon2 import PasswordHasher

from app.core.auth import KEY_PREFIX, SUFFIX_LEN, generate_key, hash_key


def test_generate_key_has_pil_prefix_and_suffix_matches() -> None:
    issued = generate_key()
    assert issued.plaintext.startswith(KEY_PREFIX)
    assert len(issued.suffix) == SUFFIX_LEN
    assert issued.plaintext.endswith(issued.suffix)


def test_generated_hash_verifies() -> None:
    issued = generate_key()
    PasswordHasher().verify(issued.hash, issued.plaintext)


def test_hash_key_helper() -> None:
    h1 = hash_key("pil-abc")
    h2 = hash_key("pil-abc")
    # Argon2 salts each hash; same input → different hashes.
    assert h1 != h2
    PasswordHasher().verify(h1, "pil-abc")
    PasswordHasher().verify(h2, "pil-abc")


def test_keys_are_unique() -> None:
    seen = {generate_key().plaintext for _ in range(20)}
    assert len(seen) == 20
