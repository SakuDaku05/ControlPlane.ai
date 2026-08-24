import pytest
from controlplane.agents.cost_agent import estimate_cost_usd, PRICING_TABLE

def test_estimate_cost_usd_dynamic_model():
    # qwen/qwen3.6-27b: (0.00015, 0.00060)
    cost = estimate_cost_usd("qwen/qwen3.6-27b", 1000, 1000)
    assert cost == 0.00015 + 0.00060

def test_estimate_cost_usd_gpt4():
    # gpt-4: (0.03, 0.06)
    cost = estimate_cost_usd("gpt-4", 1000, 1000)
    assert cost == 0.03 + 0.06

def test_estimate_cost_usd_fallback():
    # Unknown model should fall back to settings
    from controlplane.config import settings
    expected_cost = settings.cost_per_1k_prompt_tokens + settings.cost_per_1k_completion_tokens
    cost = estimate_cost_usd("unknown-model", 1000, 1000)
    assert cost == expected_cost
