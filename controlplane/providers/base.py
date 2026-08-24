"""
Provider-agnostic streaming interface.

ControlPlane.ai sits in front of *either* an OpenAI-shaped
`/v1/chat/completions` API or an Anthropic-shaped `/v1/messages` API (per
the user's choice of "provider-agnostic adapter" for this build). The rest
of the system (guardrails, agents, action engine) should never have to
know or care which one is in play, so we split the concern in two:

  * `WireFormat` — knows how to talk *this specific provider's* streaming
    protocol: how to read a prompt out of an inbound request body, how to
    build the outbound request to the real API, how to parse that API's
    SSE frames into plain text deltas, and how to re-serialize plain text
    deltas back into that provider's exact SSE frame shape (so a real
    OpenAI/Anthropic SDK on the client side is none the wiser that a proxy
    sits in the middle).

  * `ContentSource` — knows how to *produce* the stream of text deltas.
    `UpstreamContentSource` calls the real provider. `MockContentSource`
    (mock_provider.py) fabricates a deterministic scripted stream for demo
    scenarios, with zero network calls and zero API keys required — this
    is what makes a reliable live demo possible regardless of whether the
    judges' network allows outbound calls to OpenAI/Anthropic.

Everything downstream of a `StreamChunk` (guardrails, agents, dashboard)
is 100% provider-agnostic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

import httpx


@dataclass
class StreamChunk:
    text: str
    finished: bool = False
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None


class WireFormat(ABC):
    """One per upstream API shape (OpenAI chat/completions, Anthropic
    messages). Pure protocol logic — no network calls live here."""

    name: str
    upstream_url: str

    @abstractmethod
    def extract_prompt(self, body: dict) -> str:
        """Flatten the inbound request's messages into a single string,
        used as grounding/hallucination context and for logging."""

    @abstractmethod
    def extract_model(self, body: dict) -> str:
        ...

    @abstractmethod
    def wants_stream(self, body: dict) -> bool:
        ...

    @abstractmethod
    def build_upstream_payload(self, body: dict) -> dict:
        """The exact JSON body to send to the real provider (forces
        stream=True upstream regardless of what the client asked, since the
        sidecar always needs to see the stream incrementally; if the client
        didn't want streaming we still buffer and return one JSON blob to
        them at the end — see proxy/streaming.py)."""

    @abstractmethod
    def auth_headers(self, api_key: str) -> dict:
        ...

    @abstractmethod
    def parse_upstream_line(self, line: str, state: dict) -> Optional[StreamChunk]:
        """Parse one line of the upstream SSE body. `state` is a small
        mutable dict the caller keeps across lines (for accumulating
        provider-specific bookkeeping like usage stats)."""

    @abstractmethod
    def format_stream_start(self, meta: dict) -> str:
        """Any preamble frame(s) needed before the first text delta
        (Anthropic emits message_start/content_block_start events; OpenAI
        needs nothing here — the first delta chunk *is* the start)."""

    @abstractmethod
    def format_client_chunk(self, chunk: StreamChunk, meta: dict) -> str:
        """Serialize a (possibly rewritten/masked) StreamChunk back into
        this provider's exact SSE wire format for the client."""

    @abstractmethod
    def format_stream_end(self, meta: dict) -> str:
        ...

    @abstractmethod
    def format_error_frame(self, message: str, meta: dict) -> str:
        """What to emit to the client when the Dynamic Action Engine
        decides BLOCK_AND_ESCALATE mid-stream (spec 3.3 HIGH RISK row)."""

    @abstractmethod
    def non_streaming_response(self, full_text: str, meta: dict) -> dict:
        """Build a single non-streaming JSON response, for clients that
        set stream=false."""


class ContentSource(ABC):
    @abstractmethod
    def generate(self, prompt: str, model: str, body: dict) -> AsyncIterator[StreamChunk]:
        ...


class UpstreamError(RuntimeError):
    pass


class UpstreamContentSource(ContentSource):
    """Calls the real provider over HTTP and streams back parsed
    StreamChunks. This is the class that actually implements "the proxy
    intercepts outbound requests to the LLM provider" (spec 3.2.1) for a
    genuine upstream call — as opposed to MockContentSource, which never
    leaves the process."""

    def __init__(self, wire: WireFormat, api_key: str):
        self.wire = wire
        self.api_key = api_key

    async def generate(self, prompt: str, model: str, body: dict) -> AsyncIterator[StreamChunk]:
        if not self.api_key:
            raise UpstreamError(
                f"No API key configured for {self.wire.name}. Set the relevant "
                f"env var, or call with model='mock-*' / header "
                f"X-ControlPlane-Mock: true to use the offline demo provider."
            )
        payload = self.wire.build_upstream_payload(body)
        headers = self.wire.auth_headers(self.api_key)
        state: dict = {}

        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("POST", self.wire.upstream_url, json=payload, headers=headers) as resp:
                if resp.status_code >= 400:
                    raw = await resp.aread()
                    raise UpstreamError(f"{self.wire.name} upstream error {resp.status_code}: {raw[:500]!r}")
                async for line in resp.aiter_lines():
                    chunk = self.wire.parse_upstream_line(line, state)
                    if chunk is not None:
                        yield chunk
