from __future__ import annotations

import logging
import time
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Dict

from app.experts.dossier.builder import DecisionDossierBuilder
from app.experts.news.news_expert import NewsExpert
from app.experts.prophet.prophet_expert import ProphetExpert
from app.experts.quant.quant_expert import QuantExpert
from app.exchange.hyperliquid.client import HyperliquidClient
from app.llm.judge import JudgeLLM
from app.risk.risk_gate import RiskGate
from app.services.journal_service import JournalService
from app.settings import Settings

logger = logging.getLogger(__name__)


MIN_SETUP_SCORE_FOR_JUDGE = 0.40
MIN_SIGNAL_STRENGTH_FOR_JUDGE = 0.20


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _build_prefilter_no_trade_decision(*, asset: str, quant_view: Dict[str, Any]) -> "JudgeDecision":
    from app.core.enums import DecisionAction
    from app.domain.decision import JudgeDecision

    setup_score = _safe_float(quant_view.get("setup_score"))
    signal_strength = _safe_float(quant_view.get("signal_strength"))

    return JudgeDecision(
        action=DecisionAction.NO_TRADE,
        confidence=0.95,
        size_multiplier=0.0,
        ttl_minutes=20,
        reasons=[
            (
                f"Pre-judge deterministic filter blocked {asset}: "
                f"setup_score {setup_score:.4f} < {MIN_SETUP_SCORE_FOR_JUDGE:.2f}."
            )
            if setup_score < MIN_SETUP_SCORE_FOR_JUDGE
            else (
                f"Pre-judge deterministic filter passed setup_score {setup_score:.4f}, "
                f"but signal_strength {signal_strength:.4f} < {MIN_SIGNAL_STRENGTH_FOR_JUDGE:.2f}."
            ),
            "Judge LLM was skipped because the quant floor was not met.",
            "This filter applies before final judgment to remove repetitive threshold work from the prompt.",
        ],
        stop_logic=(
            "No position entered; deterministic pre-judge quant filter requires "
            f"setup_score >= {MIN_SETUP_SCORE_FOR_JUDGE:.2f} and "
            f"signal_strength >= {MIN_SIGNAL_STRENGTH_FOR_JUDGE:.2f} before judge evaluation."
        ),
        take_profit_logic=(
            "No position entered; take-profit planning is skipped until the deterministic "
            "pre-judge quant filter is satisfied."
        ),
    )


def _passes_pre_judge_quant_filter(*, dossier: Dict[str, Any]) -> bool:
    position_side = str(dossier.get("position_state", {}).get("side", "flat")).lower()
    if position_side not in {"flat", "", "none"}:
        return True

    quant_view = dossier.get("quant_expert", {})
    setup_score = _safe_float(quant_view.get("setup_score"))
    signal_strength = _safe_float(quant_view.get("signal_strength"))

    return (
        setup_score >= MIN_SETUP_SCORE_FOR_JUDGE
        and signal_strength >= MIN_SIGNAL_STRENGTH_FOR_JUDGE
    )


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _fmt_num(value: Any, digits: int = 2, fallback: str = "n/a") -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return fallback


def _fmt_bool(value: Any) -> str:
    return "yes" if bool(value) else "no"


def _fmt_list(values: Any, fallback: str = "none") -> str:
    if not values:
        return fallback
    if isinstance(values, (list, tuple, set)):
        return ", ".join(str(item) for item in values)
    return str(values)


def _decision_value(action: Any) -> str:
    if isinstance(action, Enum):
        return str(action.value)
    return str(action)


def _decision_emoji(action: str) -> str:
    mapping = {
        "NO_TRADE": "⏸️",
        "HOLD": "🟡",
        "ENTER_LONG": "🟢",
        "ENTER_SHORT": "🔴",
        "REDUCE": "📉",
        "CLOSE": "🚪",
    }
    return mapping.get(action, "🤖")


def _mode_label(settings: Settings) -> str:
    if settings.dry_run:
        return "dry-run"
    if settings.shadow_mode:
        return "shadow"
    return "live"


def _build_position_state(*, asset: str, account_state: Dict[str, Any]) -> Dict[str, Any]:
    normalized_asset = str(asset).upper()
    flat_state = {
        "asset": normalized_asset,
        "side": "flat",
        "size": 0.0,
        "size_signed": 0.0,
        "entry_price": 0.0,
        "mark_price": 0.0,
        "pnl_usd": 0.0,
        "leverage": 0.0,
    }

    open_positions = account_state.get("open_positions", [])
    if not isinstance(open_positions, list):
        return flat_state

    for position in open_positions:
        if not isinstance(position, dict):
            continue

        position_asset = str(position.get("asset", "")).upper()
        if position_asset != normalized_asset:
            continue

        size_signed = _safe_float(position.get("size_signed"))
        size = _safe_float(position.get("size"))
        side = str(position.get("side", "")).lower()

        if side not in {"long", "short"}:
            if size_signed > 0:
                side = "long"
            elif size_signed < 0:
                side = "short"
            else:
                side = "flat"

        if size <= 0.0 and size_signed != 0.0:
            size = abs(size_signed)

        return {
            "asset": normalized_asset,
            "side": side,
            "size": size,
            "size_signed": size_signed,
            "entry_price": _safe_float(position.get("entry_price")),
            "mark_price": _safe_float(position.get("mark_price")),
            "pnl_usd": _safe_float(position.get("pnl_usd")),
            "leverage": _safe_float(position.get("leverage")),
        }

    return flat_state


class Orchestrator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.exchange = HyperliquidClient(
            dry_run=settings.dry_run,
            shadow_mode=settings.shadow_mode,
        )
        self.quant = QuantExpert()
        self.prophet = ProphetExpert()
        self.news = NewsExpert()
        self.builder = DecisionDossierBuilder()
        self.judge = JudgeLLM(enabled=bool(settings.openai_api_key), model=settings.openai_model)
        self.risk_gate = RiskGate(settings)
        self.journal = JournalService()
        self._last_status = {"status": "booting", "loop_count": 0}

    def status(self) -> Dict[str, object]:
        return self._last_status

    def run_forever(self) -> None:
        logger.info(
            "🚀 Orchestrator started | env=%s | mode=%s | dry_run=%s | shadow_mode=%s | "
            "symbols=%s | loop_interval=%ss | llm_enabled=%s | llm_model=%s",
            self.settings.app_env,
            _mode_label(self.settings),
            self.settings.dry_run,
            self.settings.shadow_mode,
            ",".join(self.settings.universe_symbols),
            self.settings.loop_interval_seconds,
            _fmt_bool(bool(self.settings.openai_api_key)),
            self.settings.openai_model,
        )
        self._last_status["status"] = "running"
        while True:
            self.run_once()
            time.sleep(self.settings.loop_interval_seconds)

    def run_once(self) -> None:
        next_loop = int(self._last_status.get("loop_count", 0)) + 1
        logger.info("🔁 Starting decision loop #%s", next_loop)

        account_state = self.exchange.get_account_state()
        open_positions = account_state.get("open_positions", [])
        open_positions_count = len(open_positions) if isinstance(open_positions, list) else 0
        logger.info(
            "💼 Account snapshot | equity=%s | available_margin=%s | open_positions=%s",
            _fmt_num(account_state.get("equity")),
            _fmt_num(account_state.get("available_margin")),
            open_positions_count,
        )

        for asset in self.settings.universe_symbols:
            logger.info("🪙 %s | Starting asset review", asset)

            market = self.exchange.get_market_snapshot(asset)
            logger.info(
                "📈 %s | Market snapshot | mark_price=%s | spread_bps=%s | funding_rate=%s | "
                "open_interest_delta_1h=%s | regime_hint=%s",
                asset,
                _fmt_num(market.get("mark_price")),
                _fmt_num(market.get("spread_bps")),
                _fmt_num(market.get("funding_rate"), digits=4),
                _fmt_num(market.get("open_interest_delta_1h"), digits=4),
                market.get("regime_hint", "unknown"),
            )

            quant_view = self.quant.evaluate(market)
            logger.info(
                "🧠 %s | Quant expert | regime=%s | setup_score=%s | p_up=%s | p_down=%s | "
                "expected_move_60m=%s | invalidation_price=%s",
                asset,
                quant_view.get("regime", "unknown"),
                _fmt_num(quant_view.get("setup_score")),
                _fmt_num(quant_view.get("p_up")),
                _fmt_num(quant_view.get("p_down")),
                _fmt_num(quant_view.get("expected_move_60m")),
                _fmt_num(quant_view.get("invalidation_price")),
            )

            prophet_view = self.prophet.evaluate(market)
            logger.info(
                "🔮 %s | Prophet expert | trend_bias=%s | forecast_delta_4h=%s | "
                "interval_width=%s | changepoint_stress=%s",
                asset,
                prophet_view.get("trend_bias", "unknown"),
                _fmt_num(prophet_view.get("forecast_delta_4h")),
                prophet_view.get("interval_width", "unknown"),
                prophet_view.get("changepoint_stress", "unknown"),
            )

            news_view = self.news.evaluate(asset)
            logger.info(
                "📰 %s | News expert | impact=%s | direction=%s | headline_conflict=%s | "
                "tradability_flag=%s | freshness_minutes=%s",
                asset,
                news_view.get("impact", "unknown"),
                news_view.get("direction", "unknown"),
                news_view.get("headline_conflict", False),
                news_view.get("tradability_flag", "unknown"),
                news_view.get("freshness_minutes", "n/a"),
            )

            position_state = _build_position_state(asset=asset, account_state=account_state)

            dossier = self.builder.build(
                asset=asset,
                market_state=market,
                quant_expert=quant_view,
                prophet_expert=prophet_view,
                news_expert=news_view,
                position_state=position_state,
                execution_context={
                    "preferred_order_type": "IOC",
                    "slippage_estimate_bps": 2.0,
                    "fee_estimate_bps": 4.0,
                },
            )
            logger.info(
                "🧾 %s | Dossier ready | timestamp=%s | position_side=%s | order_type=%s | "
                "slippage_bps=%s | fee_bps=%s",
                asset,
                dossier.timestamp,
                dossier.position_state.get("side", "unknown"),
                dossier.execution_context.get("preferred_order_type", "unknown"),
                _fmt_num(dossier.execution_context.get("slippage_estimate_bps")),
                _fmt_num(dossier.execution_context.get("fee_estimate_bps")),
            )

            dossier_dict = dossier.to_dict()
            if _passes_pre_judge_quant_filter(dossier=dossier_dict):
                decision = self.judge.decide(dossier_dict)
            else:
                decision = _build_prefilter_no_trade_decision(asset=asset, quant_view=quant_view)
                logger.info(
                    "🚫 %s | Pre-judge quant filter blocked judge call | min_setup_score=%s | "
                    "min_signal_strength=%s | setup_score=%s | signal_strength=%s",
                    asset,
                    _fmt_num(MIN_SETUP_SCORE_FOR_JUDGE),
                    _fmt_num(MIN_SIGNAL_STRENGTH_FOR_JUDGE),
                    _fmt_num(quant_view.get("setup_score")),
                    _fmt_num(quant_view.get("signal_strength")),
                )

            decision_action = _decision_value(decision.action)
            logger.info(
                "%s %s | Judge decision | action=%s | confidence=%s | size_multiplier=%s | "
                "ttl_minutes=%s | stop_logic=%s | take_profit_logic=%s | reasons=%s",
                _decision_emoji(decision_action),
                asset,
                decision_action,
                _fmt_num(decision.confidence),
                _fmt_num(decision.size_multiplier),
                decision.ttl_minutes,
                decision.stop_logic,
                decision.take_profit_logic,
                _fmt_list(decision.reasons),
            )

            gate = self.risk_gate.evaluate(asset, decision, account_state)
            logger.info(
                "🛡️ %s | Risk gate verdict | allowed=%s | final_action=%s | final_size=%s | reason=%s",
                asset,
                gate.allowed,
                gate.final_action,
                _fmt_num(gate.final_size_multiplier),
                gate.reason,
            )

            if gate.allowed and gate.final_action not in {"NO_TRADE", "HOLD"}:
                logger.info(
                    "📤 %s | Sending order to execution layer | action=%s | size_multiplier=%s | mode=%s",
                    asset,
                    gate.final_action,
                    _fmt_num(gate.final_size_multiplier),
                    _mode_label(self.settings),
                )
                execution_result = self.exchange.place_order(
                    {
                        "asset": asset,
                        "action": gate.final_action,
                        "size_multiplier": gate.final_size_multiplier,
                        "dry_run": self.settings.dry_run,
                    }
                )
                logger.info(
                    "✅ %s | Execution result | accepted=%s | dry_run=%s",
                    asset,
                    execution_result.get("accepted", False),
                    execution_result.get("dry_run", self.settings.dry_run),
                )
                if execution_result.get("accepted", False):
                    account_state = self.exchange.get_account_state()
                    refreshed_open_positions = account_state.get("open_positions", [])
                    refreshed_count = (
                        len(refreshed_open_positions)
                        if isinstance(refreshed_open_positions, list)
                        else 0
                    )
                    logger.info(
                        "🔄 %s | Account snapshot refreshed after execution | equity=%s | available_margin=%s | open_positions=%s",
                        asset,
                        _fmt_num(account_state.get("equity")),
                        _fmt_num(account_state.get("available_margin")),
                        refreshed_count,
                    )
            else:
                logger.info(
                    "⏭️ %s | No order sent | allowed=%s | final_action=%s | reason=%s",
                    asset,
                    gate.allowed,
                    gate.final_action,
                    gate.reason,
                )

            self.journal.append(
                {
                    "asset": asset,
                    "dossier": dossier.to_dict(),
                    "decision": _jsonable(decision),
                    "risk_gate": _jsonable(gate),
                }
            )
            logger.info("📝 %s | Journal entry written", asset)

        self._last_status["loop_count"] = next_loop
        logger.info("🏁 Decision loop #%s completed", next_loop)
