"""Detect AWS access keys and the contextual secret keys that accompany them.

``AKIA``-prefixed access keys are an exact pattern. Secret keys are 40-char
base64 strings, which are far too ambiguous to flag in isolation — so the
secret recognizer only triggers when the surrounding window contains an
access key or an ``aws_secret``/``aws-secret``/``AWS_SECRET_ACCESS_KEY``-like
context cue.
"""

from __future__ import annotations

from presidio_analyzer import EntityRecognizer, Pattern, PatternRecognizer

AWS_ACCESS_KEY = PatternRecognizer(
    supported_entity="AWS_ACCESS_KEY",
    name="aws-access-key-recognizer",
    patterns=[
        Pattern(
            name="aws-akia",
            regex=r"\b(?:AKIA|ASIA|AGPA|AROA|AIDA|ANPA|ANVA|AIPA|APKA)[A-Z0-9]{16}\b",
            score=0.9,
        ),
    ],
    context=["aws", "amazon", "access", "key"],
)

# Bare 40-char base64 isn't recognizable as a secret without context, so this
# pattern lives at a low base score and is bumped by Presidio's context window
# whenever it sees aws_secret-style cues. We tighten the regex slightly: it
# must contain at least one ``+`` / ``/`` or at least one digit AND one upper
# AND one lower to avoid matching pure-word text.
AWS_SECRET_KEY = PatternRecognizer(
    supported_entity="AWS_SECRET_KEY",
    name="aws-secret-key-recognizer",
    patterns=[
        Pattern(
            name="aws-secret-40",
            regex=r"\b(?=[A-Za-z0-9+/]{40}\b)(?=[A-Za-z0-9+/]*[A-Z])(?=[A-Za-z0-9+/]*[a-z])(?=[A-Za-z0-9+/]*\d)[A-Za-z0-9+/]{40}\b",
            score=0.4,
        ),
    ],
    context=[
        "aws_secret",
        "aws-secret",
        "secret_access_key",
        "secret-access-key",
        "aws",
        "secret",
    ],
)

RECOGNIZERS: list[EntityRecognizer] = [AWS_ACCESS_KEY, AWS_SECRET_KEY]
