from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from app.domain.dossier import DecisionDossier


class DecisionDossierBuilder:
    def build(
        self,
        asset: str,
        market_state: Dict[str, Any],
        quant_expert: Dict[str, Any],
        prophet_expert: Dict[str, Any],
        news_expert: Dict[str, Any],
        position_state: Dict[str, Any],
        execution_context: Dict[str, Any],
    ) -> DecisionDossier:
        return DecisionDossier(
            timestamp=datetime.now(timezone.utc).isoformat(),
            asset=asset,
            market_state=market_state,
            quant_expert=quant_expert,
            prophet_expert=prophet_expert,
            news_expert=news_expert,
            position_state=position_state,
            execution_context=execution_context,
        )
