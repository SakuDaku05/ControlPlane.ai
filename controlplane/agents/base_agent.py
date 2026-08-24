"""
Shared consumer-loop skeleton for the three evaluation micro-agents (spec
3.2.3): "These agents run continuously in the background, decoupled from
the generation process to eliminate system bottlenecks."

Each concrete agent (performance_agent.py, cost_agent.py,
responsibility_agent.py) is its own independent asyncio task / process —
run_all.py runs all three plus the proxy plus the dashboard as sibling
asyncio tasks for the sandbox demo, but nothing here assumes that; in a
real deployment each agent would be its own horizontally-scalable
container/pod behind its own consumer group, which is exactly why we
designed the event bus (event_bus.py) around one Redis Streams consumer
group per agent in the first place.
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

from ..event_bus import EventBus, new_connected_bus
from ..schemas import RawEvent

logger = logging.getLogger("controlplane.agents")


class BaseAgent(ABC):
    name: str = "base"

    def __init__(self):
        self.bus: EventBus | None = None
        self._stop = asyncio.Event()

    @abstractmethod
    async def handle(self, event: RawEvent) -> None:
        """Process one RawEvent. Implementations call self.bus.publish_finding
        (and, for HIGH risk, self.bus.raise_kill_flag /
        self.bus.publish_escalation) as needed."""

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.bus = await new_connected_bus()
                await self.bus.ensure_groups()
                logger.info("[%s] agent started, consumer group ready", self.name)
                while not self._stop.is_set():
                    entries = await self.bus.consume_raw_events(self.name, count=20, block_ms=2000)
                    for entry_id, event in entries:
                        try:
                            await self.handle(event)
                        except Exception:
                            logger.exception("[%s] error handling event %s", self.name, entry_id)
                        finally:
                            await self.bus.ack_raw_event(self.name, entry_id)
            except Exception:
                logger.warning("[%s] Redis connection failed, retrying in 2s...", self.name)
                if self.bus:
                    try:
                        await self.bus.close()
                    except Exception:
                        pass
                    self.bus = None
                await asyncio.sleep(2.0)
