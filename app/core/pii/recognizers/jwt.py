"""Detect JWTs (three base64url segments separated by dots).

The header segment must be at least 4 chars and decode to JSON-looking text;
we use a length + character heuristic in the regex and bump the score via
context cues like ``bearer`` or ``authorization``.
"""

from __future__ import annotations

from presidio_analyzer import EntityRecognizer, Pattern, PatternRecognizer

JWT_TOKEN = PatternRecognizer(
    supported_entity="JWT_TOKEN",
    name="jwt-recognizer",
    patterns=[
        Pattern(
            name="jwt-three-segments",
            regex=r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b",
            score=0.85,
        ),
    ],
    context=["bearer", "authorization", "jwt", "token", "id_token", "access_token"],
)

RECOGNIZERS: list[EntityRecognizer] = [JWT_TOKEN]
