"""
Cost Telemetry Agent (spec 3.2.3).

"Analyzes active generation loops using streaming token heuristics. It
leverages Redis caching and applies dynamic rate-limiting to prevent
compute burn."

This agent maintains a running per-trace token/cost total in Redis (the
"Redis caching" the spec calls out — see event_bus.bump_cost, a plain hash
counter, not a stream, because a running total is exactly what a cache is
for) and raises the kill flag the instant a trace's *projected* cost
exceeds the configured session budget, so a runaway generation (spec
Feature 2: "prevent excessive billing") gets cut off mid-stream rather
than after the fact.
"""
from __future__ import annotations

import logging

from ..action_engine import RiskInputs, decide, _action_for
from ..config import settings
from ..schemas import ActionDecision, AgentFinding, EscalationEvent, RawEvent, RiskLevel, new_id
from .base_agent import BaseAgent

logger = logging.getLogger("controlplane.agents.cost")


PRICING_TABLE = {
    # model: (cost_per_1k_prompt, cost_per_1k_completion)
    "qwen/qwen3.6-27b": (0.00015, 0.00060),
    "gpt-4": (0.03, 0.06),
    "gpt-3.5-turbo": (0.0015, 0.002),
    "claude-3-opus": (0.015, 0.075),
    "claude-3-sonnet": (0.003, 0.015),
    "gemini-1.5-pro": (0.00125, 0.00375)
}

def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    if model in PRICING_TABLE:
        p_cost, c_cost = PRICING_TABLE[model]
        return (prompt_tokens / 1000 * p_cost) + (completion_tokens / 1000 * c_cost)
        
    return (
        prompt_tokens / 1000 * settings.cost_per_1k_prompt_tokens
        + completion_tokens / 1000 * settings.cost_per_1k_completion_tokens
    )


class CostAgent(BaseAgent):
    name = "cost"

    def __init__(self):
        super().__init__()
        self._escalated: set[str] = set()

    async def handle(self, event: RawEvent) -> None:
        completion_delta = self._token_delta(event)
        cost_delta = estimate_cost_usd(event.model, 0, completion_delta)
        # Track cumulative cost for this trace via the Redis hash counter.
        await self.bus.bump_cost(
            event.trace_id,
            prompt_tokens=0,
            completion_tokens=completion_delta,
            cost_usd=cost_delta,
        )
        totals = await self.bus.get_cost(event.trace_id)
        total_cost = float(totals.get("cost_usd", 0.0))
        prompt_cost = estimate_cost_usd(event.model, event.prompt_tokens, 0)
        session_cost = total_cost + prompt_cost
        burn_ratio = session_cost / settings.session_budget_usd if settings.session_budget_usd else 0.0

        inputs = RiskInputs(cost_over_budget=burn_ratio >= 1.0, cost_burn_ratio=burn_ratio)
        decision = decide(inputs)

        finding = AgentFinding(
            trace_id=event.trace_id,
            event_id=event.event_id,
            agent=self.name,
            risk_level=decision.risk_level,
            score=round(min(1.0, burn_ratio), 3),
            reason=decision.reason,
            details={
                "session_cost_usd": round(session_cost, 5),
                "session_budget_usd": settings.session_budget_usd,
                "completion_tokens_so_far": event.completion_tokens_so_far,
                "burn_ratio": round(burn_ratio, 3),
            },
        )
        await self.bus.publish_finding(finding)
        await self.bus.publish_action(ActionDecision(
            trace_id=event.trace_id,
            event_id=new_id(),
            risk_level=decision.risk_level,
            action=_action_for(decision.risk_level)[0],
            reason=finding.reason,
            contributing_agents=[self.name],
            latency_impact=decision.latency_impact,
        ))

        if decision.risk_level == RiskLevel.HIGH and event.trace_id not in self._escalated:
            self._escalated.add(event.trace_id)
            await self.bus.raise_kill_flag(event.trace_id, decision.reason)
            await self.bus.publish_escalation(EscalationEvent(
                trace_id=event.trace_id, risk_level=RiskLevel.HIGH, reason=decision.reason,
                snippet=event.accumulated_text[-300:], prompt=event.prompt,
            ))
            logger.warning("[cost] session budget exceeded on trace %s: $%.4f", event.trace_id, session_cost)

    @staticmethod
    def _token_delta(event: RawEvent) -> int:
        # Cheap proxy for "tokens produced by this chunk": word count of
        # the chunk text (see util.estimate_tokens for the full-string
        # variant used elsewhere; here we want just the delta contributed
        # by this event to keep the running Redis counter additive).
        if not event.chunk_text:
            return 0
        words = event.chunk_text.split()
        return max(1, round(len(words) / 0.75))
