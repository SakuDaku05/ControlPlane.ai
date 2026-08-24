"""
The Dynamic Action Engine (spec 2.2 Feature 3 / spec 3.3).

"The system must automatically mitigate risks based on severity, deciding
instantly whether to pass, edit, or block the output."

This module is the single source of truth for spec 3.3's mitigation table:

    Risk Level   Scenario        Action Protocol
    LOW          Pass-Through    Stream proceeds uninterrupted.
    MEDIUM       Auto-Edit       Inline PII mask before flushing to client.
    HIGH         Block&Escalate  Tear down the connection, fire HITL webhook.

Both the proxy's synchronous fast path (controlplane/guardrails/fast_path.py,
which can only see PII + toxicity on the chunk in hand) and the async
micro-agents (which can see grounding/hallucination and cumulative cost)
call into `decide()` here, so there is exactly one place that encodes
"what counts as HIGH" — no drift between the hot path and the background
evaluators.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .config import settings
from .schemas import Action, RiskLevel


@dataclass
class RiskInputs:
    """Whatever a caller currently knows. Every field is optional because
    the fast path only ever has pii/toxicity, while the async agents fill
    in hallucination/cost on top."""

    pii_severity: float = 0.0
    pii_categories: list[str] = field(default_factory=list)
    pii_high_confidence_count: int = 0  # count of matches with severity >= 0.85 (SSN, card, API key, ...)
    toxicity_score: float = 0.0
    toxicity_reason: str = ""
    hallucination_similarity: Optional[float] = None  # None = not evaluated yet
    cost_over_budget: bool = False
    cost_burn_ratio: float = 0.0  # session_cost / session_budget


@dataclass
class Decision:
    risk_level: RiskLevel
    action: Action
    reason: str
    contributing: list[str]
    latency_impact: str


def decide(inputs: RiskInputs) -> Decision:
    contributing: list[str] = []
    reasons: list[str] = []
    level = RiskLevel.LOW

    # --- Responsibility: toxicity -------------------------------------------------
    if inputs.toxicity_score >= settings.toxicity_high_min:
        level = RiskLevel.HIGH
        contributing.append("responsibility")
        reasons.append(f"severe toxicity ({inputs.toxicity_reason}, score={inputs.toxicity_score:.2f})")
    elif inputs.toxicity_score >= settings.toxicity_medium_min:
        level = max(level, RiskLevel.MEDIUM, key=lambda r: r.rank)
        contributing.append("responsibility")
        reasons.append(f"moderate toxicity ({inputs.toxicity_reason}, score={inputs.toxicity_score:.2f})")

    # --- Responsibility: PII / data leakage -----------------------------------------
    if inputs.pii_severity > 0:
        # Structured, high-confidence PII (SSN, card, API key) is always at
        # least an auto-edit; if it's *also* egregious (e.g. multiple
        # secrets in one turn) escalate to HIGH ("massive ... data
        # leakage" reading of spec 3.3's HIGH row, which explicitly covers
        # "severe toxicity or massive context hallucination" but the same
        # tiering principle applies to a mass data-leakage event).
        if inputs.pii_high_confidence_count >= 2:
            level = RiskLevel.HIGH
            reasons.append(f"multiple high-confidence PII leaks: {inputs.pii_categories}")
        else:
            level = max(level, RiskLevel.MEDIUM, key=lambda r: r.rank)
            reasons.append(f"PII detected: {inputs.pii_categories}")
        contributing.append("responsibility")

    # --- Performance: grounding / hallucination -------------------------------------
    if inputs.hallucination_similarity is not None:
        sim = inputs.hallucination_similarity
        if sim <= settings.hallucination_high_min:
            level = RiskLevel.HIGH
            contributing.append("performance")
            reasons.append(f"massive context hallucination (grounding similarity={sim:.2f})")
        elif sim <= settings.hallucination_low_max:
            level = max(level, RiskLevel.MEDIUM, key=lambda r: r.rank)
            contributing.append("performance")
            reasons.append(f"weak grounding (similarity={sim:.2f}); confidence flagged")

    # --- Cost: compute burn ------------------------------------------------------------
    if inputs.cost_over_budget:
        level = RiskLevel.HIGH
        contributing.append("cost")
        reasons.append(f"session budget exceeded (burn ratio={inputs.cost_burn_ratio:.2f}x)")
    elif inputs.cost_burn_ratio >= 0.75:
        level = max(level, RiskLevel.MEDIUM, key=lambda r: r.rank)
        contributing.append("cost")
        reasons.append(f"approaching budget ({inputs.cost_burn_ratio:.0%} of session cap)")

    action, latency = _action_for(level)
    reason = "; ".join(reasons) if reasons else "no risk signals"
    return Decision(risk_level=level, action=action, reason=reason, contributing=contributing, latency_impact=latency)


def _action_for(level: RiskLevel) -> tuple[Action, str]:
    if level == RiskLevel.HIGH:
        return Action.BLOCK, "Connection Terminated"
    if level == RiskLevel.MEDIUM:
        return Action.EDIT, "Minimal (inline masking)"
    return Action.PASS, "<50ms TTFT overhead"
