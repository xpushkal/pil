"""Detect Anthropic API keys (``sk-ant-...``)."""

from __future__ import annotations

from presidio_analyzer import EntityRecognizer, Pattern, PatternRecognizer

ANTHROPIC_API_KEY = PatternRecognizer(
    supported_entity="ANTHROPIC_API_KEY",
    name="anthropic-key-recognizer",
    patterns=[
        Pattern(
            name="anthropic",
            regex=r"\bsk-ant-(?:api\d{2}-)?[A-Za-z0-9_\-]{32,}\b",
            score=0.95,
        ),
    ],
    context=["anthropic", "claude", "api", "key"],
)

RECOGNIZERS: list[EntityRecognizer] = [ANTHROPIC_API_KEY]
