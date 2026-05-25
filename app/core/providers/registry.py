"""Provider lookup."""

from __future__ import annotations

from app.core.providers.anthropic import AnthropicAdapter
from app.core.providers.base import ProviderAdapter
from app.core.providers.gemini import GeminiAdapter
from app.core.providers.openai import OpenAIAdapter

_ADAPTERS: dict[str, ProviderAdapter] = {
    "openai": OpenAIAdapter(),
    "anthropic": AnthropicAdapter(),
    "gemini": GeminiAdapter(),
}


def get_adapter(name: str) -> ProviderAdapter:
    try:
        return _ADAPTERS[name]
    except KeyError as exc:
        raise ValueError(f"unknown provider: {name!r}") from exc


def known_providers() -> tuple[str, ...]:
    return tuple(_ADAPTERS)
