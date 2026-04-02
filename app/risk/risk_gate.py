from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from app.core.enums import DecisionAction
from app.domain.decision import JudgeDecision
from app.settings import Settings


@dataclass(slots=True)
class RiskGateResult:
    allowed: bool
    final_action: str
    final_size_multiplier: float
    reason: str


class RiskGate:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def evaluate(self, asset: str, decision: JudgeDecision, account_state: Dict[str, object]) -> RiskGateResult:
        if decision.action in {DecisionAction.NO_TRADE, DecisionAction.HOLD}:
            return RiskGateResult(True, decision.action.value, 0.0, "no_trade_or_hold")

        open_positions = account_state.get("open_positions", [])
        if isinstance(open_positions, list) and len(open_positions) >= self.settings.max_open_positions:
            return RiskGateResult(False, "NO_TRADE", 0.0, "max_open_positions_reached")

        base_capped_size = max(0.0, min(float(decision.size_multiplier), 1.0))

        if self.settings.dry_run:
            return RiskGateResult(True, decision.action.value, base_capped_size, "dry_run_allowed")

        live_cap = float(getattr(self.settings, "live_initial_size_multiplier_cap", 0.10))
        live_capped_size = min(base_capped_size, live_cap)

        if self.settings.shadow_mode:
            return RiskGateResult(True, decision.action.value, live_capped_size, "shadow_mode_allowed")

        return RiskGateResult(True, decision.action.value, live_capped_size, "live_allowed")
