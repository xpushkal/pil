"""Detect OpenAI API keys.

Format: ``sk-`` followed by 48 alphanumeric chars. Newer project keys use
``sk-proj-`` followed by 64+ alphanumerics; both variants matter.
"""

from __future__ import annotations

from presidio_analyzer import EntityRecognizer, Pattern, PatternRecognizer

OPENAI_API_KEY = PatternRecognizer(
    supported_entity="OPENAI_API_KEY",
    name="openai-key-recognizer",
    patterns=[
        Pattern(
            name="openai-classic",
            regex=r"\bsk-(?!ant-)[A-Za-z0-9]{48}\b",
            score=0.95,
        ),
        Pattern(
            name="openai-project",
            regex=r"\bsk-proj-[A-Za-z0-9_-]{40,}\b",
            score=0.95,
        ),
    ],
    context=["openai", "api", "key", "token"],
)

RECOGNIZERS: list[EntityRecognizer] = [OPENAI_API_KEY]
