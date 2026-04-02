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
    if value in (None, ""):
        return None
    try:
        ts = int(str(value))
        return datetime.fromtimestamp(ts, tz=timezone.utc)
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


class AlternativeMeSource:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cache_path = Path(settings.alternative_me_cache_path)

    def fetch(self) -> List[Dict[str, Any]]:
        cached_payload = _safe_load_json(self.cache_path)
        cached_items = cached_payload.get("items") if isinstance(cached_payload.get("items"), list) else None
        fetched_at = float(cached_payload.get("fetched_at", 0.0) or 0.0)
        age_seconds = max(0.0, datetime.now(timezone.utc).timestamp() - fetched_at)

        if cached_items is not None and age_seconds < self.settings.alternative_me_min_interval_seconds:
            return cached_items

        try:
            response = requests.get(
                self.settings.alternative_me_fng_url,
                params={"limit": 1},
                timeout=self.settings.alternative_me_timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
            data = body.get("data") or []
            if not data:
                raise ValueError("Alternative.me FNG returned no data")

            row = data[0]
            classification = str(row.get("value_classification") or "Neutral")
            value = int(row.get("value") or 0)
            published_at = _parse_dt(row.get("timestamp"))
            freshness = _freshness_minutes(published_at)

            items: List[Dict[str, Any]] = []
            for asset in self.settings.universe_symbols:
                items.append(
                    {
                        "source": "alternative_me",
                        "source_kind": "macro_sentiment",
                        "title": f"Alternative.me Fear & Greed {value}/100 ({classification})",
                        "url": "https://api.alternative.me/fng/",
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
                            "time_until_update": row.get("time_until_update"),
                        },
                    }
                )

            _safe_write_json(
                self.cache_path,
                {"fetched_at": datetime.now(timezone.utc).timestamp(), "items": items},
            )
            return items
        except Exception:
            logger.exception("📰 Alternative.me sentiment fetch failed | using cache if available")
            return cached_items or []
