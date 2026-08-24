"""
Typed event schemas shared across the proxy, the event bus, the three
evaluation micro-agents, the Dynamic Action Engine, and the HITL dashboard.

Everything that crosses the Redis event bus is JSON, and every JSON payload
is one of these models — this is the contract that lets each component be
developed, tested, and scaled independently (per spec 3.1: "specialized
analytical micro-agents").
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def now() -> float:
    return time.time()


class RiskLevel(str, Enum):
    LOW = "LOW_RISK"
    MEDIUM = "MEDIUM_RISK"
    HIGH = "HIGH_RISK"

    @property
    def rank(self) -> int:
        return {"LOW_RISK": 0, "MEDIUM_RISK": 1, "HIGH_RISK": 2}[self.value]


class Action(str, Enum):
    PASS = "PASS_THROUGH"
    EDIT = "AUTO_EDIT"
    BLOCK = "BLOCK_AND_ESCALATE"


class Provider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    MOCK = "mock"


class RawEvent(BaseModel):
    """One unit of LLM output forked off the primary stream by the proxy.

    The proxy emits one of these per "flush chunk" (see config.fast_path_*)
    plus a final one with `is_final=True` carrying the full accumulated
    text, so agents that need full-response context (e.g. the Performance
    Agent's grounding check) can wait for it while agents that can work
    incrementally (Responsibility) act on every chunk.
    """

    event_id: str = Field(default_factory=new_id)
    trace_id: str
    provider: Provider
    model: str
    prompt: str
    chunk_text: str
    accumulated_text: str
    is_final: bool = False
    prompt_tokens: int = 0
    completion_tokens_so_far: int = 0
    fast_path_flag: Optional[str] = None  # set if the synchronous hot-path already flagged this chunk
    created_at: float = Field(default_factory=now)


class AgentFinding(BaseModel):
    """One micro-agent's verdict on a RawEvent."""

    finding_id: str = Field(default_factory=new_id)
    trace_id: str
    event_id: str
    agent: str  # "performance" | "cost" | "responsibility"
    risk_level: RiskLevel
    score: float  # 0..1, agent-specific meaning, higher = worse
    reason: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=now)


class ActionDecision(BaseModel):
    """The Dynamic Action Engine's fused decision for a trace."""

    decision_id: str = Field(default_factory=new_id)
    trace_id: str
    event_id: str
    risk_level: RiskLevel
    action: Action
    reason: str
    contributing_agents: list[str] = Field(default_factory=list)
    latency_impact: str = ""
    created_at: float = Field(default_factory=now)


class EscalationEvent(BaseModel):
    """Fired to the HITL dashboard (and optional external webhook) on BLOCK."""

    escalation_id: str = Field(default_factory=new_id)
    trace_id: str
    risk_level: RiskLevel
    reason: str
    snippet: str
    prompt: str
    webhook_fired: bool = False
    created_at: float = Field(default_factory=now)


class CostSnapshot(BaseModel):
    trace_id: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    session_burn_rate_tokens_per_min: float
    over_budget: bool
    created_at: float = Field(default_factory=now)
