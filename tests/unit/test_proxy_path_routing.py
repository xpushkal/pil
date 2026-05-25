"""Provider path classification (pure function, no FastAPI client needed)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.proxy import _classify_provider_from_path


def test_openai_path() -> None:
    provider, upstream = _classify_provider_from_path("/openai/v1/chat/completions")
    assert provider == "openai"
    assert upstream == "/v1/chat/completions"


def test_anthropic_path() -> None:
    provider, upstream = _classify_provider_from_path("/anthropic/v1/messages")
    assert provider == "anthropic"
    assert upstream == "/v1/messages"


def test_gemini_path() -> None:
    provider, upstream = _classify_provider_from_path(
        "/gemini/v1beta/models/gemini-1.5-pro:generateContent"
    )
    assert provider == "gemini"
    assert upstream == "/v1beta/models/gemini-1.5-pro:generateContent"


def test_unknown_provider_path_raises() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _classify_provider_from_path("/cohere/v1/chat")
    assert exc_info.value.status_code == 404
