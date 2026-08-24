"""
Anthropic-compatible wire format: `/v1/messages` request/response shape and
its named-SSE-event streaming protocol (message_start, content_block_delta,
message_delta, message_stop, ...).
"""
from __future__ import annotations

import json
import time
import uuid

from .base import StreamChunk, WireFormat
from ..config import settings


class AnthropicWireFormat(WireFormat):
    name = "anthropic"
    upstream_url = f"{settings.anthropic_base_url}/messages"

    def extract_prompt(self, body: dict) -> str:
        parts = []
        system = body.get("system")
        if system:
            parts.append(f"system: {system}")
        for m in body.get("messages", []):
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
            parts.append(f"{m.get('role', 'user')}: {content}")
        return "\n".join(parts)

    def extract_model(self, body: dict) -> str:
        return body.get("model", "claude-sonnet-4-5")

    def wants_stream(self, body: dict) -> bool:
        return bool(body.get("stream", False))

    def build_upstream_payload(self, body: dict) -> dict:
        payload = dict(body)
        payload["stream"] = True
        payload.setdefault("max_tokens", 1024)
        return payload

    def auth_headers(self, api_key: str) -> dict:
        return {
            "x-api-key": api_key,
            "anthropic-version": settings.anthropic_version,
            "content-type": "application/json",
        }

    def parse_upstream_line(self, line: str, state: dict) -> StreamChunk | None:
        line = line.strip()
        if not line.startswith("data:"):
            return None
        data = line[len("data:"):].strip()
        if not data:
            return None
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            return None

        etype = obj.get("type")
        if etype == "message_start":
            msg = obj.get("message", {})
            state["id"] = msg.get("id")
            state["model"] = msg.get("model")
            usage = msg.get("usage", {})
            return StreamChunk(text="", finished=False, prompt_tokens=usage.get("input_tokens"))
        if etype == "content_block_delta":
            delta = obj.get("delta", {})
            if delta.get("type") == "text_delta":
                return StreamChunk(text=delta.get("text", ""), finished=False)
            return None
        if etype == "message_delta":
            usage = obj.get("usage", {})
            return StreamChunk(text="", finished=False, completion_tokens=usage.get("output_tokens"))
        if etype == "message_stop":
            return StreamChunk(text="", finished=True)
        return None

    def format_stream_start(self, meta: dict) -> str:
        message_start = {
            "type": "message_start",
            "message": {
                "id": meta.get("id", f"msg_{uuid.uuid4().hex[:24]}"),
                "type": "message",
                "role": "assistant",
                "model": meta.get("model", "claude-sonnet-4-5"),
                "content": [],
                "stop_reason": None,
                "usage": {"input_tokens": meta.get("prompt_tokens", 0), "output_tokens": 0},
            },
        }
        content_block_start = {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        }
        return (
            f"event: message_start\ndata: {json.dumps(message_start)}\n\n"
            f"event: content_block_start\ndata: {json.dumps(content_block_start)}\n\n"
        )

    def format_client_chunk(self, chunk: StreamChunk, meta: dict) -> str:
        obj = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": chunk.text},
        }
        return f"event: content_block_delta\ndata: {json.dumps(obj)}\n\n"

    def format_stream_end(self, meta: dict) -> str:
        content_block_stop = {"type": "content_block_stop", "index": 0}
        message_delta = {
            "type": "message_delta",
            "delta": {"stop_reason": meta.get("stop_reason", "end_turn"), "stop_sequence": None},
            "usage": {"output_tokens": meta.get("completion_tokens", 0)},
        }
        message_stop = {"type": "message_stop"}
        return (
            f"event: content_block_stop\ndata: {json.dumps(content_block_stop)}\n\n"
            f"event: message_delta\ndata: {json.dumps(message_delta)}\n\n"
            f"event: message_stop\ndata: {json.dumps(message_stop)}\n\n"
        )

    def format_error_frame(self, message: str, meta: dict) -> str:
        note = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": f"\n\n[ControlPlane.ai blocked this response: {message}]"},
        }
        content_block_stop = {"type": "content_block_stop", "index": 0}
        message_delta = {
            "type": "message_delta",
            "delta": {"stop_reason": "stop_sequence", "stop_sequence": "controlplane_block"},
            "usage": {"output_tokens": meta.get("completion_tokens", 0)},
        }
        message_stop = {"type": "message_stop"}
        return (
            f"event: content_block_delta\ndata: {json.dumps(note)}\n\n"
            f"event: content_block_stop\ndata: {json.dumps(content_block_stop)}\n\n"
            f"event: message_delta\ndata: {json.dumps(message_delta)}\n\n"
            f"event: message_stop\ndata: {json.dumps(message_stop)}\n\n"
        )

    def non_streaming_response(self, full_text: str, meta: dict) -> dict:
        return {
            "id": meta.get("id", f"msg_{uuid.uuid4().hex[:24]}"),
            "type": "message",
            "role": "assistant",
            "model": meta.get("model", "claude-sonnet-4-5"),
            "content": [{"type": "text", "text": full_text}],
            "stop_reason": meta.get("stop_reason", "end_turn"),
            "usage": {
                "input_tokens": meta.get("prompt_tokens", 0),
                "output_tokens": meta.get("completion_tokens", 0),
            },
            "controlplane": meta.get("controlplane", {}),
        }
