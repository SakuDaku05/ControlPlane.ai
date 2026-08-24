"""
Central configuration for ControlPlane.ai.

Everything is env-driven (12-factor style) so the same code runs in the
sandboxed demo environment, in docker-compose, or in a real enterprise
deployment. See .env.example for the full list of knobs.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    # --- Redis / event bus -------------------------------------------------
    redis_host: str = os.environ.get("REDIS_HOST", "127.0.0.1")
    redis_port: int = _int("REDIS_PORT", 6379)

    # Stream names (the "topics" of the async event bus).
    stream_raw_events: str = "cp:events:raw"          # proxy -> agents (chunks/full turns)
    stream_agent_results: str = "cp:events:results"   # agents -> action engine / dashboard
    stream_actions: str = "cp:events:actions"         # action engine decisions (audit trail)
    stream_escalations: str = "cp:events:escalations" # HIGH risk -> HITL webhook/dashboard
    consumer_group: str = "cp-agents"

    # --- Upstream LLM providers ---------------------------------------------
    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    openai_base_url: str = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    anthropic_base_url: str = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1")
    anthropic_version: str = os.environ.get("ANTHROPIC_VERSION", "2023-06-01")

    # --- Proxy ---------------------------------------------------------------
    proxy_host: str = os.environ.get("PROXY_HOST", "0.0.0.0")
    proxy_port: int = _int("PROXY_PORT", 8080)

    # Size (in characters) of the rolling text buffer the proxy is allowed to
    # hold back before flushing to the client. This is the knob that trades
    # "true zero latency" for "ability to mask/block inline". Spec section
    # 3.3 requires inline masking for MEDIUM risk and connection teardown for
    # HIGH risk — neither is possible with a byte-for-byte instant passthrough,
    # so we buffer at most one short clause at a time. See README "Design
    # Decisions" for the full reasoning.
    fast_path_buffer_chars: int = _int("FAST_PATH_BUFFER_CHARS", 80)
    fast_path_flush_on: tuple = (".", "!", "?", "\n", ",")

    # --- Dashboard / HITL ------------------------------------------------------
    dashboard_host: str = os.environ.get("DASHBOARD_HOST", "0.0.0.0")
    dashboard_port: int = _int("DASHBOARD_PORT", 8090)
    hitl_webhook_url: str = os.environ.get("HITL_WEBHOOK_URL", "")  # optional external webhook

    # --- Cost telemetry --------------------------------------------------------
    # $ per 1K tokens, rough blended defaults; override via env for real pricing.
    cost_per_1k_prompt_tokens: float = _float("COST_PER_1K_PROMPT_TOKENS", 0.003)
    cost_per_1k_completion_tokens: float = _float("COST_PER_1K_COMPLETION_TOKENS", 0.015)
    # Session budget that trips the "compute burn" rate limiter (Feature 2 /
    # Cost Telemetry Agent — "prevent excessive billing").
    session_budget_usd: float = _float("SESSION_BUDGET_USD", 0.02)
    rate_limit_tokens_per_min: int = _int("RATE_LIMIT_TOKENS_PER_MIN", 20000)

    # --- Risk thresholds (Dynamic Action Engine, spec 3.3) ----------------------
    hallucination_low_max: float = _float("HALLUCINATION_LOW_MAX", 0.30)      # similarity below -> ungrounded but minor
    hallucination_high_min: float = _float("HALLUCINATION_HIGH_MIN", 0.10)    # similarity below -> "massive" hallucination
    toxicity_medium_min: float = _float("TOXICITY_MEDIUM_MIN", 0.4)
    toxicity_high_min: float = _float("TOXICITY_HIGH_MIN", 0.75)

    demo_mode: bool = _bool("CONTROLPLANE_DEMO_MODE", True)


settings = Settings()
