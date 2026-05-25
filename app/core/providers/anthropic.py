"""Anthropic Messages adapter.

Inbound:  POST /anthropic/v1/messages
Upstream: POST https://api.anthropic.com/v1/messages
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy

from app.core.pii.scrubber import PIIScrubber, ScrubResult, restore
from app.core.providers.base import ProviderAdapter

UPSTREAM_BASE = "https://api.anthropic.com"


class AnthropicAdapter(ProviderAdapter):
    name = "anthropic"
    upstream_base = UPSTREAM_BASE
    auth_header_in = "x-api-key"

    def upstream_url(self, path: str) -> str:
        return f"{self.upstream_base}{path}"

    def scrub_request(
        self, scrubber: PIIScrubber, body: dict, mode: str
    ) -> tuple[dict, ScrubResult]:
        body = deepcopy(body)
        mapping: dict[str, str] = {}
        detected_all: list = []

        # System prompt — can be a string or a list of content blocks.
        system = body.get("system")
        if isinstance(system, str):
            result = scrubber.scrub(system, mode=mode)
            body["system"] = result.scrubbed_text
            mapping.update(result.placeholders_to_originals)
            detected_all.extend(result.detected)
        elif isinstance(system, list):
            for part in system:
                if isinstance(part, dict) and part.get("type") == "text":
                    result = scrubber.scrub(part.get("text", ""), mode=mode)
                    part["text"] = result.scrubbed_text
                    mapping.update(result.placeholders_to_originals)
                    detected_all.extend(result.detected)

        for message in body.get("messages") or []:
            content = message.get("content")
            if isinstance(content, str):
                result = scrubber.scrub(content, mode=mode)
                message["content"] = result.scrubbed_text
                mapping.update(result.placeholders_to_originals)
                detected_all.extend(result.detected)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        result = scrubber.scrub(part.get("text", ""), mode=mode)
                        part["text"] = result.scrubbed_text
                        mapping.update(result.placeholders_to_originals)
                        detected_all.extend(result.detected)
                    elif isinstance(part, dict) and part.get("type") == "tool_result":
                        sub = part.get("content")
                        if isinstance(sub, str):
                            result = scrubber.scrub(sub, mode=mode)
                            part["content"] = result.scrubbed_text
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
        for block in body.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "text":
                block["text"] = restore(block.get("text", ""), mapping)
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
        for block in body.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "text":
                chunks.append(block.get("text", ""))
        return "\n".join(chunks)
