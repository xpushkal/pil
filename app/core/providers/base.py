"""Provider adapter base class.

Each adapter knows three things:

* How to **scrub the request body** in-place: walk to the right field(s),
  apply the PIL :class:`PIIScrubber`, return the cleaned body + the
  placeholder→original mapping.
* How to **restore PII in the response body** (for non-streaming responses)
  and **in streaming chunks** (for SSE / JSON-stream).
* How to **forward** to the upstream HTTPS endpoint with the inbound
  ``Authorization`` / ``x-api-key`` / ``x-goog-api-key`` header untouched.

PIL never stores upstream provider keys. They live in inbound headers and
are forwarded verbatim.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import orjson

from app.core.pii.scrubber import PIIScrubber, ScrubResult
from app.core.tokenization import count_tokens, extract_prompt_text


@dataclass(frozen=True)
class UpstreamForwardResult:
    """Non-streaming forward outcome."""

    status_code: int
    headers: dict[str, str]
    body: dict[str, Any]
    raw: bytes


class ProviderAdapter(abc.ABC):
    """Stateless adapter — pass through ``httpx.AsyncClient`` for forwarding."""

    name: str
    upstream_base: str
    auth_header_in: str  # e.g. "authorization" or "x-api-key"

    # ------------------------------------------------------------------
    # Body manipulation
    # ------------------------------------------------------------------
    @abc.abstractmethod
    def scrub_request(self, scrubber: PIIScrubber, body: dict, mode: str) -> tuple[dict, ScrubResult]:
        """Apply the scrubber to the prompt-bearing fields of the request body.

        Returns the new body (a copy) and the underlying :class:`ScrubResult`.
        The caller stages the mapping in Redis (reversible mode only).
        """

    @abc.abstractmethod
    def restore_response_body(self, body: dict, mapping: dict[str, str]) -> dict:
        """Walk the response body and restore placeholders → originals.

        Only called in reversible mode.
        """

    @abc.abstractmethod
    def restore_stream_chunk(self, chunk: bytes, mapping: dict[str, str]) -> bytes:
        """Restore placeholders inside a streaming chunk (SSE / JSON-stream)."""

    @abc.abstractmethod
    def upstream_url(self, path: str) -> str:
        """Resolve an inbound path (after stripping the ``/<provider>`` prefix)
        to the upstream URL."""

    # ------------------------------------------------------------------
    # Tokenization
    # ------------------------------------------------------------------
    def count_request_tokens(self, model: str, body: dict) -> int:
        return count_tokens(self.name, model, extract_prompt_text(self.name, body))

    def count_response_tokens(self, model: str, body: dict) -> int:
        text = self._extract_response_text(body)
        return count_tokens(self.name, model, text)

    @abc.abstractmethod
    def _extract_response_text(self, body: dict) -> str: ...

    # ------------------------------------------------------------------
    # Header forwarding
    # ------------------------------------------------------------------
    def upstream_headers(self, inbound: dict[str, str]) -> dict[str, str]:
        """Forward only the headers that matter. Strip ``host``, ``x-pil-*``,
        and anything httpx will set itself."""
        out: dict[str, str] = {}
        # Lower-case keys for safety; httpx normalizes on send.
        lower = {k.lower(): v for k, v in inbound.items()}
        for key in ("authorization", "x-api-key", "x-goog-api-key", "anthropic-version",
                    "anthropic-beta", "openai-organization", "openai-project"):
            if key in lower:
                out[key] = lower[key]
        out["content-type"] = "application/json"
        return out

    # ------------------------------------------------------------------
    # Forwarding
    # ------------------------------------------------------------------
    async def forward(
        self,
        client: httpx.AsyncClient,
        *,
        url: str,
        headers: dict[str, str],
        body: dict,
        timeout: float = 120.0,
    ) -> UpstreamForwardResult:
        resp = await client.post(url, headers=headers, content=orjson.dumps(body), timeout=timeout)
        raw = resp.content
        try:
            decoded = orjson.loads(raw) if raw else {}
        except orjson.JSONDecodeError:
            decoded = {}
        return UpstreamForwardResult(
            status_code=resp.status_code,
            headers={k: v for k, v in resp.headers.items()},
            body=decoded,
            raw=raw,
        )

    async def forward_stream(
        self,
        client: httpx.AsyncClient,
        *,
        url: str,
        headers: dict[str, str],
        body: dict,
        timeout: float = 120.0,
    ) -> AsyncIterator[bytes]:
        """Yield raw streaming bytes from the upstream. Caller is responsible
        for applying :meth:`restore_stream_chunk`."""
        async with client.stream(
            "POST", url, headers=headers, content=orjson.dumps(body), timeout=timeout
        ) as resp:
            self._last_status = resp.status_code
            self._last_headers = dict(resp.headers)
            async for chunk in resp.aiter_raw():
                yield chunk

    last_stream_status: int = 200
