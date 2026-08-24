"""
One-command launcher: sidecar proxy + HITL dashboard + all three
micro-agents, as sibling asyncio tasks in a single process.

This is a convenience for local dev / the hackathon demo. In a real
deployment each of these five pieces (proxy, dashboard, performance agent,
cost agent, responsibility agent) is its own independently-scalable
process/container — see docker-compose.yml, which runs them exactly that
way — and the code needs zero changes to move from "all in one process"
to "five separate processes" because they only ever talk to each other
through Redis.

Usage:
    redis-server &                 # make sure Redis is running first
    python -m controlplane.run_all
"""
from __future__ import annotations

import asyncio
import logging
import signal

import uvicorn

from .agents import ALL_AGENTS
from .config import settings
from .dashboard.app import app as dashboard_app
from .proxy.app import app as proxy_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")

BANNER = f"""
============================================================
  ControlPlane.ai — trust & telemetry layer for enterprise AI
============================================================
  Sidecar proxy   : http://{settings.proxy_host}:{settings.proxy_port}
    OpenAI-compat : POST /v1/chat/completions
    Anthropic-comp: POST /v1/messages
    Health        : GET  /healthz

  HITL dashboard  : http://{settings.dashboard_host}:{settings.dashboard_port}

  Redis           : {settings.redis_host}:{settings.redis_port}
  Demo mode       : {settings.demo_mode}  (no API key -> falls back to the
                     offline `mock:<scenario>` provider automatically)

  Try it:
    python -m demo.demo_client --scenario high_toxicity
    python -m demo.demo_client --list
============================================================
"""


async def main() -> None:
    print(BANNER)

    proxy_server = uvicorn.Server(uvicorn.Config(
        proxy_app, host=settings.proxy_host, port=settings.proxy_port, log_level="warning"))
    dashboard_server = uvicorn.Server(uvicorn.Config(
        dashboard_app, host=settings.dashboard_host, port=settings.dashboard_port, log_level="warning"))

    agents = [cls() for cls in ALL_AGENTS]

    tasks = [
        asyncio.create_task(proxy_server.serve(), name="proxy"),
        asyncio.create_task(dashboard_server.serve(), name="dashboard"),
    ] + [asyncio.create_task(a.run(), name=f"agent:{a.name}") for a in agents]

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_signal():
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            pass  # Windows

    await stop_event.wait()
    logging.getLogger("controlplane").info("shutting down...")
    for a in agents:
        a.stop()
    proxy_server.should_exit = True
    dashboard_server.should_exit = True
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
