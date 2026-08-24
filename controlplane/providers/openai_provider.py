"""
OpenAI-compatible wire format: `/v1/chat/completions` request/response
shape and SSE framing, as used by the OpenAI Python/Node SDKs and every
OpenAI-compatible gateway (vLLM, LiteLLM, Azure OpenAI, etc.).
"""
from __future__ import annotations

import json
import time
import uuid

from .base import StreamChunk, WireFormat
from ..config import settings


class OpenAIWireFormat(WireFormat):
    name = "openai"
    upstream_url = f"{settings.openai_base_url}/chat/completions"

    def extract_prompt(self, body: dict) -> str:
        messages = body.get("messages", [])
        parts = []
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, list):  # multimodal-style content blocks
                content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
            parts.append(f"{m.get('role', 'user')}: {content}")
        return "\n".join(parts)

    def extract_model(self, body: dict) -> str:
        return body.get("model", "gpt-4o-mini")

    def wants_stream(self, body: dict) -> bool:
        return bool(body.get("stream", False))

    def build_upstream_payload(self, body: dict) -> dict:
        payload = dict(body)
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
        return payload

    def auth_headers(self, api_key: str) -> dict:
        return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def parse_upstream_line(self, line: str, state: dict) -> StreamChunk | None:
        line = line.strip()
        if not line or not line.startswith("data:"):
            return None
        data = line[len("data:"):].strip()
        if data == "[DONE]":
            return StreamChunk(text="", finished=True)
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            return None
        state.setdefault("id", obj.get("id"))
        state.setdefault("model", obj.get("model"))
        choices = obj.get("choices") or [{}]
        delta = choices[0].get("delta", {})
        text = delta.get("content") or ""
        usage = obj.get("usage") or {}
        return StreamChunk(
            text=text,
            finished=False,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )

    def format_stream_start(self, meta: dict) -> str:
        return ""  # the first delta chunk doubles as the start of stream for OpenAI's wire format

    def format_client_chunk(self, chunk: StreamChunk, meta: dict) -> str:
        obj = {
            "id": meta.get("id", f"chatcmpl-{uuid.uuid4().hex[:20]}"),
            "object": "chat.completion.chunk",
            "created": meta.get("created", int(time.time())),
            "model": meta.get("model", "gpt-4o-mini"),
            "choices": [{"index": 0, "delta": {"content": chunk.text}, "finish_reason": None}],
        }
        return f"data: {json.dumps(obj)}\n\n"

    def format_stream_end(self, meta: dict) -> str:
        obj = {
            "id": meta.get("id", f"chatcmpl-{uuid.uuid4().hex[:20]}"),
            "object": "chat.completion.chunk",
            "created": meta.get("created", int(time.time())),
            "model": meta.get("model", "gpt-4o-mini"),
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        return f"data: {json.dumps(obj)}\n\ndata: [DONE]\n\n"

    def format_error_frame(self, message: str, meta: dict) -> str:
        obj = {
            "id": meta.get("id", f"chatcmpl-{uuid.uuid4().hex[:20]}"),
            "object": "chat.completion.chunk",
            "created": meta.get("created", int(time.time())),
            "model": meta.get("model", "gpt-4o-mini"),
            "choices": [{
                "index": 0,
                "delta": {"content": f"\n\n[ControlPlane.ai blocked this response: {message}]"},
                "finish_reason": "content_filter",
            }],
        }
        return f"data: {json.dumps(obj)}\n\ndata: [DONE]\n\n"

    def non_streaming_response(self, full_text: str, meta: dict) -> dict:
        return {
            "id": meta.get("id", f"chatcmpl-{uuid.uuid4().hex[:20]}"),
            "object": "chat.completion",
            "created": meta.get("created", int(time.time())),
            "model": meta.get("model", "gpt-4o-mini"),
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": full_text},
                "finish_reason": meta.get("finish_reason", "stop"),
            }],
            "usage": {
                "prompt_tokens": meta.get("prompt_tokens", 0),
                "completion_tokens": meta.get("completion_tokens", 0),
                "total_tokens": meta.get("prompt_tokens", 0) + meta.get("completion_tokens", 0),
            },
            "controlplane": meta.get("controlplane", {}),
        }
