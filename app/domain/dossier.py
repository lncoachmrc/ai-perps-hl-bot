from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict


@dataclass(slots=True)
class DecisionDossier:
    timestamp: str
    asset: str
    market_state: Dict[str, Any]
    quant_expert: Dict[str, Any]
    prophet_expert: Dict[str, Any]
    news_expert: Dict[str, Any]
    position_state: Dict[str, Any]
    execution_context: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
