"""Vector literal formatting + prompt hashing — pure-Python, no DB."""

from __future__ import annotations

import hashlib

from app.core.cache import _vector_literal, hash_prompt


def test_vector_literal_format() -> None:
    out = _vector_literal([0.0, 1.5, -0.25])
    assert out == "[0.0000000,1.5000000,-0.2500000]"


def test_hash_prompt_is_sha256_bytes() -> None:
    out = hash_prompt("hello")
    assert isinstance(out, bytes)
    assert len(out) == 32
    assert out == hashlib.sha256(b"hello").digest()
