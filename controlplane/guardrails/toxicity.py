"""
Lightweight toxicity / harmful-content heuristic scorer.

Honest caveat (documented, not hidden): a production Responsibility Agent
should run a fine-tuned toxicity classifier (e.g. a distilled
toxic-comment transformer, Perspective API, or a moderation endpoint).
This sandbox cannot download model weights (no PyPI/HF egress), so this
module implements a transparent, weighted lexical/pattern scorer instead.
It is intentionally conservative and explainable — every score traces back
to specific matched terms/patterns, which is actually a nice property for
an auditable enterprise guardrail (a HITL reviewer can see *why* something
was flagged, not just a black-box float). controlplane/agents/
responsibility_agent.py is the seam where a real classifier would be
dropped in behind the same `score_toxicity(text) -> ToxicityResult`
interface used here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Weighted term/pattern buckets. Weights are heuristic, tuned so a single
# severe hit crosses HIGH risk and a couple of mild hits sit in MEDIUM.
_SEVERE_PATTERNS = [
    r"\bkill (?:you|yourself|him|her|them)\b",
    r"\bi will (?:hurt|attack|murder)\b",
    r"\bhow to (?:make|build) a bomb\b",
    r"\bslur\b",  # placeholder bucket; real deployments load a maintained slur list
]
_MODERATE_TERMS = [
    "idiot", "stupid", "worthless", "pathetic", "shut up", "hate you",
    "garbage", "loser", "disgusting", "moron",
]
_PROFANITY_TERMS = [
    "damn", "hell", "crap",
]


@dataclass
class ToxicityResult:
    score: float  # 0..1
    matched_severe: list[str] = field(default_factory=list)
    matched_moderate: list[str] = field(default_factory=list)
    matched_profanity: list[str] = field(default_factory=list)

    @property
    def reason(self) -> str:
        if self.matched_severe:
            return f"severe pattern(s): {', '.join(self.matched_severe)}"
        if self.matched_moderate:
            return f"moderate term(s): {', '.join(self.matched_moderate)}"
        if self.matched_profanity:
            return f"mild profanity: {', '.join(self.matched_profanity)}"
        return "clean"


_SEVERE_RE = [re.compile(p, re.IGNORECASE) for p in _SEVERE_PATTERNS]


def score_toxicity(text: str) -> ToxicityResult:
    lower = text.lower()
    result = ToxicityResult(score=0.0)

    for rx in _SEVERE_RE:
        m = rx.search(lower)
        if m:
            result.matched_severe.append(m.group())

    for term in _MODERATE_TERMS:
        if term in lower:
            result.matched_moderate.append(term)

    for term in _PROFANITY_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", lower):
            result.matched_profanity.append(term)

    score = 0.0
    if result.matched_severe:
        score = max(score, 0.85 + 0.05 * min(len(result.matched_severe), 3))
    if result.matched_moderate:
        score = max(score, 0.35 + 0.12 * min(len(result.matched_moderate), 4))
    if result.matched_profanity:
        score = max(score, 0.15 + 0.05 * min(len(result.matched_profanity), 3))

    result.score = min(1.0, score)
    return result
