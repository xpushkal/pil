"""Inbound → upstream header forwarding."""

from __future__ import annotations

from app.core.providers.anthropic import AnthropicAdapter
from app.core.providers.gemini import GeminiAdapter
from app.core.providers.openai import OpenAIAdapter


def test_openai_forwards_authorization_only() -> None:
    inbound = {
        "host": "localhost",
        "user-agent": "curl",
        "authorization": "Bearer sk-fake",
        "x-pil-key": "pil-xyz",
        "openai-organization": "org-1",
    }
    out = OpenAIAdapter().upstream_headers(inbound)
    assert out["authorization"] == "Bearer sk-fake"
    assert out["openai-organization"] == "org-1"
    assert "x-pil-key" not in out
    assert "host" not in out


def test_anthropic_forwards_xapikey_and_version() -> None:
    inbound = {"x-api-key": "sk-ant-...", "anthropic-version": "2023-06-01"}
    out = AnthropicAdapter().upstream_headers(inbound)
    assert out["x-api-key"] == "sk-ant-..."
    assert out["anthropic-version"] == "2023-06-01"


def test_gemini_forwards_xgoogapikey() -> None:
    inbound = {"x-goog-api-key": "AI..."}
    out = GeminiAdapter().upstream_headers(inbound)
    assert out["x-goog-api-key"] == "AI..."
