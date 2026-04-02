from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from app.settings import Settings

logger = logging.getLogger(__name__)

POSITIVE_HINTS = ("surge", "rally", "approval", "bull", "breakout", "gain", "record high", "etf inflow")
NEGATIVE_HINTS = ("selloff", "hack", "lawsuit", "bear", "drop", "decline", "outage", "liquidation", "exploit")
HIGH_IMPACT_HINTS = ("etf", "sec", "fed", "cpi", "lawsuit", "hack", "exploit", "outage", "liquidation")


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
        dt = parsedate_to_datetime(text)
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


def _detect_assets(title: str, allowed: List[str]) -> List[str]:
    title_upper = title.upper()
    mapping = {
        "BTC": ("BTC", "BITCOIN"),
        "ETH": ("ETH", "ETHEREUM"),
        "SOL": ("SOL", "SOLANA"),
    }
    assets: List[str] = []
    for symbol in allowed:
        candidates = mapping.get(symbol, (symbol,))
        if any(token in title_upper for token in candidates):
            assets.append(symbol)
    return assets


def _direction_from_title(title: str) -> str:
    title_lower = title.lower()
    positive = sum(1 for token in POSITIVE_HINTS if token in title_lower)
    negative = sum(1 for token in NEGATIVE_HINTS if token in title_lower)
    if positive > negative:
        return "bullish"
    if negative > positive:
        return "bearish"
    return "neutral"


def _impact_from_title(title: str) -> str:
    title_lower = title.lower()
    if any(token in title_lower for token in HIGH_IMPACT_HINTS):
        return "medium"
    return "low"


class CoinJournalSource:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cache_path = Path(settings.coinjournal_cache_path)

    def fetch(self) -> List[Dict[str, Any]]:
        cached_payload = _safe_load_json(self.cache_path)
        cached_items = cached_payload.get("items") if isinstance(cached_payload.get("items"), list) else None
        fetched_at = float(cached_payload.get("fetched_at", 0.0) or 0.0)
        age_seconds = max(0.0, datetime.now(timezone.utc).timestamp() - fetched_at)

        if cached_items is not None and age_seconds < self.settings.coinjournal_min_interval_seconds:
            return cached_items

        try:
            response = requests.get(self.settings.coinjournal_rss_url, timeout=self.settings.coinjournal_timeout_seconds)
            response.raise_for_status()
            root = ET.fromstring(response.content)

            items: List[Dict[str, Any]] = []
            for node in root.findall(".//item")[: self.settings.coinjournal_max_items]:
                title = (node.findtext("title") or "").strip()
                link = (node.findtext("link") or "").strip()
                published_at = _parse_dt(node.findtext("pubDate"))
                freshness = _freshness_minutes(published_at)
                if freshness is not None and freshness > self.settings.coinjournal_max_age_minutes:
                    continue

                assets = _detect_assets(title, self.settings.universe_symbols)
                if not assets:
                    continue

                item = {
                    "source": "coinjournal",
                    "source_kind": "headline",
                    "title": title,
                    "url": link,
                    "published_at": published_at.isoformat() if published_at else None,
                    "freshness_minutes": freshness,
                    "assets": assets,
                    "direction": _direction_from_title(title),
                    "impact": _impact_from_title(title),
                    "score": 0.0,
                    "payload": {
                        "title": title,
                        "link": link,
                        "pubDate": node.findtext("pubDate"),
                    },
                }
                if item["title"]:
                    items.append(item)

            _safe_write_json(
                self.cache_path,
                {"fetched_at": datetime.now(timezone.utc).timestamp(), "items": items},
            )
            return items
        except Exception:
            logger.exception("📰 CoinJournal RSS fetch failed | using cache if available")
            return cached_items or []
