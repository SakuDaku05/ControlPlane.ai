"""
Run exactly one micro-agent as its own process — this is what
docker-compose.yml uses so each of the three evaluation agents is an
independently restartable/scalable service, matching spec 3.2.3's
"specialized micro-agents" framing (as opposed to run_all.py, which
bundles everything into one process for local/demo convenience).

Usage:
    python -m controlplane.run_agent performance
    python -m controlplane.run_agent cost
    python -m controlplane.run_agent responsibility
"""
from __future__ import annotations

import asyncio
import logging
import sys

from .agents import CostAgent, PerformanceAgent, ResponsibilityAgent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")

AGENTS = {
    "performance": PerformanceAgent,
    "cost": CostAgent,
    "responsibility": ResponsibilityAgent,
}


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in AGENTS:
        print(f"Usage: python -m controlplane.run_agent <{'|'.join(AGENTS)}>")
        sys.exit(1)
    agent = AGENTS[sys.argv[1]]()
    asyncio.run(agent.run())


if __name__ == "__main__":
    main()
