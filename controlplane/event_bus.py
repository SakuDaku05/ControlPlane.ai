"""
The Asynchronous Event Bus (spec 3.2.2).

"Decouples the proxy from the evaluation layer. Handles high-velocity
pub/sub messaging. The proxy publishes the intercepted payload; the
micro-agents subscribe and consume it."

Implementation choice: Redis Streams with one consumer group per
micro-agent, rather than plain Pub/Sub. Plain Pub/Sub is fire-and-forget —
a message published while an agent is briefly restarting or busy is lost
forever, which is unacceptable for a Responsibility/PII agent whose whole
job is "don't let this slip through." Streams give us:

  * Fan-out: every agent (performance / cost / responsibility) gets its own
    consumer group, so each one independently sees every event — this is
    what the spec's "specialized micro-agents subscribe and consume it"
    calls for.
  * At-least-once delivery with XACK, so a crashed agent's unacked messages
    are re-claimable instead of silently dropped.
  * A natural audit log (XRANGE over cp:events:actions) for compliance,
    which a pure pub/sub topic doesn't give you for free.

This still satisfies "Redis / Kafka" from the spec — Streams are Redis's
answer to a Kafka-style partitioned log, and this module's interface is
intentionally narrow enough that swapping the transport for real Kafka
(e.g. aiokafka) later only touches this one file.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import AsyncIterator, Optional

from .config import settings
import redis
import redis.asyncio as aioredis
from redis.exceptions import ResponseError
from .schemas import (
    ActionDecision,
    AgentFinding,
    CostSnapshot,
    EscalationEvent,
    RawEvent,
)

AGENT_NAMES = ("performance", "cost", "responsibility")


def _group_for(agent_name: str) -> str:
    return f"{settings.consumer_group}:{agent_name}"


class EventBus:
    """Thin, schema-aware facade over RedisClient for the streams
    ControlPlane.ai cares about. One instance per process/worker."""

    def __init__(self):
        self._pub = aioredis.Redis(host=settings.redis_host, port=settings.redis_port, decode_responses=True)
        self._consumer_id = uuid.uuid4().hex[:8]

    async def connect(self) -> "EventBus":
        await self._pub.ping()
        return self

    async def close(self) -> None:
        await self._pub.close()

    # ---- setup ----------------------------------------------------------------

    async def ensure_groups(self) -> None:
        for agent in AGENT_NAMES:
            try:
                await self._pub.xgroup_create(settings.stream_raw_events, _group_for(agent), mkstream=True)
            except ResponseError as e:
                if "BUSYGROUP" not in str(e): raise
        for stream, group in [
            (settings.stream_agent_results, f"{settings.consumer_group}:action-engine"),
            (settings.stream_actions, f"{settings.consumer_group}:dashboard-actions"),
            (settings.stream_escalations, f"{settings.consumer_group}:dashboard-escalations"),
            (settings.stream_agent_results, f"{settings.consumer_group}:dashboard-results")
        ]:
            try:
                await self._pub.xgroup_create(stream, group, mkstream=True)
            except ResponseError as e:
                if "BUSYGROUP" not in str(e): raise

    # ---- proxy -> agents --------------------------------------------------------

    async def publish_raw_event(self, event: RawEvent) -> str:
        return await self._pub.xadd(settings.stream_raw_events, {"json": event.model_dump_json()})

    async def consume_raw_events(self, agent_name: str, count: int = 10, block_ms: int = 2000) -> list[tuple[str, RawEvent]]:
        reply = await self._pub.xreadgroup(_group_for(agent_name), self._consumer_id, {settings.stream_raw_events: ">"}, count=count, block=block_ms)
        if not reply: return []
        entries = reply[0][1]
        return [(eid, RawEvent.model_validate_json(fields["json"])) for eid, fields in entries]

    async def ack_raw_event(self, agent_name: str, entry_id: str) -> None:
        await self._pub.xack(settings.stream_raw_events, _group_for(agent_name), entry_id)

    # ---- agents -> action engine / dashboard -------------------------------------

    async def publish_finding(self, finding: AgentFinding) -> str:
        return await self._pub.xadd(settings.stream_agent_results, {"json": finding.model_dump_json()})

    async def consume_findings(self, group: str, count: int = 20, block_ms: int = 2000) -> list[tuple[str, AgentFinding]]:
        reply = await self._pub.xreadgroup(group, self._consumer_id, {settings.stream_agent_results: ">"}, count=count, block=block_ms)
        if not reply: return []
        entries = reply[0][1]
        return [(eid, AgentFinding.model_validate_json(fields["json"])) for eid, fields in entries]

    async def ack_finding(self, group: str, entry_id: str) -> None:
        await self._pub.xack(settings.stream_agent_results, group, entry_id)

    # ---- action engine -> audit trail / dashboard --------------------------------

    async def publish_action(self, decision: ActionDecision) -> str:
        return await self._pub.xadd(settings.stream_actions, {"json": decision.model_dump_json()})

    async def consume_actions(self, group: str, count: int = 20, block_ms: int = 2000) -> list[tuple[str, ActionDecision]]:
        reply = await self._pub.xreadgroup(group, self._consumer_id, {settings.stream_actions: ">"}, count=count, block=block_ms)
        if not reply: return []
        entries = reply[0][1]
        return [(eid, ActionDecision.model_validate_json(fields["json"])) for eid, fields in entries]

    async def ack_action(self, group: str, entry_id: str) -> None:
        await self._pub.xack(settings.stream_actions, group, entry_id)

    # ---- escalations (HIGH risk -> HITL) ------------------------------------------

    async def publish_escalation(self, escalation: EscalationEvent) -> str:
        return await self._pub.xadd(settings.stream_escalations, {"json": escalation.model_dump_json()})

    async def consume_escalations(self, group: str, count: int = 20, block_ms: int = 2000) -> list[tuple[str, EscalationEvent]]:
        reply = await self._pub.xreadgroup(group, self._consumer_id, {settings.stream_escalations: ">"}, count=count, block=block_ms)
        if not reply: return []
        entries = reply[0][1]
        return [(eid, EscalationEvent.model_validate_json(fields["json"])) for eid, fields in entries]

    async def ack_escalation(self, group: str, entry_id: str) -> None:
        await self._pub.xack(settings.stream_escalations, group, entry_id)

    # ---- cost telemetry (hash counters, not a stream — cheap running totals) -----

    async def bump_cost(self, trace_id: str, prompt_tokens: int, completion_tokens: int, cost_usd: float) -> None:
        key = f"cp:cost:{trace_id}"
        await self._pub.hincrby(key, "prompt_tokens", prompt_tokens)
        await self._pub.hincrby(key, "completion_tokens", completion_tokens)
        await self._pub.hincrbyfloat(key, "cost_usd", cost_usd)
        await self._pub.expire(key, 3600)
        await self._pub.incrbyfloat("cp:cost:session_total_usd", cost_usd)

    async def bump_request(self) -> None:
        await self._pub.incr("cp:requests:session_total")

    async def get_session_total_requests(self) -> int:
        val = await self._pub.get("cp:requests:session_total")
        return int(val) if val else 0

    async def reset_session_totals(self) -> None:
        await self._pub.delete("cp:requests:session_total", "cp:cost:session_total_usd")

    async def get_cost(self, trace_id: str) -> dict:
        return await self._pub.hgetall(f"cp:cost:{trace_id}")

    async def get_session_total_cost(self) -> float:
        val = await self._pub.get("cp:cost:session_total_usd")
        return float(val) if val else 0.0

    # ---- kill-switch: lets the still-open proxy connection learn "abort now" -----
    # Even though heavy grounding checks run async, a request whose stream is
    # still open when a HIGH verdict lands should still get torn down instead
    # of finishing uninterrupted (spec 3.3, HIGH RISK -> "Connection
    # Terminated"). We use a tiny TTL'd key as a cross-process flag the proxy
    # polls between chunks — cheaper than another stream for a single bit.

    async def raise_kill_flag(self, trace_id: str, reason: str) -> None:
        await self._pub.set(f"cp:kill:{trace_id}", reason, ex=120)

    async def check_kill_flag(self, trace_id: str) -> Optional[str]:
        return await self._pub.get(f"cp:kill:{trace_id}")


async def new_connected_bus() -> EventBus:
    bus = EventBus()
    await bus.connect()
    return bus
