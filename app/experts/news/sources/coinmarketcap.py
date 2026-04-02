from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from app.settings import Settings

logger = logging.getLogger(__name__)


def _safe_load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _safe_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _freshness_minutes(published_at: Optional[datetime]) -> Optional[int]:
    if published_at is None:
        return None
    delta = datetime.now(timezone.utc) - published_at
    return max(0, int(delta.total_seconds() // 60))


def _classification_to_direction(classification: str) -> str:
    value = classification.strip().lower()
    if "greed" in value:
        return "bullish"
    if "fear" in value:
        return "bearish"
    return "neutral"


def _classification_to_impact(classification: str) -> str:
    value = classification.strip().lower()
    if "extreme" in value:
        return "high"
    if value in {"fear", "greed"}:
        return "medium"
    return "low"


class CoinMarketCapSource:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cache_path = Path(settings.cmc_cache_path)

    def fetch(self) -> List[Dict[str, Any]]:
        cached_payload = _safe_load_json(self.cache_path)
        cached_items = cached_payload.get("items") if isinstance(cached_payload.get("items"), list) else None
        fetched_at = float(cached_payload.get("fetched_at", 0.0) or 0.0)
        age_seconds = max(0.0, datetime.now(timezone.utc).timestamp() - fetched_at)

        if cached_items is not None and age_seconds < self.settings.cmc_min_interval_seconds:
            return cached_items

        if not self.settings.coinmarketcap_api_key:
            logger.info("📰 CMC sentiment skipped | COINMARKETCAP_API_KEY missing")
            return cached_items or []

        try:
            response = requests.get(
                "https://pro-api.coinmarketcap.com/v3/fear-and-greed/historical",
                headers={"X-CMC_PRO_API_KEY": self.settings.coinmarketcap_api_key, "Accept": "application/json"},
                params={"limit": 1},
                timeout=self.settings.cmc_timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
            data = body.get("data") or []
            if not data:
                raise ValueError("CMC fear and greed returned no data")

            row = data[0]
            classification = str(row.get("value_classification") or "Neutral")
            value = int(row.get("value") or 0)
            published_at = _parse_dt(row.get("timestamp"))
            freshness = _freshness_minutes(published_at)

            items: List[Dict[str, Any]] = []
            for asset in self.settings.cmc_market_symbols:
                items.append(
                    {
                        "source": "coinmarketcap",
                        "source_kind": "macro_sentiment",
                        "title": f"CMC Fear & Greed {value}/100 ({classification})",
                        "url": "https://coinmarketcap.com/charts/fear-and-greed-index/",
                        "published_at": published_at.isoformat() if published_at else None,
                        "freshness_minutes": freshness,
                        "assets": [asset],
                        "direction": _classification_to_direction(classification),
                        "impact": _classification_to_impact(classification),
                        "score": float(value - 50) / 50.0,
                        "payload": {
                            "value": value,
                            "value_classification": classification,
                            "timestamp": row.get("timestamp"),
                        },
                    }
                )

            _safe_write_json(
                self.cache_path,
                {"fetched_at": datetime.now(timezone.utc).timestamp(), "items": items},
            )
            return items
        except Exception:
            logger.exception("📰 CMC sentiment fetch failed | using cache if available")
            return cached_items or []
