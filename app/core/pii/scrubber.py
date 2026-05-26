"""PII + dev-secret scrubber.

Built on Microsoft Presidio. Loads:

* A curated set of Presidio's built-in entities (PERSON, EMAIL_ADDRESS,
  PHONE_NUMBER, CREDIT_CARD, IBAN_CODE, IP_ADDRESS, URL, LOCATION, DATE_TIME).
* All custom recognizers exported from ``app/core/pii/recognizers/*.py`` via
  a ``RECOGNIZERS: list[EntityRecognizer]`` module-level binding.

Government-ID entities (US_SSN, US_DRIVER_LICENSE, US_PASSPORT, UK_NHS,
IN_AADHAAR, MEDICAL_LICENSE) are explicitly **not** loaded — see
``app/core/pii/recognizers/README.md``.

Two modes:

* ``reversible`` (default) — placeholders are ``<{ENTITY}_{index}>`` and a
  mapping placeholder→original is staged in Redis under ``pii:map:{request_id}``
  for ≤ ``PIL_PII_MAP_TTL_SECONDS``. The proxy uses this to restore PII in
  the upstream response.
* ``one_way`` — stable placeholders, no mapping written. Caller cannot
  recover originals.

Fail-closed: if any step in the scrubber raises, the caller is expected to
abort the request with ``502 PII_SCRUBBER_UNAVAILABLE`` (the proxy wires this
based on ``PIL_PII_FAIL_CLOSED``; default ``true``).
"""

from __future__ import annotations

import importlib.util
import pkgutil
import re
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from presidio_analyzer import (
    AnalyzerEngine,
    EntityRecognizer,
    RecognizerRegistry,
    RecognizerResult,
)
from presidio_analyzer.nlp_engine import NlpEngineProvider

from app.core.pii import recognizers as recognizers_pkg
from app.observability.logging import get_logger
from app.observability.metrics import pii_detections_total
from app.settings import get_settings

log = get_logger("pii.scrubber")

# Presidio built-ins we want enabled. Anything not in this set is ignored.
ENABLED_PRESIDIO_ENTITIES: tuple[str, ...] = (
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "IBAN_CODE",
    "IP_ADDRESS",
    "URL",
    "LOCATION",
    "DATE_TIME",
)

# Documented as DISABLED in the recognizers README. We explicitly do not allow
# the analyzer to load these — even if a future Presidio update enables them.
DISABLED_PRESIDIO_ENTITIES: tuple[str, ...] = (
    "US_SSN",
    "US_DRIVER_LICENSE",
    "US_PASSPORT",
    "UK_NHS",
    "IN_AADHAAR",
    "MEDICAL_LICENSE",
)

Mode = Literal["reversible", "one_way"]

DEFAULT_SCORE_THRESHOLD = 0.5


# --------------------------------------------------------------------------
# data classes
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class DetectedEntity:
    category: str
    start: int
    end: int
    score: float


@dataclass
class ScrubResult:
    scrubbed_text: str
    placeholders_to_originals: dict[str, str]
    detected: list[DetectedEntity]
    category_counts: Counter[str] = field(default_factory=Counter)

    @property
    def total_entities(self) -> int:
        return len(self.detected)

    @property
    def categories(self) -> list[str]:
        return sorted(self.category_counts)


# --------------------------------------------------------------------------
# plugin loader
# --------------------------------------------------------------------------
def _discover_plugin_recognizers(
    package_dir: Path | None = None,
) -> list[EntityRecognizer]:
    """Walk ``app/core/pii/recognizers`` (or a supplied dir) and collect any
    module that exports ``RECOGNIZERS``.
    """
    if package_dir is None:
        # Default: the in-package directory.
        package_dir = Path(recognizers_pkg.__file__).parent

    discovered: list[EntityRecognizer] = []
    for module_info in pkgutil.iter_modules([str(package_dir)]):
        if module_info.ispkg or module_info.name.startswith("_"):
            continue
        modname = f"{recognizers_pkg.__name__}.{module_info.name}"
        if package_dir != Path(recognizers_pkg.__file__).parent:
            # Loading from an out-of-tree fixture path — synthesize a module name.
            modname = f"_pil_plugin_{module_info.name}"
            spec = importlib.util.spec_from_file_location(
                modname, package_dir / f"{module_info.name}.py"
            )
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[modname] = module
            spec.loader.exec_module(module)
        else:
            module = importlib.import_module(modname)
        recs = getattr(module, "RECOGNIZERS", None)
        if not recs:
            continue
        for rec in recs:
            if not isinstance(rec, EntityRecognizer):
                log.warning("plugin.skip_non_recognizer", module=modname, type=type(rec).__name__)
                continue
            discovered.append(rec)
    return discovered


# --------------------------------------------------------------------------
# scrubber
# --------------------------------------------------------------------------
class PIIScrubber:
    """Wraps a Presidio AnalyzerEngine with PIL policy.

    Thread/coroutine-safe for read-only use — analysis is stateless once the
    engine is built.
    """

    def __init__(
        self,
        *,
        extra_recognizer_dirs: Iterable[Path] = (),
        score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    ) -> None:
        self._score_threshold = score_threshold

        registry = RecognizerRegistry()
        # Load Presidio's built-ins, then immediately remove anything we don't
        # want enabled. (Presidio doesn't expose a "load only these" API.)
        registry.load_predefined_recognizers()
        for rec in list(registry.recognizers):
            if any(ent in DISABLED_PRESIDIO_ENTITIES for ent in rec.supported_entities):
                registry.remove_recognizer(rec.name)

        # Register custom recognizers from the plugin dirs.
        for rec in _discover_plugin_recognizers():
            registry.add_recognizer(rec)
        for extra_dir in extra_recognizer_dirs:
            for rec in _discover_plugin_recognizers(extra_dir):
                registry.add_recognizer(rec)

        # Build an NLP engine using whatever spaCy model is configured (lg in
        # prod, sm in CI/tests). Presidio constructs its own default engine if
        # we don't pass one, but that default unconditionally loads
        # ``en_core_web_lg`` which is heavy.
        nlp_engine = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": get_settings().spacy_model}],
            }
        ).create_engine()
        self._analyzer = AnalyzerEngine(
            registry=registry, nlp_engine=nlp_engine, supported_languages=["en"]
        )
        self._allowed_entities: tuple[str, ...] = (
            *ENABLED_PRESIDIO_ENTITIES,
            *self._custom_entity_names(registry),
        )

    @staticmethod
    def _custom_entity_names(registry: RecognizerRegistry) -> tuple[str, ...]:
        names: set[str] = set()
        for rec in registry.recognizers:
            for ent in rec.supported_entities:
                if ent not in ENABLED_PRESIDIO_ENTITIES and ent not in DISABLED_PRESIDIO_ENTITIES:
                    names.add(ent)
        return tuple(sorted(names))

    @property
    def allowed_entities(self) -> tuple[str, ...]:
        return self._allowed_entities

    def scrub(self, text: str, *, mode: Mode = "reversible") -> ScrubResult:
        """Detect entities and return a (scrubbed_text, mapping, detected) bundle.

        Mode behavior:

        * ``reversible`` — placeholders are ``<{ENTITY}_{index}>`` where index
          counts occurrences of that entity within the prompt. The mapping is
          returned alongside; the caller stages it in Redis.
        * ``one_way`` — placeholders are stable per (entity, exact value) within
          the prompt so identical strings collapse to the same placeholder, but
          the mapping isn't surfaced.
        """
        if not text:
            return ScrubResult(scrubbed_text="", placeholders_to_originals={}, detected=[])

        try:
            results: list[RecognizerResult] = self._analyzer.analyze(
                text=text,
                language="en",
                entities=list(self._allowed_entities),
                score_threshold=self._score_threshold,
            )
        except Exception as exc:  # noqa: BLE001 — surface to caller fail-closed
            log.error("pii.analyze_failed", error_type=type(exc).__name__)
            raise

        # Resolve overlaps: keep the higher-score, longer span.
        results = _resolve_overlaps(results)
        results.sort(key=lambda r: r.start)

        scrubbed_parts: list[str] = []
        mapping: dict[str, str] = {}
        # one_way: value-stable placeholders; reversible: index-stable per entity.
        per_entity_index: Counter[str] = Counter()
        one_way_lookup: dict[tuple[str, str], str] = {}
        detected: list[DetectedEntity] = []
        cursor = 0

        for r in results:
            if r.start < cursor:
                # Overlap survived resolution somehow; skip.
                continue
            original = text[r.start : r.end]
            entity = r.entity_type

            if mode == "one_way":
                key = (entity, original)
                if key in one_way_lookup:
                    placeholder = one_way_lookup[key]
                else:
                    per_entity_index[entity] += 1
                    placeholder = f"<{entity}_{per_entity_index[entity]}>"
                    one_way_lookup[key] = placeholder
            else:  # reversible
                per_entity_index[entity] += 1
                placeholder = f"<{entity}_{per_entity_index[entity]}>"
                mapping[placeholder] = original

            scrubbed_parts.append(text[cursor : r.start])
            scrubbed_parts.append(placeholder)
            cursor = r.end

            detected.append(
                DetectedEntity(category=entity, start=r.start, end=r.end, score=r.score)
            )
            pii_detections_total.labels(category=entity).inc()

        scrubbed_parts.append(text[cursor:])

        counts: Counter[str] = Counter(d.category for d in detected)
        return ScrubResult(
            scrubbed_text="".join(scrubbed_parts),
            placeholders_to_originals=mapping,
            detected=detected,
            category_counts=counts,
        )


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _resolve_overlaps(results: list[RecognizerResult]) -> list[RecognizerResult]:
    """Sort by start, then drop any later span that overlaps a chosen one.
    Tie-break by (higher score, longer span)."""
    chosen: list[RecognizerResult] = []
    for r in sorted(results, key=lambda x: (-x.score, -(x.end - x.start), x.start)):
        if any(not (r.end <= c.start or r.start >= c.end) for c in chosen):
            continue
        chosen.append(r)
    return chosen


_PLACEHOLDER_RE = re.compile(r"<([A-Z][A-Z0-9_]+)_(\d+)>")


def restore(text: str, mapping: dict[str, str]) -> str:
    """Reverse a previous reversible scrub on the upstream response.

    Replaces every ``<ENTITY_n>`` placeholder with its mapped original. Any
    placeholder not in the mapping is left intact — the model occasionally
    hallucinates new ones, and we don't want to expose unrelated originals.
    """
    if not mapping:
        return text

    def _sub(match: re.Match[str]) -> str:
        placeholder = match.group(0)
        return mapping.get(placeholder, placeholder)

    return _PLACEHOLDER_RE.sub(_sub, text)
