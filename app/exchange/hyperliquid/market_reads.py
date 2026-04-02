from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, Tuple

import pandas as pd

from app.exchange.hyperliquid.resilience import call_with_rate_limit_retry

try:
    from hyperliquid.info import Info
    from hyperliquid.utils import constants

    _HL_SDK_AVAILABLE = True
except Exception:  # pragma: no cover - import safety for partially bootstrapped envs
    Info = None  # type: ignore[assignment]
    constants = None  # type: ignore[assignment]
    _HL_SDK_AVAILABLE = False


INTERVAL_TO_MS = {
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
}

_CANDLES_CACHE: Dict[tuple[str, str, int], tuple[float, pd.DataFrame]] = {}
_SHARED_INFO_CLIENT: Info | None = None


def get_shared_info_client() -> Info | None:
    global _SHARED_INFO_CLIENT

    if not _HL_SDK_AVAILABLE:
        return None

    if _SHARED_INFO_CLIENT is not None:
        return _SHARED_INFO_CLIENT

    timeout_seconds = float(os.getenv("HYPERLIQUID_EXECUTION_TIMEOUT_SECONDS", "15"))
    empty_spot_meta = {"universe": [], "tokens": []}
    _SHARED_INFO_CLIENT = Info(
        constants.MAINNET_API_URL,
        skip_ws=True,
        spot_meta=empty_spot_meta,
        timeout=max(timeout_seconds, 1.0),
    )
    return _SHARED_INFO_CLIENT


def _cache_ttl_seconds(interval: str) -> float:
    if interval == "15m":
        return 45.0
    if interval == "1h":
        return 120.0
    return 30.0


def fetch_candles_df(
    *,
    asset: str,
    interval: str,
    limit: int,
    logger: logging.Logger,
) -> pd.DataFrame:
    cache_key = (asset.upper(), interval, int(limit))
    now = time.time()
    cached = _CANDLES_CACHE.get(cache_key)
    ttl_seconds = _cache_ttl_seconds(interval)

    if cached is not None and (now - cached[0]) < ttl_seconds:
        return cached[1].copy()

    info = get_shared_info_client()
    if info is None:
        raise RuntimeError("Hyperliquid Info client not available")

    if interval not in INTERVAL_TO_MS:
        raise ValueError(f"Unsupported interval: {interval}")

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    step_ms = INTERVAL_TO_MS[interval]
    start_ms = now_ms - (limit * step_ms)

    try:
        raw = call_with_rate_limit_retry(
            lambda: info.candles_snapshot(
                name=asset.upper(),
                interval=interval,
                startTime=start_ms,
                endTime=now_ms,
            ),
            logger=logger,
            operation=f"candles_snapshot:{asset.upper()}:{interval}",
        )
    except Exception:
        if cached is not None:
            logger.warning(
                "⚠️ Using stale candle cache | asset=%s | interval=%s | age_s=%.1f",
                asset.upper(),
                interval,
                now - cached[0],
            )
            return cached[1].copy()
        raise

    if not raw:
        raise RuntimeError(f"No candles returned for {asset} {interval}")

    df = pd.DataFrame(raw)
    expected = {"t", "o", "h", "l", "c", "v"}
    if not expected.issubset(df.columns):
        raise RuntimeError(f"Malformed candles for {asset} {interval}")

    df["timestamp"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    df = df[["timestamp", "o", "h", "l", "c", "v"]].copy()
    df.rename(
        columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"},
        inplace=True,
    )
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df = df.sort_values("timestamp").reset_index(drop=True)

    _CANDLES_CACHE[cache_key] = (now, df.copy())
    return df
