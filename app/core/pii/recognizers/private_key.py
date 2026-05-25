"""Detect PEM-wrapped private key blocks.

The regex captures the entire BEGIN..END block including the body so the
scrubber can replace the whole thing — leaving just a footer would still
leak the key.
"""

from __future__ import annotations

from presidio_analyzer import EntityRecognizer, Pattern, PatternRecognizer

PRIVATE_KEY_BLOCK = PatternRecognizer(
    supported_entity="PRIVATE_KEY_BLOCK",
    name="private-key-block-recognizer",
    patterns=[
        Pattern(
            name="pem-block",
            regex=(
                r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY-----"
                r"[\s\S]+?"
                r"-----END (?:RSA |EC |DSA |OPENSSH |ENCRYPTED |PGP )?PRIVATE KEY-----"
            ),
            score=0.99,
        ),
    ],
    context=["private", "key", "pem", "rsa", "ssh"],
)

RECOGNIZERS: list[EntityRecognizer] = [PRIVATE_KEY_BLOCK]
