"""Adapter scrub/restore wiring without invoking Presidio.

A fake scrubber lets us prove that each adapter:
  * walks the right body fields,
  * preserves message structure,
  * round-trips via restore_response_body for placeholders that came back.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field

from app.core.providers.anthropic import AnthropicAdapter
from app.core.providers.gemini import GeminiAdapter
from app.core.providers.openai import OpenAIAdapter


@dataclass
class _FakeScrubResult:
    scrubbed_text: str
    placeholders_to_originals: dict[str, str]
    detected: list = field(default_factory=list)
    category_counts: Counter = field(default_factory=Counter)


class _ReplaceEmailScrubber:
    """Stand-in for PIIScrubber: replaces the literal "alice@x.com" with a
    placeholder, ignoring everything else. Sufficient to test adapter wiring.
    """

    def scrub(self, text: str, *, mode: str = "reversible") -> _FakeScrubResult:
        placeholder = "<EMAIL_ADDRESS_1>"
        if "alice@x.com" in text:
            return _FakeScrubResult(
                scrubbed_text=text.replace("alice@x.com", placeholder),
                placeholders_to_originals={placeholder: "alice@x.com"},
            )
        return _FakeScrubResult(scrubbed_text=text, placeholders_to_originals={})


def test_openai_scrub_walks_messages() -> None:
    adapter = OpenAIAdapter()
    body = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "be nice"},
            {"role": "user", "content": "email alice@x.com please"},
        ],
    }
    scrubbed, agg = adapter.scrub_request(_ReplaceEmailScrubber(), body, mode="reversible")
    assert scrubbed["messages"][1]["content"] == "email <EMAIL_ADDRESS_1> please"
    assert agg.placeholders_to_originals == {"<EMAIL_ADDRESS_1>": "alice@x.com"}
    # original body untouched (deepcopy)
    assert body["messages"][1]["content"] == "email alice@x.com please"


def test_openai_restore_response_body() -> None:
    adapter = OpenAIAdapter()
    body = {"choices": [{"message": {"content": "reply to <EMAIL_ADDRESS_1>"}}]}
    out = adapter.restore_response_body(body, {"<EMAIL_ADDRESS_1>": "alice@x.com"})
    assert out["choices"][0]["message"]["content"] == "reply to alice@x.com"


def test_anthropic_scrub_walks_system_and_messages() -> None:
    adapter = AnthropicAdapter()
    body = {
        "system": "talk to alice@x.com only",
        "messages": [{"role": "user", "content": "send alice@x.com a note"}],
    }
    scrubbed, agg = adapter.scrub_request(_ReplaceEmailScrubber(), body, mode="reversible")
    assert "<EMAIL_ADDRESS_1>" in scrubbed["system"]
    assert "<EMAIL_ADDRESS_1>" in scrubbed["messages"][0]["content"]
    assert agg.placeholders_to_originals["<EMAIL_ADDRESS_1>"] == "alice@x.com"


def test_anthropic_restore_response_body() -> None:
    adapter = AnthropicAdapter()
    body = {"content": [{"type": "text", "text": "ok <EMAIL_ADDRESS_1>"}]}
    out = adapter.restore_response_body(body, {"<EMAIL_ADDRESS_1>": "alice@x.com"})
    assert out["content"][0]["text"] == "ok alice@x.com"


def test_gemini_scrub_walks_parts_and_system_instruction() -> None:
    adapter = GeminiAdapter()
    body = {
        "systemInstruction": {"parts": [{"text": "be brief, email alice@x.com"}]},
        "contents": [
            {"role": "user", "parts": [{"text": "say hi to alice@x.com"}]},
        ],
    }
    scrubbed, agg = adapter.scrub_request(_ReplaceEmailScrubber(), body, mode="reversible")
    assert "<EMAIL_ADDRESS_1>" in scrubbed["systemInstruction"]["parts"][0]["text"]
    assert "<EMAIL_ADDRESS_1>" in scrubbed["contents"][0]["parts"][0]["text"]


def test_gemini_restore_response_body() -> None:
    adapter = GeminiAdapter()
    body = {"candidates": [{"content": {"parts": [{"text": "hello <EMAIL_ADDRESS_1>"}]}}]}
    out = adapter.restore_response_body(body, {"<EMAIL_ADDRESS_1>": "alice@x.com"})
    assert out["candidates"][0]["content"]["parts"][0]["text"] == "hello alice@x.com"


def test_stream_chunk_restore_is_byte_safe() -> None:
    adapter = OpenAIAdapter()
    chunk = b'data: {"choices":[{"delta":{"content":"hello <EMAIL_ADDRESS_1>"}}]}\n\n'
    out = adapter.restore_stream_chunk(chunk, {"<EMAIL_ADDRESS_1>": "alice@x.com"})
    assert b"alice@x.com" in out
    assert b"<EMAIL_ADDRESS_1>" not in out
