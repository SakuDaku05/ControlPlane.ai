"""
ControlPlane.ai — the trust and telemetry layer for enterprise AI.

This package implements the architecture described in the ControlPlane.ai
Product Requirements & Technical Specification Document:

  1. A non-blocking sidecar proxy (controlplane.proxy) that intercepts LLM
     traffic at the network layer and forks it to an async event bus without
     adding perceptible latency to the primary stream.
  2. An asynchronous event bus (controlplane.event_bus) built on Redis
     Streams + Pub/Sub, decoupling generation from evaluation.
  3. Three specialized micro-agents (controlplane.agents) that evaluate
     Performance (hallucination/grounding), Cost (token/compute burn), and
     Responsibility (PII, toxicity, data leakage) in parallel.
  4. A Dynamic Action Engine (controlplane.action_engine) that maps agent
     findings to a LOW / MEDIUM / HIGH risk tier and a PASS / EDIT / BLOCK
     action, per spec section 3.3.
  5. A HITL dashboard (controlplane.dashboard) that gives humans live
     visibility into telemetry and escalations.
"""

__version__ = "0.1.0"
