"""The recognizer plugin loader picks up any fixture dir we point it at.

Verifies the documented promise from ``app/core/pii/recognizers/README.md``:
drop a ``.py`` file with a ``RECOGNIZERS: list[EntityRecognizer]`` binding
into the dir and PIL auto-registers it at startup.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "dummy_recognizers"


def test_plugin_loader_registers_and_detects_widget_id() -> None:
    from app.core.pii.scrubber import PIIScrubber

    scrubber = PIIScrubber(extra_recognizer_dirs=[FIXTURE_DIR])
    assert "WIDGET_ID" in scrubber.allowed_entities

    text = "the broken part is WDG-123456 — please fix"
    result = scrubber.scrub(text, mode="reversible")
    assert any(e.category == "WIDGET_ID" for e in result.detected), (
        f"WIDGET_ID not detected; got {[e.category for e in result.detected]!r}"
    )
    assert "WDG-123456" not in result.scrubbed_text
    assert "<WIDGET_ID_1>" in result.scrubbed_text
    # Mapping recovers the original for reversible mode
    assert result.placeholders_to_originals["<WIDGET_ID_1>"] == "WDG-123456"
