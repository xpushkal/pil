"""Placeholder restoration is pure-Python and doesn't need Presidio."""

from __future__ import annotations

from app.core.pii.scrubber import restore


def test_restore_replaces_known_placeholders() -> None:
    mapping = {"<EMAIL_ADDRESS_1>": "alice@example.com", "<GITHUB_TOKEN_1>": "ghp_x" * 7}
    out = restore("Hi <EMAIL_ADDRESS_1>, here's <GITHUB_TOKEN_1>", mapping)
    assert out == f"Hi alice@example.com, here's {mapping['<GITHUB_TOKEN_1>']}"


def test_restore_leaves_unknown_placeholders_intact() -> None:
    # Model may hallucinate placeholders that aren't in the mapping — those
    # must be left alone to avoid exposing unrelated originals.
    mapping = {"<EMAIL_ADDRESS_1>": "alice@example.com"}
    out = restore("Saw <EMAIL_ADDRESS_99> and <PERSON_3>", mapping)
    assert out == "Saw <EMAIL_ADDRESS_99> and <PERSON_3>"


def test_restore_no_mapping_is_identity() -> None:
    assert restore("plain text", {}) == "plain text"


def test_restore_handles_multiple_occurrences() -> None:
    mapping = {"<EMAIL_ADDRESS_1>": "a@x.com"}
    out = restore("To <EMAIL_ADDRESS_1>; cc <EMAIL_ADDRESS_1>", mapping)
    assert out == "To a@x.com; cc a@x.com"
