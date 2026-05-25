"""Gemini (Google Generative Language) adapter.

Inbound:  POST /gemini/v1/generateContent
          POST /gemini/v1beta/models/{model}:generateContent
          POST /gemini/v1beta/models/{model}:streamGenerateContent
Upstream: POST https://generativelanguage.googleapis.com{path}

For Gemini, model + ``generateContent`` are in the URL path, not the body.
The proxy passes the raw path through and Gemini routes accordingly.

Streaming uses ``alt=sse`` for SSE or a JSON array for ``streamGenerateContent``;
we forward bytes verbatim and apply placeholder restoration over the bytes.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy

from app.core.pii.scrubber import PIIScrubber, ScrubResult, restore
from app.core.providers.base import ProviderAdapter

UPSTREAM_BASE = "https://generativelanguage.googleapis.com"


class GeminiAdapter(ProviderAdapter):
    name = "gemini"
    upstream_base = UPSTREAM_BASE
    auth_header_in = "x-goog-api-key"

    def upstream_url(self, path: str) -> str:
        return f"{self.upstream_base}{path}"

    def scrub_request(
        self, scrubber: PIIScrubber, body: dict, mode: str
    ) -> tuple[dict, ScrubResult]:
        body = deepcopy(body)
        mapping: dict[str, str] = {}
        detected_all: list = []

        for content in body.get("contents") or []:
            for part in content.get("parts") or []:
                if isinstance(part, dict) and "text" in part and isinstance(part["text"], str):
                    result = scrubber.scrub(part["text"], mode=mode)
                    part["text"] = result.scrubbed_text
                    mapping.update(result.placeholders_to_originals)
                    detected_all.extend(result.detected)

        sys_inst = body.get("systemInstruction")
        if isinstance(sys_inst, dict):
            for part in sys_inst.get("parts") or []:
                if isinstance(part, dict) and "text" in part and isinstance(part["text"], str):
                    result = scrubber.scrub(part["text"], mode=mode)
                    part["text"] = result.scrubbed_text
                    mapping.update(result.placeholders_to_originals)
                    detected_all.extend(result.detected)

        aggregated = ScrubResult(
            scrubbed_text="",
            placeholders_to_originals=mapping,
            detected=detected_all,
        )
        aggregated.category_counts = Counter(d.category for d in detected_all)
        return body, aggregated

    def restore_response_body(self, body: dict, mapping: dict[str, str]) -> dict:
        if not mapping:
            return body
        body = deepcopy(body)
        for candidate in body.get("candidates", []) or []:
            content = candidate.get("content") or {}
            for part in content.get("parts") or []:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    part["text"] = restore(part["text"], mapping)
        return body

    def restore_stream_chunk(self, chunk: bytes, mapping: dict[str, str]) -> bytes:
        if not mapping:
            return chunk
        out = chunk
        for placeholder, original in mapping.items():
            out = out.replace(placeholder.encode("utf-8"), original.encode("utf-8"))
        return out

    def _extract_response_text(self, body: dict) -> str:
        chunks: list[str] = []
        for candidate in body.get("candidates", []) or []:
            content = candidate.get("content") or {}
            for part in content.get("parts") or []:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    chunks.append(part["text"])
        return "\n".join(chunks)
