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


def _parse_assets(item: Dict[str, Any], allowed: List[str]) -> List[str]:
    assets: set[str] = set()
    for key in ("currencies", "instruments"):
        raw = item.get(key) or []
        if isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, dict):
                    symbol = str(entry.get("code") or entry.get("symbol") or entry.get("name") or "").upper()
                    if symbol in allowed:
                        assets.add(symbol)
                elif isinstance(entry, str):
                    symbol = entry.upper()
                    if symbol in allowed:
                        assets.add(symbol)
    title = str(item.get("title", "")).upper()
    for symbol in allowed:
        if symbol in title:
            assets.add(symbol)
    return sorted(assets)


class CryptoPanicSource:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cache_path = Path(settings.cryptopanic_cache_path)
        self.usage_path = Path(settings.cryptopanic_usage_path)

    def _usage_allows_refresh(self) -> bool:
        month_key = datetime.now(timezone.utc).strftime("%Y-%m")
        payload = _safe_load_json(self.usage_path)
        if payload.get("month") != month_key:
            payload = {"month": month_key, "count": 0}
        count = int(payload.get("count", 0) or 0)
        projected = (count + 1) * float(self.settings.cryptopanic_quota_safety_factor)
        return projected <= int(self.settings.cryptopanic_monthly_cap)

    def _increment_usage(self) -> None:
        month_key = datetime.now(timezone.utc).strftime("%Y-%m")
        payload = _safe_load_json(self.usage_path)
        if payload.get("month") != month_key:
            payload = {"month": month_key, "count": 0}
        payload["count"] = int(payload.get("count", 0) or 0) + 1
        _safe_write_json(self.usage_path, payload)

    def _load_cached(self) -> Optional[List[Dict[str, Any]]]:
        payload = _safe_load_json(self.cache_path)
        items = payload.get("items")
        if isinstance(items, list):
            return items
        return None


    def fetch(self) -> List[Dict[str, Any]]:
        cached_payload = _safe_load_json(self.cache_path)
        cached_items = cached_payload.get("items") if isinstance(cached_payload.get("items"), list) else None
        fetched_at = float(cached_payload.get("fetched_at", 0.0) or 0.0)
        age_seconds = max(0.0, datetime.now(timezone.utc).timestamp() - fetched_at)

        if cached_items is not None and age_seconds < self.settings.cryptopanic_min_interval_seconds:
            return cached_items

        if not self._usage_allows_refresh():
            logger.info("📰 CryptoPanic refresh skipped | monthly cap safety triggered")
            return cached_items or []

        params: Dict[str, Any] = {
            "currencies": ",".join(self.settings.cryptopanic_currencies),
            "filter": self.settings.cryptopanic_filter,
            "kind": self.settings.cryptopanic_kind,
        }
        if self.settings.cryptopanic_public:
            params["public"] = "true"
        if self.settings.cryptopanic_api_key:
            params["auth_token"] = self.settings.cryptopanic_api_key
        elif not self.settings.cryptopanic_public:
            logger.info("📰 CryptoPanic disabled | no API key and public mode off")
            return cached_items or []

        headers = {"User-Agent": self.settings.cryptopanic_user_agent}
        try:
            response = requests.get(
                self.settings.cryptopanic_posts_url,
                params=params,
                headers=headers,
                timeout=self.settings.cryptopanic_timeout_seconds,
            )
        except requests.RequestException as exc:
            logger.warning(
                "📰 CryptoPanic network error | %s | using cache if available",
                exc.__class__.__name__,
            )
            return cached_items or []

        status = response.status_code
        if status in {429, 502, 503, 504}:
            logger.warning(
                "📰 CryptoPanic temporary upstream error | status=%s | using cache if available",
                status,
            )
            return cached_items or []
        if status >= 400:
            logger.error(
                "📰 CryptoPanic request rejected | status=%s | using cache if available",
                status,
            )
            return cached_items or []

        try:
            body = response.json()
            raw_items = body.get("results") or []
            if not isinstance(raw_items, list):
                raise ValueError("CryptoPanic results is not a list")

            items: List[Dict[str, Any]] = []
            for raw in raw_items[: self.settings.cryptopanic_max_items]:
                if not isinstance(raw, dict):
                    continue
                published_at = _parse_dt(raw.get("published_at") or raw.get("created_at"))
                freshness = _freshness_minutes(published_at)
                if freshness is not None and freshness > self.settings.cryptopanic_max_age_minutes:
                    continue

                votes = raw.get("votes") or {}
                positive = int((votes.get("positive") or 0)) if isinstance(votes, dict) else 0
                negative = int((votes.get("negative") or 0)) if isinstance(votes, dict) else 0
                important = int((votes.get("important") or 0)) if isinstance(votes, dict) else 0

                direction = "neutral"
                if positive > negative:
                    direction = "bullish"
                elif negative > positive:
                    direction = "bearish"

                impact = "low"
                if important >= 3:
                    impact = "high"
                elif important >= 1 or abs(positive - negative) >= 3:
                    impact = "medium"

                item = {
                    "source": "cryptopanic",
                    "source_kind": "headline",
                    "title": str(raw.get("title") or "").strip(),
                    "url": str(raw.get("url") or "").strip(),
                    "published_at": published_at.isoformat() if published_at else None,
                    "freshness_minutes": freshness,
                    "assets": _parse_assets(raw, self.settings.universe_symbols),
                    "direction": direction,
                    "impact": impact,
                    "score": float(positive - negative),
                    "payload": raw,
                }
                if item["title"]:
                    items.append(item)

            _safe_write_json(
                self.cache_path,
                {"fetched_at": datetime.now(timezone.utc).timestamp(), "items": items},
            )
            self._increment_usage()
            return items
        except Exception as exc:
            logger.warning(
                "📰 CryptoPanic parse error | %s | using cache if available",
                exc.__class__.__name__,
            )
            return cached_items or []

