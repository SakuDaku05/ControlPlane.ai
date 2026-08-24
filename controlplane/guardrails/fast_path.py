"""
The synchronous hot path (spec 3.3's "<50ms TTFT overhead" budget).

Everything in this module runs *inline*, inside the proxy's streaming loop,
before a chunk is flushed to the client. It is deliberately narrow: only
regex-speed PII detection and lexical toxicity scoring, because those are
the only checks fast enough to run per-chunk without users noticing added
latency. The heavier checks — RAG-grounded hallucination detection
(embedding/cross-encoder inference) and cumulative cost aggregation — run
truly asynchronously in the micro-agents and can only affect an
*already-streaming* response via the kill-flag mechanism in event_bus.py,
or affect the *next* request via the HITL escalation + rate limiter.

This is the resolution to a real tension in the PRD: it asks for both
"zero perceived latency" (implying nothing runs in the blocking path) and
for HIGH-risk content to result in "the proxy forcefully tears down the
... connection" (which is only possible if *something* fast enough runs
inline). We resolve it the way real guardrail products do: cheap
deterministic checks inline, expensive semantic checks async.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..action_engine import Decision, RiskInputs, decide
from .pii import mask_pii
from .toxicity import score_toxicity


@dataclass
class FastPathResult:
    decision: Decision
    output_text: str  # possibly masked
    pii_categories: list[str]


def evaluate_chunk(text: str) -> FastPathResult:
    masked_text, pii_matches = mask_pii(text)
    tox = score_toxicity(text)

    pii_categories = [m.category for m in pii_matches]
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

    # Only actually swap in the masked text if we're auto-editing; a HIGH
    # verdict means the connection is about to be torn down and a LOW
    # verdict means nothing needs to change.
    output_text = masked_text if decision.action.value == "AUTO_EDIT" else text
    return FastPathResult(decision=decision, output_text=output_text, pii_categories=pii_categories)
