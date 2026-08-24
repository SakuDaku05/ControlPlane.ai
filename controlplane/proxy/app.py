"""
The Network Proxy Layer / Sidecar (spec 3.2.1).

"Sits alongside the application container. Intercepts outbound requests to
the LLM provider and inbound streaming responses without blocking the
network layer."

Exposes two ingress routes that mirror the two most common enterprise
integration shapes:

    POST /v1/chat/completions   (OpenAI-compatible)
    POST /v1/messages           (Anthropic-compatible)

A real deployment points its application's `OPENAI_BASE_URL` /
`ANTHROPIC_BASE_URL` at this proxy instead of the real provider — that's
the entire integration; nothing about the calling application changes.
"""
from __future__ import annotations

import json
import time

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, StreamingResponse
from starlette.routing import Route
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from ..config import settings
from ..event_bus import new_connected_bus
from ..providers import get_wire_format, list_scenarios
from ..schemas import new_id
from .streaming import run_turn, run_turn_buffered

_STARTED_AT = time.time()


async def health(request: Request) -> JSONResponse:
    try:
        bus = await new_connected_bus()
        await bus._pub.ping()
        await bus.close()
        redis_ok = True
    except Exception as e:  # noqa: BLE001 - health check, report anything
        redis_ok = False
    return JSONResponse({
        "status": "ok" if redis_ok else "degraded",
        "service": "controlplane-proxy",
        "uptime_s": round(time.time() - _STARTED_AT, 1),
        "redis_ok": redis_ok,
        "demo_mode": settings.demo_mode,
        "mock_scenarios": list_scenarios(),
    })


async def root(request: Request) -> JSONResponse:
    return JSONResponse({
        "service": "ControlPlane.ai sidecar proxy",
        "endpoints": {
            "openai_compatible": "/v1/chat/completions",
            "anthropic_compatible": "/v1/messages",
            "health": "/healthz",
        },
        "docs": "See README.md for the mitigation protocol and demo scenarios.",
    })


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


async def _handle(request: Request, provider_name: str) -> StreamingResponse | JSONResponse | PlainTextResponse:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    wire = get_wire_format(provider_name)
    trace_id = request.headers.get("x-controlplane-trace-id") or new_id()

    if wire.wants_stream(body):
        async def gen():
            async for frame in run_turn(wire, provider_name, body, trace_id):
                yield frame.encode("utf-8")

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "X-ControlPlane-Trace-Id": trace_id,
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    result = await run_turn_buffered(wire, provider_name, body, trace_id)
    return JSONResponse(result, headers={"X-ControlPlane-Trace-Id": trace_id})


async def chat_completions(request: Request):
    return await _handle(request, "openai")


async def messages(request: Request):
    return await _handle(request, "anthropic")


app = Starlette(
    routes=[
        Route("/", root, methods=["GET"]),
        Route("/healthz", health, methods=["GET"]),
        Route("/v1/chat/completions", chat_completions, methods=["POST", "OPTIONS"]),
        Route("/v1/messages", messages, methods=["POST", "OPTIONS"]),
    ],
    middleware=[
        Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    ]
)
