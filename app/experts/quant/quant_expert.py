from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict

import pandas as pd

logger = logging.getLogger(__name__)

try:
    import ta  # type: ignore
except Exception:  # pragma: no cover - optional dependency guard
    ta = None

from app.exchange.hyperliquid.market_reads import fetch_candles_df, get_shared_info_client


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(out):
        return float(default)
    return out


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _ema_fallback(data: pd.Series, period: int) -> pd.Series:
    return data.astype(float).ewm(span=period, adjust=False, min_periods=period).mean()


def _rsi_fallback(data: pd.Series, period: int) -> pd.Series:
    close = data.astype(float)
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50.0)


def _atr_fallback(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    high = high.astype(float)
    low = low.astype(float)
    close = close.astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


class QuantExpert:
    def __init__(self) -> None:
        try:
            info_client = get_shared_info_client()
            if info_client is None:
                logger.warning("⚠️ Quant expert init | Hyperliquid SDK unavailable | using conservative fallback")
                return
            logger.info("🧠 Quant expert market reader ready | network=mainnet")
        except Exception:
            logger.exception("❌ Quant expert init failed | using conservative fallback")

    def _fetch_candles(self, asset: str, interval: str, limit: int) -> pd.DataFrame:
        return fetch_candles_df(
            asset=asset,
            interval=interval,
            limit=limit,
            logger=logger,
        )

    def _fallback(self, market_snapshot: Dict[str, Any]) -> Dict[str, Any]:
        price = _safe_float(market_snapshot.get("mark_price", 0.0))
        return {
            "regime": market_snapshot.get("regime_hint", "unknown"),
            "setup_score": 0.0,
            "p_up": 0.5,
            "p_down": 0.5,
            "expected_move_60m": 0.0,
            "invalidation_price": price,
            "signal_strength": 0.0,
            "signal_direction": "neutral",
            "source": "fallback",
        }

    def evaluate(self, market_snapshot: Dict[str, Any]) -> Dict[str, Any]:
        asset = str(market_snapshot.get("asset", "")).upper()
        if not asset:
            return self._fallback(market_snapshot)

        try:
            df_15m = self._fetch_candles(asset, "15m", limit=240)
            df_1h = self._fetch_candles(asset, "1h", limit=240)
            if len(df_15m) < 60 or len(df_1h) < 60:
                logger.warning("⚠️ Quant expert | insufficient candles | asset=%s", asset)
                return self._fallback(market_snapshot)

            price = _safe_float(market_snapshot.get("mark_price", 0.0), _safe_float(df_15m["close"].iloc[-1]))
            if price <= 0:
                price = _safe_float(df_15m["close"].iloc[-1])

            df_15m["ema_20"] = self._ema(df_15m["close"], 20)
            df_15m["ema_50"] = self._ema(df_15m["close"], 50)
            df_15m["rsi_14"] = self._rsi(df_15m["close"], 14)
            df_15m["atr_14"] = self._atr(df_15m["high"], df_15m["low"], df_15m["close"], 14)

            df_1h["ema_20"] = self._ema(df_1h["close"], 20)
            df_1h["ema_50"] = self._ema(df_1h["close"], 50)
            df_1h["rsi_14"] = self._rsi(df_1h["close"], 14)

            current_15 = df_15m.iloc[-1]
            current_1h = df_1h.iloc[-1]

            ema20_15 = _safe_float(current_15["ema_20"], price)
            ema50_15 = _safe_float(current_15["ema_50"], price)
            ema20_1h = _safe_float(current_1h["ema_20"], price)
            ema50_1h = _safe_float(current_1h["ema_50"], price)

            rsi14_15 = _safe_float(current_15["rsi_14"], 50.0)
            rsi14_1h = _safe_float(current_1h["rsi_14"], 50.0)
            atr14_15 = _safe_float(current_15["atr_14"], 0.0)
            atr_pct_15 = (atr14_15 / price) if price > 0 else 0.0

            momentum_1h_pct = 0.0
            if len(df_15m) >= 5:
                prev_1h_close = _safe_float(df_15m["close"].iloc[-5], price)
                if prev_1h_close > 0:
                    momentum_1h_pct = (price / prev_1h_close) - 1.0

            momentum_4h_pct = 0.0
            if len(df_1h) >= 5:
                prev_4h_close = _safe_float(df_1h["close"].iloc[-5], price)
                if prev_4h_close > 0:
                    momentum_4h_pct = (price / prev_4h_close) - 1.0

            trend_15 = _clip((ema20_15 - ema50_15) / max(price * 0.004, 1e-9), -1.0, 1.0)
            trend_1h = _clip((ema20_1h - ema50_1h) / max(price * 0.010, 1e-9), -1.0, 1.0)
            rsi_15_score = _clip((rsi14_15 - 50.0) / 15.0, -1.0, 1.0)
            rsi_1h_score = _clip((rsi14_1h - 50.0) / 15.0, -1.0, 1.0)
            mom_15_score = _clip(momentum_1h_pct / 0.006, -1.0, 1.0)
            mom_1h_score = _clip(momentum_4h_pct / 0.015, -1.0, 1.0)

            raw_signal = (
                0.30 * trend_15
                + 0.25 * trend_1h
                + 0.15 * mom_15_score
                + 0.10 * mom_1h_score
                + 0.10 * rsi_15_score
                + 0.10 * rsi_1h_score
            )

            spread_bps = _safe_float(market_snapshot.get("spread_bps", 0.0))
            funding_rate = _safe_float(market_snapshot.get("funding_rate", 0.0))
            oi_delta_1h = _safe_float(market_snapshot.get("open_interest_delta_1h", 0.0))

            alignment_bonus = 0.0
            if trend_15 * trend_1h > 0 and abs(trend_15) > 0.10 and abs(trend_1h) > 0.10:
                alignment_bonus = 0.10

            disagreement_penalty = 0.0
            if trend_15 * trend_1h < 0 and abs(trend_15) > 0.20 and abs(trend_1h) > 0.20:
                disagreement_penalty = 0.18

            oi_confirmation = min(abs(oi_delta_1h) / 5.0, 0.20)
            if abs(raw_signal) > 0.15:
                raw_signal += math.copysign(oi_confirmation * 0.10, raw_signal)

            crowding_penalty = 0.0
            if raw_signal > 0:
                crowding_penalty = min(max(funding_rate, 0.0) / 0.0008, 0.15)
            elif raw_signal < 0:
                crowding_penalty = min(abs(min(funding_rate, 0.0)) / 0.0008, 0.15)

            raw_signal = _clip(raw_signal, -1.0, 1.0)
            signal_strength = abs(raw_signal)

            if signal_strength < 0.12:
                regime = "range_bound" if atr_pct_15 < 0.010 else "balanced"
            elif raw_signal > 0 and trend_1h > 0 and mom_15_score > 0:
                regime = "trend_up"
            elif raw_signal < 0 and trend_1h < 0 and mom_15_score < 0:
                regime = "trend_down"
            elif atr_pct_15 >= 0.012:
                regime = "high_volatility"
            else:
                regime = market_snapshot.get("regime_hint", "balanced")

            spread_penalty = min(max(spread_bps - 1.0, 0.0) / 8.0, 0.15)
            volatility_penalty = 0.10 if atr_pct_15 < 0.0015 else 0.0

            setup_score = _clip(
                ((signal_strength - 0.05) / 0.65)
                + alignment_bonus
                - disagreement_penalty
                - spread_penalty
                - crowding_penalty
                - volatility_penalty,
                0.0,
                1.0,
            )

            p_up = _clip(0.5 + (raw_signal * 0.35), 0.05, 0.95)
            p_down = _clip(1.0 - p_up, 0.05, 0.95)

            expected_move_pct_60m = max(0.0, atr_pct_15 * (0.85 + 1.50 * signal_strength))
            expected_move_60m = price * expected_move_pct_60m

            invalidation_distance = max(atr14_15 * 1.2, price * 0.003)
            if raw_signal >= 0.10:
                invalidation_price = max(0.0, price - invalidation_distance)
                signal_direction = "bullish"
            elif raw_signal <= -0.10:
                invalidation_price = price + invalidation_distance
                signal_direction = "bearish"
            else:
                invalidation_price = price
                signal_direction = "neutral"

            return {
                "regime": regime,
                "setup_score": round(setup_score, 4),
                "p_up": round(p_up, 4),
                "p_down": round(p_down, 4),
                "expected_move_60m": round(expected_move_60m, 6),
                "invalidation_price": round(invalidation_price, 6),
                "signal_strength": round(signal_strength, 4),
                "signal_direction": signal_direction,
                "ema20_15m": round(ema20_15, 6),
                "ema50_15m": round(ema50_15, 6),
                "ema20_1h": round(ema20_1h, 6),
                "ema50_1h": round(ema50_1h, 6),
                "rsi14_15m": round(rsi14_15, 4),
                "rsi14_1h": round(rsi14_1h, 4),
                "atr14_15m": round(atr14_15, 6),
                "atr_pct_15m": round(atr_pct_15, 6),
                "momentum_1h_pct": round(momentum_1h_pct, 6),
                "momentum_4h_pct": round(momentum_4h_pct, 6),
                "source": "hyperliquid_candles",
            }
        except Exception:
            logger.exception("❌ Quant expert evaluation failed | asset=%s | falling back to neutral", asset)
            return self._fallback(market_snapshot)
