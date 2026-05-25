"""Tokenization + prompt-text extraction."""

from __future__ import annotations

from app.core.tokenization import count_tokens, extract_prompt_text


def test_openai_uses_tiktoken() -> None:
    n = count_tokens("openai", "gpt-4o", "hello world")
    assert n >= 1


def test_unknown_provider_uses_approximation() -> None:
    n = count_tokens("ollama", "anything", "x" * 40)
    # ceil(40/4) == 10
    assert n == 10


def test_extract_openai_prompt_text() -> None:
    body = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "fix my bug"},
        ],
    }
    out = extract_prompt_text("openai", body)
    assert "system: be terse" in out
    assert "user: fix my bug" in out


def test_extract_anthropic_with_system_list() -> None:
    body = {
        "system": [{"type": "text", "text": "you are a helper"}],
        "messages": [{"role": "user", "content": "go"}],
    }
    out = extract_prompt_text("anthropic", body)
    assert "you are a helper" in out
    assert "user: go" in out


def test_extract_gemini_parts() -> None:
    body = {
        "contents": [
            {"role": "user", "parts": [{"text": "hi"}, {"text": "there"}]},
        ]
    }
    out = extract_prompt_text("gemini", body)
    assert "hi" in out and "there" in out
