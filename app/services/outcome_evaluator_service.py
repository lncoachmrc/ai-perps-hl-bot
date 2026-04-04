from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

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


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    value = getattr(value, "value", value)
    return str(value).strip()


def _return_pct(reference_price: float, current_price: float) -> float:
    if reference_price <= 0:
        return 0.0
    return ((current_price / reference_price) - 1.0) * 100.0


def _neutral_band_pct(cost_estimate_bps: float) -> float:
    return max((max(cost_estimate_bps, 0.0) / 100.0) * 1.5, 0.10)


def _effective_action(decision_action: str, risk_gate_final_action: str, position_side: str) -> str:
    action = (_clean_text(risk_gate_final_action) or _clean_text(decision_action) or "NO_TRADE").upper()
    side = _clean_text(position_side).lower()

    if action == "NO_TRADE" and side in {"long", "short"}:
        return "HOLD"
    return action


def classify_outcome(
    *,
    effective_action: str,
    position_side: str,
    future_return_pct: float,
    neutral_band_pct: float,
) -> Tuple[str, float]:
    action = _clean_text(effective_action).upper()
    side = _clean_text(position_side).lower()
    long_edge = future_return_pct
    short_edge = -future_return_pct

    if action == "ENTER_LONG":
        if long_edge > neutral_band_pct:
            return "correct_long_entry", 1.0
        if long_edge < -neutral_band_pct:
            return "bad_long_entry", -1.0
        return "neutral_long_entry", 0.0

    if action == "ENTER_SHORT":
        if short_edge > neutral_band_pct:
            return "correct_short_entry", 1.0
        if short_edge < -neutral_band_pct:
            return "bad_short_entry", -1.0
        return "neutral_short_entry", 0.0

    if action == "NO_TRADE":
        if abs(future_return_pct) <= neutral_band_pct:
            return "correct_no_trade", 0.5
        if future_return_pct > neutral_band_pct:
            return "missed_long_opportunity", -0.5
        return "missed_short_opportunity", -0.5

    if action == "HOLD":
        if side == "long":
            if long_edge > neutral_band_pct:
                return "hold_was_right_long", 0.5
            if long_edge < -neutral_band_pct:
                return "hold_was_wrong_long", -0.75
            return "hold_neutral_long", 0.0

        if side == "short":
            if short_edge > neutral_band_pct:
                return "hold_was_right_short", 0.5
            if short_edge < -neutral_band_pct:
                return "hold_was_wrong_short", -0.75
            return "hold_neutral_short", 0.0

        return "hold_without_position", 0.0

    if action in {"CLOSE", "REDUCE"}:
        prefix = action.lower()

        if side == "long":
            if future_return_pct < -neutral_band_pct:
                return f"good_{prefix}_long", 0.75
            if future_return_pct > neutral_band_pct:
                return f"premature_{prefix}_long", -0.5
            return f"neutral_{prefix}_long", 0.0

        if side == "short":
            if future_return_pct > neutral_band_pct:
                return f"good_{prefix}_short", 0.75
            if future_return_pct < -neutral_band_pct:
                return f"premature_{prefix}_short", -0.5
            return f"neutral_{prefix}_short", 0.0

        return f"{prefix}_without_position", 0.0

    return "unclassified_outcome", 0.0


class OutcomeEvaluatorService:
    def __init__(
        self,
        path: str = "/tmp/decision_outcomes.jsonl",
        database_url: Optional[str] = None,
        horizons_minutes: Sequence[int] = (60,),
        batch_size: int = 100,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.database_url = (database_url or os.getenv("DATABASE_URL", "")).strip()
        self.horizons_minutes = tuple(int(value) for value in horizons_minutes if int(value) > 0) or (60,)
        self.batch_size = max(1, int(batch_size))

    def evaluate_due_outcomes(self) -> int:
        if not self.database_url or psycopg2 is None or Json is None or not callable(getattr(psycopg2, "connect", None)):
            return 0

        created = 0
        try:
            with psycopg2.connect(self.database_url) as conn:
                with conn.cursor() as cur:
                    for horizon in self.horizons_minutes:
                        for observation in self._fetch_due_observations(cur=cur, horizon_minutes=horizon):
                            future_observation = self._find_future_observation(
                                cur=cur,
                                asset=observation["asset"],
                                observed_at=observation["observed_at"],
                                horizon_minutes=horizon,
                            )
                            if future_observation is None:
                                continue

                            path_prices = self._fetch_path_prices(
                                cur=cur,
                                asset=observation["asset"],
                                start_observed_at=observation["observed_at"],
                                end_observed_at=future_observation["observed_at"],
                            )
                            outcome = self._build_outcome(
                                observation=observation,
                                future_observation=future_observation,
                                path_prices=path_prices,
                                horizon_minutes=horizon,
                            )
                            inserted = self._insert_outcome(cur=cur, outcome=outcome)
                            if inserted:
                                created += 1
                                self._append_to_file(outcome)
        except Exception:
            logger.exception("Outcome evaluator PostgreSQL run failed")
            return created

        return created

    def _fetch_due_observations(self, *, cur: Any, horizon_minutes: int) -> List[Dict[str, Any]]:
        cur.execute(
            """
            SELECT
                mo.id,
                mo.observed_at,
                mo.asset,
                mo.mark_price,
                mo.position_side,
                mo.decision_action,
                mo.risk_gate_final_action,
                mo.cost_estimate_bps
            FROM market_observations mo
            LEFT JOIN decision_outcomes dout
              ON dout.observation_id = mo.id
             AND dout.horizon_minutes = %s
            WHERE dout.id IS NULL
              AND mo.observed_at <= NOW() - (%s * INTERVAL '1 minute')
            ORDER BY mo.observed_at ASC
            LIMIT %s
            """,
            (horizon_minutes, horizon_minutes, self.batch_size),
        )
        rows = cur.fetchall() or []
        return [
            {
                "id": row[0],
                "observed_at": self._normalize_dt(row[1]),
                "asset": _clean_text(row[2]).upper(),
                "mark_price": _safe_float(row[3]),
                "position_side": _clean_text(row[4]).lower() or "flat",
                "decision_action": _clean_text(row[5]).upper() or "NO_TRADE",
                "risk_gate_final_action": _clean_text(row[6]).upper(),
                "cost_estimate_bps": _safe_float(row[7]),
            }
            for row in rows
        ]

    def _find_future_observation(
        self,
        *,
        cur: Any,
        asset: str,
        observed_at: datetime,
        horizon_minutes: int,
    ) -> Optional[Dict[str, Any]]:
        target_observed_at = observed_at + timedelta(minutes=horizon_minutes)
        cur.execute(
            """
            SELECT id, observed_at, mark_price
            FROM market_observations
            WHERE asset = %s
              AND observed_at >= %s
            ORDER BY observed_at ASC
            LIMIT 1
            """,
            (asset, target_observed_at),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "observed_at": self._normalize_dt(row[1]),
            "mark_price": _safe_float(row[2]),
            "target_observed_at": target_observed_at,
        }

    def _fetch_path_prices(
        self,
        *,
        cur: Any,
        asset: str,
        start_observed_at: datetime,
        end_observed_at: datetime,
    ) -> List[float]:
        cur.execute(
            """
            SELECT mark_price
            FROM market_observations
            WHERE asset = %s
              AND observed_at > %s
              AND observed_at <= %s
            ORDER BY observed_at ASC
            """,
            (asset, start_observed_at, end_observed_at),
        )
        rows = cur.fetchall() or []
        prices = [_safe_float(row[0]) for row in rows if _safe_float(row[0]) > 0]
        return prices

    def _build_outcome(
        self,
        *,
        observation: Dict[str, Any],
        future_observation: Dict[str, Any],
        path_prices: Iterable[float],
        horizon_minutes: int,
    ) -> Dict[str, Any]:
        reference_price = max(_safe_float(observation.get("mark_price")), 0.0)
        future_price = max(_safe_float(future_observation.get("mark_price")), 0.0)
        if future_price <= 0.0:
            future_price = reference_price

        future_return_pct = _return_pct(reference_price, future_price)

        prices = [price for price in path_prices if price > 0]
        if future_price > 0:
            prices.append(future_price)
        if not prices and reference_price > 0:
            prices = [reference_price]

        path_returns = [_return_pct(reference_price, price) for price in prices] if reference_price > 0 else [0.0]
        mfe_pct = max(path_returns) if path_returns else 0.0
        mae_pct = min(path_returns) if path_returns else 0.0

        neutral_band_pct = _neutral_band_pct(_safe_float(observation.get("cost_estimate_bps")))
        effective_action = _effective_action(
            decision_action=_clean_text(observation.get("decision_action")),
            risk_gate_final_action=_clean_text(observation.get("risk_gate_final_action")),
            position_side=_clean_text(observation.get("position_side")),
        )
        outcome_label, outcome_score = classify_outcome(
            effective_action=effective_action,
            position_side=_clean_text(observation.get("position_side")),
            future_return_pct=future_return_pct,
            neutral_band_pct=neutral_band_pct,
        )

        payload = {
            "decision_action": observation.get("decision_action"),
            "risk_gate_final_action": observation.get("risk_gate_final_action"),
            "position_side": observation.get("position_side"),
            "bars_observed": len(path_returns),
        }

        return {
            "observation_id": observation["id"],
            "asset": observation["asset"],
            "effective_action": effective_action,
            "position_side": observation["position_side"],
            "horizon_minutes": horizon_minutes,
            "reference_observed_at": observation["observed_at"].isoformat(),
            "target_observed_at": future_observation["target_observed_at"].isoformat(),
            "future_observed_at": future_observation["observed_at"].isoformat(),
            "reference_price": reference_price,
            "future_price": future_price,
            "future_return_pct": future_return_pct,
            "mfe_pct": mfe_pct,
            "mae_pct": mae_pct,
            "bars_observed": len(path_returns),
            "neutral_band_pct": neutral_band_pct,
            "outcome_label": outcome_label,
            "outcome_score": outcome_score,
            "payload": payload,
        }

    def _insert_outcome(self, *, cur: Any, outcome: Dict[str, Any]) -> bool:
        cur.execute(
            """
            INSERT INTO decision_outcomes (
                observation_id,
                asset,
                effective_action,
                position_side,
                horizon_minutes,
                reference_observed_at,
                target_observed_at,
                future_observed_at,
                reference_price,
                future_price,
                future_return_pct,
                mfe_pct,
                mae_pct,
                bars_observed,
                neutral_band_pct,
                outcome_label,
                outcome_score,
                payload
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (observation_id, horizon_minutes) DO NOTHING
            RETURNING id
            """,
            (
                outcome.get("observation_id"),
                outcome.get("asset"),
                outcome.get("effective_action"),
                outcome.get("position_side"),
                outcome.get("horizon_minutes"),
                self._normalize_dt(outcome.get("reference_observed_at")),
                self._normalize_dt(outcome.get("target_observed_at")),
                self._normalize_dt(outcome.get("future_observed_at")),
                outcome.get("reference_price"),
                outcome.get("future_price"),
                outcome.get("future_return_pct"),
                outcome.get("mfe_pct"),
                outcome.get("mae_pct"),
                outcome.get("bars_observed"),
                outcome.get("neutral_band_pct"),
                outcome.get("outcome_label"),
                outcome.get("outcome_score"),
                Json(outcome.get("payload", {})),
            ),
        )
        return cur.fetchone() is not None

    def _append_to_file(self, outcome: Dict[str, Any]) -> None:
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(outcome, ensure_ascii=False) + "\n")
        except Exception:
            logger.exception("Outcome evaluator file write failed")

    @staticmethod
    def _normalize_dt(value: Any) -> datetime:
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
