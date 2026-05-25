"""Regex-level smoke tests for the 8 custom recognizers.

We don't spin up Presidio's full analyzer here — that's an integration test
(needs spaCy) — but we exercise each ``PatternRecognizer``'s ``patterns``
directly via its compiled regex so we catch broken patterns in unit-mode.
"""

from __future__ import annotations

import re

import pytest

from app.core.pii.recognizers import (
    anthropic_key,
    aws_keys,
    db_connection,
    github_token,
    jwt,
    openai_key,
    private_key,
)


def _matches_any(rec, text: str) -> bool:
    for p in rec.patterns:
        if re.search(p.regex, text):
            return True
    return False


@pytest.mark.parametrize(
    "needle",
    [
        "sk-" + "a" * 48,
        "sk-proj-" + "A1B2C3D4" * 5 + "abcdef",
    ],
)
def test_openai_key_recognizes(needle: str) -> None:
    assert _matches_any(openai_key.OPENAI_API_KEY, f"my key is {needle} and...")


def test_openai_key_does_not_match_anthropic() -> None:
    # The classic pattern uses negative lookahead to avoid claiming Anthropic.
    text = "sk-ant-api03-abc"
    assert not _matches_any(openai_key.OPENAI_API_KEY, text)


def test_anthropic_key_recognizes() -> None:
    assert _matches_any(
        anthropic_key.ANTHROPIC_API_KEY,
        "ANTHROPIC_API_KEY=sk-ant-api03-" + "X" * 90,
    )


def test_aws_access_key_recognizes() -> None:
    assert _matches_any(aws_keys.AWS_ACCESS_KEY, "AKIAIOSFODNN7EXAMPLE")


def test_aws_secret_key_pattern_matches_mixed_case_and_digit() -> None:
    sample = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    assert _matches_any(aws_keys.AWS_SECRET_KEY, sample)


@pytest.mark.parametrize(
    "needle",
    [
        "ghp_" + "x" * 36,
        "github_pat_" + "A" * 22 + "_" + "B" * 59,
    ],
)
def test_github_token_recognizes(needle: str) -> None:
    assert _matches_any(github_token.GITHUB_TOKEN, needle)


def test_private_key_block_recognizes_multiline() -> None:
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEAxyz...\nABCD\n"
        "-----END RSA PRIVATE KEY-----"
    )
    assert _matches_any(private_key.PRIVATE_KEY_BLOCK, pem)


def test_db_connection_requires_credentials() -> None:
    # Has creds — should match.
    assert _matches_any(
        db_connection.DATABASE_CONNECTION,
        "DATABASE_URL=postgres://user:hunter2@db.example.com:5432/app",
    )
    # No creds — should NOT match (config metadata, not a secret).
    assert not _matches_any(
        db_connection.DATABASE_CONNECTION, "postgres://db.example.com:5432/app"
    )


def test_jwt_recognizes_three_segments() -> None:
    token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcd1234"
    assert _matches_any(jwt.JWT_TOKEN, f"Authorization: Bearer {token}")
