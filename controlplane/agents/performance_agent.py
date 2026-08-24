"""
Performance Agent — Anti-Hallucination (spec 3.2.3).

"Utilizes optimized RAG pipelines for verification. It employs quantized
Cross-Encoders to compute real-time semantic similarity scores between the
LLM output and grounded enterprise context vectors."

See controlplane/kb/retriever.py for the concrete similarity method (and
the honest note on why it's TF-IDF + numeric-consistency rather than a
downloaded neural cross-encoder in this sandbox). This agent's only job is
to wire that scorer into the event bus / action engine / kill-flag loop.
"""
from __future__ import annotations

import logging
import re

from ..action_engine import RiskInputs, decide, _action_for
from ..kb.retriever import get_retriever
from ..schemas import ActionDecision, AgentFinding, EscalationEvent, RawEvent, RiskLevel, new_id
from .base_agent import BaseAgent

logger = logging.getLogger("controlplane.agents.performance")


class PerformanceAgent(BaseAgent):
    name = "performance"

    def __init__(self):
        super().__init__()
        self._escalated: set[str] = set()
        self.retriever = get_retriever()

    async def handle(self, event: RawEvent) -> None:
        # Grounding only makes sense once we have enough text to judge —
        # skip tiny intermediate chunks, but always evaluate the final,
        # fully-accumulated turn.
        text = event.accumulated_text
        
        # Remove <think>...</think> blocks so we only score the actual response
        clean_text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
        # If the model is currently inside a think block (stream hasn't finished it),
        # remove that too.
        clean_text = re.sub(r'<think>.*', '', clean_text, flags=re.DOTALL).strip()
        
        if len(clean_text) < 40 and not event.is_final:
            return

        similarity = self.retriever.grounding_score(clean_text)
        inputs = RiskInputs(hallucination_similarity=similarity)
        decision = decide(inputs)

        finding = AgentFinding(
            trace_id=event.trace_id,
            event_id=event.event_id,
            agent=self.name,
            risk_level=decision.risk_level,
            score=round(1.0 - similarity, 3),  # higher score = worse, consistent with other agents
            reason=decision.reason,
            details={"grounding_similarity": round(similarity, 3), "top_match": self._top_match_title(text)},
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
                snippet=text[-300:], prompt=event.prompt,
            ))
            logger.warning("[performance] HIGH risk hallucination on trace %s: %s", event.trace_id, decision.reason)

    def _top_match_title(self, text: str) -> str:
        matches = self.retriever.top_matches(text, k=1)
        return matches[0].title if matches else "none"
