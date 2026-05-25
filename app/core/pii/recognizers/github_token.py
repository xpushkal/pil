"""Detect GitHub personal/OAuth/fine-grained tokens.

Prefixes: ``ghp_`` (classic PAT), ``gho_`` (OAuth user-to-server),
``ghu_`` (user-to-server), ``ghs_`` (server-to-server),
``ghr_`` (refresh), ``github_pat_`` (fine-grained PAT v2).
"""

from __future__ import annotations

from presidio_analyzer import EntityRecognizer, Pattern, PatternRecognizer

GITHUB_TOKEN = PatternRecognizer(
    supported_entity="GITHUB_TOKEN",
    name="github-token-recognizer",
    patterns=[
        Pattern(name="gh-classic", regex=r"\bgh[pousr]_[A-Za-z0-9]{36,251}\b", score=0.95),
        Pattern(
            name="gh-fine-grained",
            regex=r"\bgithub_pat_[A-Za-z0-9]{22}_[A-Za-z0-9]{59}\b",
            score=0.95,
        ),
    ],
    context=["github", "git", "token", "pat"],
)

RECOGNIZERS: list[EntityRecognizer] = [GITHUB_TOKEN]
