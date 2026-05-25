"""OpenAI Chat Completions adapter.

Inbound:  POST /openai/v1/chat/completions
Upstream: POST https://api.openai.com/v1/chat/completions
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.core.pii.scrubber import PIIScrubber, ScrubResult, restore
from app.core.providers.base import ProviderAdapter

UPSTREAM_BASE = "https://api.openai.com"


class OpenAIAdapter(ProviderAdapter):
    name = "openai"
    upstream_base = UPSTREAM_BASE
    auth_header_in = "authorization"

    def upstream_url(self, path: str) -> str:
        return f"{self.upstream_base}{path}"

    def scrub_request(
        self, scrubber: PIIScrubber, body: dict, mode: str
    ) -> tuple[dict, ScrubResult]:
        body = deepcopy(body)
        messages = body.get("messages") or []
        mapping: dict[str, str] = {}
        detected_all: list = []

        for i, message in enumerate(messages):
            content = message.get("content")
            if isinstance(content, str):
                result = scrubber.scrub(content, mode=mode)
                message["content"] = result.scrubbed_text
                mapping.update(result.placeholders_to_originals)
                detected_all.extend(result.detected)
            elif isinstance(content, list):
                for j, part in enumerate(content):
                    if isinstance(part, dict) and part.get("type") == "text":
                        result = scrubber.scrub(part.get("text", ""), mode=mode)
                        part["text"] = result.scrubbed_text
                        mapping.update(result.placeholders_to_originals)
                        detected_all.extend(result.detected)
            messages[i] = message

        body["messages"] = messages
        # Aggregate into a synthetic ScrubResult for the proxy.
        aggregated = ScrubResult(
            scrubbed_text="",  # not used downstream — proxy uses count + categories only
            placeholders_to_originals=mapping,
            detected=detected_all,
        )
        from collections import Counter

        aggregated.category_counts = Counter(d.category for d in detected_all)
        return body, aggregated

    def restore_response_body(self, body: dict, mapping: dict[str, str]) -> dict:
        if not mapping:
            return body
        body = deepcopy(body)
        for choice in body.get("choices", []) or []:
            msg = choice.get("message") or {}
            content = msg.get("content")
            if isinstance(content, str):
                msg["content"] = restore(content, mapping)
            choice["message"] = msg
            # delta is only used in streaming, but include defensive handling.
            delta = choice.get("delta") or {}
            if isinstance(delta.get("content"), str):
                delta["content"] = restore(delta["content"], mapping)
                choice["delta"] = delta
        return body

    def restore_stream_chunk(self, chunk: bytes, mapping: dict[str, str]) -> bytes:
        if not mapping:
            return chunk
        # SSE chunks are text; placeholder format is ASCII-safe so simple
        # bytewise replace is sufficient and avoids decode roundtrips.
        out = chunk
        for placeholder, original in mapping.items():
            out = out.replace(placeholder.encode("utf-8"), original.encode("utf-8"))
        return out

    def _extract_response_text(self, body: dict) -> str:
        chunks: list[str] = []
        for choice in body.get("choices", []) or []:
            msg = choice.get("message") or {}
            content = msg.get("content")
            if isinstance(content, str):
                chunks.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        chunks.append(part.get("text", ""))
        return "\n".join(chunks)
