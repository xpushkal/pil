"""A fictional ``WIDGET_ID`` recognizer used to test the plugin loader.

Picked up by ``app/core/pii/scrubber._discover_plugin_recognizers`` when the
test passes this dir as an ``extra_recognizer_dir``.
"""

from __future__ import annotations

from presidio_analyzer import EntityRecognizer, Pattern, PatternRecognizer

WIDGET_ID = PatternRecognizer(
    supported_entity="WIDGET_ID",
    name="widget-id-recognizer",
    patterns=[Pattern(name="widget", regex=r"\bWDG-\d{6}\b", score=0.95)],
    context=["widget", "wdg"],
)

RECOGNIZERS: list[EntityRecognizer] = [WIDGET_ID]
