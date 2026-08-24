"""
Responsibility Agent — Guardrails (spec 3.2.3).

"Deploys ultra-fast Named Entity Recognition (NER) pipelines and
lightweight toxicity classifiers to scan outbound data, preventing data
leakage and enforcing enterprise guardrails."

Why this agent exists *in addition to* the proxy's synchronous fast path
(guardrails/fast_path.py runs the same detectors inline): the fast path
only ever sees one small buffered chunk at a time (spec's "<50ms TTFT"
budget forces small buffers). A structured secret can straddle a chunk
boundary — e.g. "123-45-" flushed in one chunk and "6789" in the next —
and be invisible to a single-chunk regex scan while still being
completely reconstructable by the end user. This agent re-scans the full
*accumulated* text on every event, so anything that slipped past the hot
path chunk-by-chunk still gets caught, logged, and escalated once the
full picture is visible — the "defense in depth" half of the
Responsibility Agent's job, complementing rather than duplicating the
fast path.
"""
from __future__ import annotations

import logging

from ..action_engine import RiskInputs, decide, _action_for
from ..guardrails.pii import detect_pii
from ..guardrails.toxicity import score_toxicity
from ..schemas import ActionDecision, AgentFinding, EscalationEvent, RawEvent, RiskLevel, new_id
from .base_agent import BaseAgent

logger = logging.getLogger("controlplane.agents.responsibility")


class ResponsibilityAgent(BaseAgent):
    name = "responsibility"

    def __init__(self):
        super().__init__()
        self._escalated: set[str] = set()

    async def handle(self, event: RawEvent) -> None:
        text = event.accumulated_text
        pii_matches = detect_pii(text)
        tox = score_toxicity(text)

        pii_categories = sorted({m.category for m in pii_matches})
        pii_severity = max((m.severity for m in pii_matches), default=0.0)
        pii_high_confidence_count = sum(1 for m in pii_matches if m.severity >= 0.85)

        inputs = RiskInputs(
            pii_severity=pii_severity,
            pii_categories=pii_categories,
            pii_high_confidence_count=pii_high_confidence_count,
            toxicity_score=tox.score,
            toxicity_reason=tox.reason,
        )
        decision = decide(inputs)

        finding = AgentFinding(
            trace_id=event.trace_id,
            event_id=event.event_id,
            agent=self.name,
            risk_level=decision.risk_level,
            score=round(max(pii_severity, tox.score), 3),
            reason=decision.reason,
            details={
                "pii_categories": pii_categories,
                "toxicity_score": round(tox.score, 3),
                "caught_by_fast_path": event.fast_path_flag in ("fast_path_block", "fast_path_edit"),
                "scanned_full_accumulated_text": True,
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

        already_handled_inline = event.fast_path_flag == "fast_path_block"
        if decision.risk_level == RiskLevel.HIGH and not already_handled_inline and event.trace_id not in self._escalated:
            self._escalated.add(event.trace_id)
            await self.bus.raise_kill_flag(event.trace_id, decision.reason)
            boundary_evasion = bool(pii_matches) and event.fast_path_flag is None
            await self.bus.publish_escalation(EscalationEvent(
                trace_id=event.trace_id, risk_level=RiskLevel.HIGH,
                reason=decision.reason + (" [caught on deep re-scan, not the per-chunk fast path]" if boundary_evasion else ""),
                snippet=text[-300:], prompt=event.prompt,
            ))
            logger.warning("[responsibility] HIGH risk on trace %s: %s", event.trace_id, decision.reason)
