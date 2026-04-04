from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import psycopg2
    from psycopg2.extras import Json
except Exception:  # pragma: no cover - optional DB dependency in tests/dev
    psycopg2 = None  # type: ignore[assignment]
    Json = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clean_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = getattr(value, "value", value)
    text = str(text).strip()
    return text or None


def _parse_observed_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    text = str(value or "").strip()
    if not text:
        return datetime.now(timezone.utc)

    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.now(timezone.utc)

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class MarketObserverService:
    def __init__(
        self,
        path: str = "/tmp/market_observations.jsonl",
        database_url: Optional[str] = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.database_url = (database_url or os.getenv("DATABASE_URL", "")).strip()

    def record(
        self,
        *,
        asset: str,
        market: Dict[str, Any],
        dossier: Dict[str, Any],
        decision: Dict[str, Any],
        risk_gate: Dict[str, Any],
        execution_result: Optional[Dict[str, Any]] = None,
        loop_count: Optional[int] = None,
    ) -> Optional[int]:
        observation = self._build_observation(
            asset=asset,
            market=market,
            dossier=dossier,
            decision=decision,
            risk_gate=risk_gate,
            execution_result=execution_result or {},
            loop_count=loop_count,
        )
        self._append_to_file(observation)
        return self._append_to_postgres(observation)

    def _build_observation(
        self,
        *,
        asset: str,
        market: Dict[str, Any],
        dossier: Dict[str, Any],
        decision: Dict[str, Any],
        risk_gate: Dict[str, Any],
        execution_result: Dict[str, Any],
        loop_count: Optional[int],
    ) -> Dict[str, Any]:
        quant = dossier.get("quant_expert", {}) if isinstance(dossier.get("quant_expert"), dict) else {}
        prophet = dossier.get("prophet_expert", {}) if isinstance(dossier.get("prophet_expert"), dict) else {}
        news = dossier.get("news_expert", {}) if isinstance(dossier.get("news_expert"), dict) else {}
        position_state = dossier.get("position_state", {}) if isinstance(dossier.get("position_state"), dict) else {}
        execution_context = dossier.get("execution_context", {}) if isinstance(dossier.get("execution_context"), dict) else {}
        observed_at = _parse_observed_at(dossier.get("timestamp"))

        fee_bps = _safe_float(execution_context.get("fee_estimate_bps"))
        slippage_bps = _safe_float(execution_context.get("slippage_estimate_bps"))
        cost_estimate_bps = fee_bps + slippage_bps

        payload = {
            "loop_count": loop_count,
            "market_state": market,
            "dossier": dossier,
            "decision": decision,
            "risk_gate": risk_gate,
            "execution_result": execution_result,
        }

        return {
            "observed_at": observed_at.isoformat(),
            "asset": str(asset).upper(),
            "mark_price": _safe_float(market.get("mark_price")),
            "spread_bps": _safe_float(market.get("spread_bps")),
            "funding_rate": _safe_float(market.get("funding_rate")),
            "open_interest_delta_1h": _safe_float(market.get("open_interest_delta_1h")),
            "regime_hint": _clean_text(market.get("regime_hint")),
            "position_side": _clean_text(position_state.get("side")) or "flat",
            "decision_action": _clean_text(decision.get("action")),
            "risk_gate_final_action": _clean_text(risk_gate.get("final_action")),
            "setup_score": _safe_float(quant.get("setup_score")),
            "signal_strength": _safe_float(quant.get("signal_strength")),
            "p_up": _safe_float(quant.get("p_up")),
            "p_down": _safe_float(quant.get("p_down")),
            "expected_move_60m": _safe_float(quant.get("expected_move_60m")),
            "invalidation_price": _safe_float(quant.get("invalidation_price")),
            "prophet_trend_bias": _clean_text(prophet.get("trend_bias")),
            "forecast_delta_4h": _safe_float(prophet.get("forecast_delta_4h")),
            "news_impact": _clean_text(news.get("impact")),
            "news_direction": _clean_text(news.get("direction")),
            "tradability_flag": _clean_text(news.get("tradability_flag")),
            "cost_estimate_bps": cost_estimate_bps,
            "payload": payload,
        }

    def _append_to_file(self, observation: Dict[str, Any]) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(observation, ensure_ascii=False) + "\n")
        except Exception:
            logger.exception("Market observer file write failed")

    def _append_to_postgres(self, observation: Dict[str, Any]) -> Optional[int]:
        if not self.database_url or psycopg2 is None or Json is None or not callable(getattr(psycopg2, "connect", None)):
            return None

        try:
            with psycopg2.connect(self.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO market_observations (
                            observed_at,
                            asset,
                            mark_price,
                            spread_bps,
                            funding_rate,
                            open_interest_delta_1h,
                            regime_hint,
                            position_side,
                            decision_action,
                            risk_gate_final_action,
                            setup_score,
                            signal_strength,
                            p_up,
                            p_down,
                            expected_move_60m,
                            invalidation_price,
                            prophet_trend_bias,
                            forecast_delta_4h,
                            news_impact,
                            news_direction,
                            tradability_flag,
                            cost_estimate_bps,
                            payload
                        )
                        VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        RETURNING id
                        """,
                        (
                            _parse_observed_at(observation.get("observed_at")),
                            observation.get("asset"),
                            observation.get("mark_price"),
                            observation.get("spread_bps"),
                            observation.get("funding_rate"),
                            observation.get("open_interest_delta_1h"),
                            observation.get("regime_hint"),
                            observation.get("position_side"),
                            observation.get("decision_action"),
                            observation.get("risk_gate_final_action"),
                            observation.get("setup_score"),
                            observation.get("signal_strength"),
                            observation.get("p_up"),
                            observation.get("p_down"),
                            observation.get("expected_move_60m"),
                            observation.get("invalidation_price"),
                            observation.get("prophet_trend_bias"),
                            observation.get("forecast_delta_4h"),
                            observation.get("news_impact"),
                            observation.get("news_direction"),
                            observation.get("tradability_flag"),
                            observation.get("cost_estimate_bps"),
                            Json(observation.get("payload", {})),
                        ),
                    )
                    row = cur.fetchone()
        except Exception:
            logger.exception("Market observer PostgreSQL write failed")
            return None

        if not row:
            return None
        try:
            return int(row[0])
        except (TypeError, ValueError):
            return None
