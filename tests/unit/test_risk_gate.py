from app.core.enums import DecisionAction
from app.domain.decision import JudgeDecision
from app.risk.risk_gate import RiskGate
from app.settings import Settings


def test_risk_gate_allows_no_trade():
    gate = RiskGate(Settings())
    decision = JudgeDecision(
        action=DecisionAction.NO_TRADE,
        confidence=0.0,
        size_multiplier=0.0,
        ttl_minutes=30,
    )
    result = gate.evaluate("BTC", decision, {"open_positions": []})
    assert result.allowed is True
    assert result.final_action == "NO_TRADE"
