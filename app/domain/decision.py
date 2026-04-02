from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from app.core.enums import DecisionAction


@dataclass(slots=True)
class JudgeDecision:
    action: DecisionAction
    confidence: float
    size_multiplier: float
    ttl_minutes: int
    reasons: List[str] = field(default_factory=list)
    stop_logic: str = "use_quant_invalidation"
    take_profit_logic: str = "1.5R_or_trailing"
