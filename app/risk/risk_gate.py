from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

try:
    import psycopg2
    from psycopg2.extras import Json
except Exception:  # pragma: no cover - optional DB dependency in tests/dev
    psycopg2 = None  # type: ignore[assignment]
    Json = None  # type: ignore[assignment]

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
    def __init__(
        self,
        settings: Settings,
        now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.settings = settings
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._baseline_cache: Dict[Tuple[str, str], float] = {}

    def evaluate(self, asset: str, decision: JudgeDecision, account_state: Dict[str, object]) -> RiskGateResult:
        if decision.action in {DecisionAction.NO_TRADE, DecisionAction.HOLD}:
            return RiskGateResult(True, decision.action.value, 0.0, "no_trade_or_hold")

        if self._stop_limit_breached(account_state):
            if self._is_exit_action(decision.action):
                return RiskGateResult(
                    True,
                    decision.action.value,
                    self._exit_size_multiplier(decision),
                    "stop_limit_exit_allowed",
                )
            return RiskGateResult(False, "NO_TRADE", 0.0, "daily_or_weekly_stop_reached")

        if self._is_entry_action(decision.action):
            open_positions = account_state.get("open_positions", [])
            if isinstance(open_positions, list) and len(open_positions) >= self.settings.max_open_positions:
                return RiskGateResult(False, "NO_TRADE", 0.0, "max_open_positions_reached")

        if self._is_exit_action(decision.action):
            return RiskGateResult(
                True,
                decision.action.value,
                self._exit_size_multiplier(decision),
                self._exit_reason(),
            )

        base_capped_size = max(0.0, min(float(decision.size_multiplier), 1.0))

        if self.settings.dry_run:
            return RiskGateResult(True, decision.action.value, base_capped_size, "dry_run_allowed")

        live_cap = float(getattr(self.settings, "live_initial_size_multiplier_cap", 0.10))
        live_capped_size = min(base_capped_size, live_cap)

        if self.settings.shadow_mode:
            return RiskGateResult(True, decision.action.value, live_capped_size, "shadow_mode_allowed")

        return RiskGateResult(True, decision.action.value, live_capped_size, "live_allowed")

    def _stop_limit_breached(self, account_state: Dict[str, object]) -> bool:
        equity = self._safe_float(account_state.get("equity"))
        if equity <= 0.0:
            return False

        now_utc = self._normalize_now(self._now_fn())
        day_key = now_utc.strftime("%Y-%m-%d")
        iso_year, iso_week, _ = now_utc.isocalendar()
        week_key = f"{iso_year}-W{iso_week:02d}"

        daily_baseline = self._get_or_create_baseline("day", day_key, equity, now_utc)
        if self._drawdown_pct(daily_baseline, equity) >= max(0.0, float(self.settings.daily_stop_pct)):
            return True

        weekly_baseline = self._get_or_create_baseline("week", week_key, equity, now_utc)
        if self._drawdown_pct(weekly_baseline, equity) >= max(0.0, float(self.settings.weekly_stop_pct)):
            return True

        return False

    def _get_or_create_baseline(
        self,
        scope: str,
        period_key: str,
        current_equity: float,
        now_utc: datetime,
    ) -> float:
        cache_key = (scope, period_key)
        cached = self._baseline_cache.get(cache_key)
        if cached is not None and cached > 0.0:
            return cached

        baseline = self._load_baseline_from_db(scope, period_key)
        if baseline is None or baseline <= 0.0:
            baseline = current_equity
            self._persist_baseline_to_db(scope, period_key, baseline, now_utc)

        self._baseline_cache[cache_key] = baseline
        return baseline

    def _load_baseline_from_db(self, scope: str, period_key: str) -> Optional[float]:
        if not self.settings.database_url or psycopg2 is None:
            return None

        try:
            with psycopg2.connect(self.settings.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT payload->>'baseline_equity'
                        FROM journal_events
                        WHERE event_type = 'risk_baseline'
                          AND COALESCE(payload->>'scope', '') = %s
                          AND COALESCE(payload->>'period_key', '') = %s
                        ORDER BY created_at DESC
                        LIMIT 1
                        """,
                        (scope, period_key),
                    )
                    row = cur.fetchone()
        except Exception:
            return None

        if not row:
            return None

        return self._safe_float(row[0])

    def _persist_baseline_to_db(self, scope: str, period_key: str, baseline_equity: float, now_utc: datetime) -> None:
        if not self.settings.database_url or psycopg2 is None or Json is None:
            return

        payload = {
            "scope": scope,
            "period_key": period_key,
            "baseline_equity": baseline_equity,
            "created_at_utc": now_utc.isoformat(),
        }

        try:
            with psycopg2.connect(self.settings.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO journal_events (event_type, asset, payload)
                        VALUES (%s, %s, %s)
                        """,
                        ("risk_baseline", None, Json(payload)),
                    )
        except Exception:
            return

    def _exit_reason(self) -> str:
        if self.settings.dry_run:
            return "dry_run_exit_allowed"
        if self.settings.shadow_mode:
            return "shadow_mode_exit_allowed"
        return "live_exit_allowed"

    @staticmethod
    def _is_entry_action(action: DecisionAction) -> bool:
        return action in {DecisionAction.ENTER_LONG, DecisionAction.ENTER_SHORT}

    @staticmethod
    def _is_exit_action(action: DecisionAction) -> bool:
        return action in {DecisionAction.REDUCE, DecisionAction.CLOSE}

    @staticmethod
    def _exit_size_multiplier(decision: JudgeDecision) -> float:
        if decision.action == DecisionAction.CLOSE:
            return 1.0
        return max(0.0, min(float(decision.size_multiplier), 1.0))

    @staticmethod
    def _drawdown_pct(baseline_equity: float, current_equity: float) -> float:
        if baseline_equity <= 0.0:
            return 0.0
        loss = max(0.0, baseline_equity - current_equity)
        return (loss / baseline_equity) * 100.0

    @staticmethod
    def _normalize_now(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float(default)
