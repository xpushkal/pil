"""Token counting per provider.

* OpenAI models → tiktoken (encoding picked by ``encoding_for_model``).
* Anthropic models → ``anthropic.Anthropic().count_tokens`` via the SDK's
  bundled tokenizer (no network call).
* Anything else (Gemini, Ollama, unknown) → approximation ``ceil(len(text) / 4)``.
"""

from __future__ import annotations

import math
from functools import lru_cache

import tiktoken


@lru_cache(maxsize=32)
def _tiktoken_for(model: str) -> tiktoken.Encoding:
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(provider: str, model: str, text: str) -> int:
    if not text:
        return 0
    if provider == "openai":
        return len(_tiktoken_for(model).encode(text))
    if provider == "anthropic":
        # The anthropic SDK uses a local BPE tokenizer for Claude 2.x; for
        # 3.x+ models its counter is also local. We count safely without
        # network IO.
        try:
            from anthropic import Anthropic

            counter = Anthropic().count_tokens  # type: ignore[attr-defined]
            return counter(text)  # type: ignore[misc]
        except Exception:
            # SDK API may move; fall back to the approximation.
            return _approx_tokens(text)
    # TODO(sprint-2): wire a real Gemini counter when google-genai exposes one.
    return _approx_tokens(text)


def _approx_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def extract_prompt_text(provider: str, body: dict) -> str:
    """Collapse a provider-specific request body into a single text string
    suitable for embedding + cache hashing. Tool/function-call payloads are
    serialised so they participate in the cache key.
    """
    import orjson

    if provider == "openai":
        msgs = body.get("messages") or []
        chunks: list[str] = []
        for m in msgs:
            role = m.get("role", "")
            content = m.get("content", "")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        chunks.append(f"{role}: {part.get('text', '')}")
                    else:
                        chunks.append(f"{role}: {orjson.dumps(part).decode()}")
            else:
                chunks.append(f"{role}: {content}")
        tools = body.get("tools")
        if tools:
            chunks.append("tools:" + orjson.dumps(tools).decode())
        return "\n".join(chunks)

    if provider == "anthropic":
        chunks: list[str] = []
        system = body.get("system")
        if system:
            chunks.append(f"system: {system if isinstance(system, str) else orjson.dumps(system).decode()}")
        for m in body.get("messages") or []:
            role = m.get("role", "")
            content = m.get("content", "")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        chunks.append(f"{role}: {part.get('text', '')}")
                    else:
                        chunks.append(f"{role}: {orjson.dumps(part).decode()}")
            else:
                chunks.append(f"{role}: {content}")
        tools = body.get("tools")
        if tools:
            chunks.append("tools:" + orjson.dumps(tools).decode())
        return "\n".join(chunks)

    if provider == "gemini":
        chunks: list[str] = []
        for c in body.get("contents") or []:
            role = c.get("role", "")
            for part in c.get("parts") or []:
                if isinstance(part, dict) and "text" in part:
                    chunks.append(f"{role}: {part['text']}")
                else:
                    chunks.append(f"{role}: {orjson.dumps(part).decode()}")
        sys_inst = body.get("systemInstruction")
        if sys_inst:
            chunks.append("system: " + orjson.dumps(sys_inst).decode())
        return "\n".join(chunks)

    return orjson.dumps(body).decode()
