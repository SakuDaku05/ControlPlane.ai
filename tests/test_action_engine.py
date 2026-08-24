import pytest
from controlplane.action_engine import RiskInputs, decide, RiskLevel, Action
from controlplane.config import settings

def test_decide_low_risk():
    inputs = RiskInputs()
    decision = decide(inputs)
    assert decision.risk_level == RiskLevel.LOW
    assert decision.action == Action.PASS

def test_decide_high_cost_over_budget():
    inputs = RiskInputs(cost_over_budget=True, cost_burn_ratio=1.1)
    decision = decide(inputs)
    assert decision.risk_level == RiskLevel.HIGH
    assert decision.action == Action.BLOCK
    assert "cost" in decision.contributing

def test_decide_medium_cost_warning():
    inputs = RiskInputs(cost_over_budget=False, cost_burn_ratio=0.8)
    decision = decide(inputs)
    assert decision.risk_level == RiskLevel.MEDIUM
    assert decision.action == Action.EDIT

def test_decide_high_toxicity():
    inputs = RiskInputs(toxicity_score=0.9, toxicity_reason="hate speech")
    decision = decide(inputs)
    assert decision.risk_level == RiskLevel.HIGH
    assert decision.action == Action.BLOCK

def test_decide_medium_toxicity():
    inputs = RiskInputs(toxicity_score=0.7)
    decision = decide(inputs)
    assert decision.risk_level == RiskLevel.MEDIUM

def test_decide_pii_medium():
    inputs = RiskInputs(pii_severity=0.5, pii_categories=["EMAIL"])
    decision = decide(inputs)
    assert decision.risk_level == RiskLevel.MEDIUM

def test_decide_pii_massive_leak():
    inputs = RiskInputs(pii_severity=0.9, pii_high_confidence_count=2, pii_categories=["SSN", "CREDIT_CARD"])
    decision = decide(inputs)
    assert decision.risk_level == RiskLevel.HIGH
    assert decision.action == Action.BLOCK

def test_decide_hallucination_high():
    inputs = RiskInputs(hallucination_similarity=0.10)
    decision = decide(inputs)
    assert decision.risk_level == RiskLevel.HIGH

def test_decide_hallucination_medium():
    inputs = RiskInputs(hallucination_similarity=0.30)
    decision = decide(inputs)
    assert decision.risk_level == RiskLevel.MEDIUM
