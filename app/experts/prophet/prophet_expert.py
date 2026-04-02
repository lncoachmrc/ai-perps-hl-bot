from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict

import pandas as pd

logger = logging.getLogger(__name__)

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


def _ema(data: pd.Series, period: int) -> pd.Series:
    return data.astype(float).ewm(span=period, adjust=False, min_periods=period).mean()


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
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


class ProphetExpert:
    def __init__(self) -> None:
        try:
            info_client = get_shared_info_client()
            if info_client is None:
                logger.warning("⚠️ Prophet expert init | Hyperliquid SDK unavailable | using neutral fallback")
                return
            logger.info("🔮 Prophet expert market reader ready | network=mainnet")
        except Exception:
            logger.exception("❌ Prophet expert init failed | using neutral fallback")

    def _fetch_candles(self, asset: str, interval: str, limit: int) -> pd.DataFrame:
        return fetch_candles_df(
            asset=asset,
            interval=interval,
            limit=limit,
            logger=logger,
        )

    def _fallback(self) -> Dict[str, Any]:
        return {
            "trend_bias": "neutral",
            "forecast_delta_4h": 0.0,
            "interval_width": "wide",
            "changepoint_stress": "low",
            "source": "fallback",
        }

    def evaluate(self, market_snapshot: Dict[str, Any]) -> Dict[str, Any]:
        asset = str(market_snapshot.get("asset", "")).upper()
        if not asset:
            return self._fallback()

        try:
            df_1h = self._fetch_candles(asset, "1h", limit=240)
            df_15m = self._fetch_candles(asset, "15m", limit=160)
            if len(df_1h) < 80 or len(df_15m) < 80:
                logger.warning("⚠️ Prophet expert | insufficient candles | asset=%s", asset)
                return self._fallback()

            price = _safe_float(market_snapshot.get("mark_price", 0.0), _safe_float(df_1h["close"].iloc[-1]))
            if price <= 0:
                price = _safe_float(df_1h["close"].iloc[-1])

            df_1h["ema_20"] = _ema(df_1h["close"], 20)
            df_1h["ema_50"] = _ema(df_1h["close"], 50)
            df_1h["atr_14"] = _atr(df_1h["high"], df_1h["low"], df_1h["close"], 14)
            df_15m["ema_20"] = _ema(df_15m["close"], 20)

            current_1h = df_1h.iloc[-1]
            ema20_1h = _safe_float(current_1h["ema_20"], price)
            ema50_1h = _safe_float(current_1h["ema_50"], price)
            atr14_1h = _safe_float(current_1h["atr_14"], 0.0)
            atr_pct_1h = (atr14_1h / price) if price > 0 else 0.0

            ema20_15m = _safe_float(df_15m["ema_20"].iloc[-1], price)

            prev_close_4h = _safe_float(df_1h["close"].iloc[-5], price) if len(df_1h) >= 5 else price
            prev_close_8h = _safe_float(df_1h["close"].iloc[-9], price) if len(df_1h) >= 9 else price
            prev_close_24h = _safe_float(df_1h["close"].iloc[-25], price) if len(df_1h) >= 25 else price

            momentum_4h_pct = (price / prev_close_4h - 1.0) if prev_close_4h > 0 else 0.0
            momentum_8h_pct = (price / prev_close_8h - 1.0) if prev_close_8h > 0 else 0.0
            momentum_24h_pct = (price / prev_close_24h - 1.0) if prev_close_24h > 0 else 0.0

            ema_gap_pct = ((ema20_1h - ema50_1h) / price) if price > 0 else 0.0
            intraday_gap_pct = ((price - ema20_15m) / price) if price > 0 else 0.0

            slope_score = _clip(momentum_4h_pct / 0.020, -1.0, 1.0)
            medium_term_score = _clip(momentum_8h_pct / 0.035, -1.0, 1.0)
            long_term_score = _clip(momentum_24h_pct / 0.060, -1.0, 1.0)
            ema_score = _clip(ema_gap_pct / 0.012, -1.0, 1.0)
            intraday_score = _clip(intraday_gap_pct / 0.006, -1.0, 1.0)

            raw_context = (
                0.30 * ema_score
                + 0.25 * slope_score
                + 0.20 * medium_term_score
                + 0.15 * long_term_score
                + 0.10 * intraday_score
            )
            raw_context = _clip(raw_context, -1.0, 1.0)

            forecast_pct_4h = _clip(
                0.45 * momentum_4h_pct
                + 0.30 * momentum_8h_pct
                + 0.15 * momentum_24h_pct
                + 0.10 * (ema_gap_pct * 2.5),
                -0.06,
                0.06,
            )
            forecast_delta_4h = price * forecast_pct_4h

            returns_1h = df_1h["close"].pct_change().dropna()
            realized_vol_1h = _safe_float(returns_1h.tail(24).std(), 0.0)
            sign_flips = 0
            if len(returns_1h) >= 8:
                recent_signs = [1 if x > 0 else -1 if x < 0 else 0 for x in returns_1h.tail(8)]
                sign_flips = sum(
                    1
                    for i in range(1, len(recent_signs))
                    if recent_signs[i] != 0 and recent_signs[i - 1] != 0 and recent_signs[i] != recent_signs[i - 1]
                )

            disagreement = abs(slope_score - intraday_score)
            uncertainty_score = _clip(
                (atr_pct_1h / 0.012) * 0.35
                + (realized_vol_1h / 0.010) * 0.35
                + min(sign_flips / 6.0, 1.0) * 0.20
                + min(disagreement / 1.5, 1.0) * 0.10,
                0.0,
                1.0,
            )

            if uncertainty_score <= 0.33:
                interval_width = "narrow"
            elif uncertainty_score <= 0.66:
                interval_width = "medium"
            else:
                interval_width = "wide"

            changepoint_base = (
                min(sign_flips / 5.0, 1.0) * 0.45
                + min(abs(ema_gap_pct) / 0.003, 1.0) * 0.15
                + min(abs(intraday_gap_pct - ema_gap_pct) / 0.010, 1.0) * 0.20
                + min(realized_vol_1h / 0.012, 1.0) * 0.20
            )
            if changepoint_base <= 0.33:
                changepoint_stress = "low"
            elif changepoint_base <= 0.66:
                changepoint_stress = "medium"
            else:
                changepoint_stress = "high"

            conviction = abs(raw_context)
            if conviction >= 0.30 and uncertainty_score < 0.75:
                trend_bias = "bullish" if raw_context > 0 else "bearish"
            else:
                trend_bias = "neutral"

            return {
                "trend_bias": trend_bias,
                "forecast_delta_4h": round(forecast_delta_4h, 6),
                "forecast_pct_4h": round(forecast_pct_4h, 6),
                "interval_width": interval_width,
                "changepoint_stress": changepoint_stress,
                "context_strength": round(conviction, 4),
                "uncertainty_score": round(uncertainty_score, 4),
                "ema_gap_pct_1h": round(ema_gap_pct, 6),
                "intraday_gap_pct_15m": round(intraday_gap_pct, 6),
                "momentum_4h_pct": round(momentum_4h_pct, 6),
                "momentum_8h_pct": round(momentum_8h_pct, 6),
                "momentum_24h_pct": round(momentum_24h_pct, 6),
                "atr_pct_1h": round(atr_pct_1h, 6),
                "source": "hyperliquid_trend_context",
            }
        except Exception:
            logger.exception("❌ Prophet expert evaluation failed | asset=%s | falling back to neutral", asset)
            return self._fallback()
