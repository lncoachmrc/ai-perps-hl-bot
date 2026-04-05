from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2.extras import Json

from app.experts.news.sources.alternative_me import AlternativeMeSource
from app.experts.news.sources.coinjournal import CoinJournalSource
from app.experts.news.sources.coinmarketcap import CoinMarketCapSource
from app.experts.news.sources.cryptopanic import CryptoPanicSource
from app.settings import Settings

logger = logging.getLogger(__name__)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


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
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _impact_weight(value: str) -> float:
    mapping = {"low": 1.0, "medium": 2.0, "high": 3.0}
    return mapping.get(str(value).lower(), 1.0)


class NewsExpert:
    def __init__(self) -> None:
        self.settings = Settings()
        self.cryptopanic = CryptoPanicSource(self.settings)
        self.coinmarketcap = CoinMarketCapSource(self.settings)
        self.alternative_me = AlternativeMeSource(self.settings)
        self.coinjournal = CoinJournalSource(self.settings)

        self._aggregate_cache: Dict[str, Any] = {"fetched_at": 0.0, "items": []}
        self._seen_path = Path(self.settings.news_events_cache_path)
        self._seen_payload = _safe_load_json(self._seen_path)
        self._seen_fingerprints: List[str] = list(self._seen_payload.get("fingerprints", []))

    def _fingerprint(self, item: Dict[str, Any], asset: Optional[str]) -> str:
        basis = "|".join(
            [
                str(item.get("source", "")),
                str(asset or ""),
                str(item.get("title", "")),
                str(item.get("url", "")),
                str(item.get("published_at", "")),
            ]
        )
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()

    def _persist_seen(self) -> None:
        payload = {
            "fingerprints": self._seen_fingerprints[-2000:],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _safe_write_json(self._seen_path, payload)

    def _write_news_events(self, items: List[Dict[str, Any]]) -> None:
        if not self.settings.database_url or not items:
            return

        rows: List[tuple[str, Optional[str], str, str, str, str, Dict[str, Any]]] = []
        for item in items:
            assets = item.get("assets") or [None]
            if not isinstance(assets, list) or not assets:
                assets = [None]
            for asset in assets:
                asset_value = str(asset).upper() if asset else None
                fingerprint = self._fingerprint(item, asset_value)
                if fingerprint in self._seen_fingerprints:
                    continue
                rows.append(
                    (
                        str(item.get("source", "unknown")),
                        asset_value,
                        str(item.get("impact", "low")),
                        str(item.get("direction", "neutral")),
                        str(item.get("title", "")),
                        str(item.get("url", "")),
                        {**item, "asset": asset_value, "fingerprint": fingerprint},
                    )
                )
                self._seen_fingerprints.append(fingerprint)

        if not rows:
            return

        try:
            with psycopg2.connect(self.settings.database_url) as conn:
                with conn.cursor() as cur:
                    cur.executemany(
                        """
                        INSERT INTO news_events (source, asset, impact, direction, title, url, payload)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        [
                            (source, asset, impact, direction, title, url, Json(payload))
                            for source, asset, impact, direction, title, url, payload in rows
                        ],
                    )
            self._persist_seen()
        except Exception:
            logger.exception("📰 Failed to write news_events to PostgreSQL")

    def _dedupe_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: List[Dict[str, Any]] = []
        seen_keys: set[str] = set()
        for item in items:
            key = "|".join(
                [
                    str(item.get("source_kind", "")),
                    str(item.get("title", "")).strip().lower(),
                    str(item.get("url", "")).strip().lower(),
                ]
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(item)
        return deduped

    def _fetch_source_items(self, source_name: str, source: Any) -> List[Dict[str, Any]]:
        try:
            items = source.fetch()
            if isinstance(items, list):
                return items
            logger.warning("📰 News source returned non-list payload | source=%s", source_name)
            return []
        except Exception:
            logger.exception(
                "📰 News source failed unexpectedly | source=%s | continuing without blocking trading",
                source_name,
            )
            return []

    def _get_items(self) -> List[Dict[str, Any]]:
        if (_now_ts() - float(self._aggregate_cache.get("fetched_at", 0.0) or 0.0)) < 30.0:
            cached_items = self._aggregate_cache.get("items")
            if isinstance(cached_items, list):
                return cached_items

        items: List[Dict[str, Any]] = []
        items.extend(self._fetch_source_items("cryptopanic", self.cryptopanic))
        items.extend(self._fetch_source_items("coinmarketcap", self.coinmarketcap))
        items.extend(self._fetch_source_items("alternative_me", self.alternative_me))
        items.extend(self._fetch_source_items("coinjournal", self.coinjournal))

        deduped = self._dedupe_items(items)
        self._write_news_events(deduped)
        self._aggregate_cache = {"fetched_at": _now_ts(), "items": deduped}
        return deduped

    def evaluate(self, asset: str) -> Dict[str, Any]:
        asset = str(asset).upper().strip()
        items = [item for item in self._get_items() if asset in (item.get("assets") or [])]

        if not items:
            return {
                "asset": asset,
                "impact": "low",
                "direction": "neutral",
                "headline_conflict": False,
                "tradability_flag": "allowed",
                "freshness_minutes": None,
                "source_counts": {},
                "macro_sentiment_value": None,
                "macro_sentiment_classification": None,
            }

        freshest = [
            item.get("freshness_minutes")
            for item in items
            if isinstance(item.get("freshness_minutes"), int)
        ]
        freshness_minutes = min(freshest) if freshest else None

        source_counts: Dict[str, int] = {}
        bullish_score = 0.0
        bearish_score = 0.0
        high_impact = False
        macro_sentiment_value = None
        macro_sentiment_classification = None

        for item in items:
            source = str(item.get("source", "unknown"))
            source_counts[source] = source_counts.get(source, 0) + 1
            impact = str(item.get("impact", "low"))
            weight = _impact_weight(impact)
            if impact == "high":
                high_impact = True

            direction = str(item.get("direction", "neutral"))
            if direction == "bullish":
                bullish_score += weight
            elif direction == "bearish":
                bearish_score += weight

            payload = item.get("payload") or {}
            if source == "coinmarketcap" and isinstance(payload, dict):
                macro_sentiment_value = payload.get("value")
                macro_sentiment_classification = payload.get("value_classification")
            elif macro_sentiment_value is None and source == "alternative_me" and isinstance(payload, dict):
                macro_sentiment_value = payload.get("value")
                macro_sentiment_classification = payload.get("value_classification")

        headline_conflict = bullish_score > 0 and bearish_score > 0

        net_score = bullish_score - bearish_score
        direction = "neutral"
        if net_score >= 2.0:
            direction = "bullish"
        elif net_score <= -2.0:
            direction = "bearish"

        impact = "low"
        if high_impact or abs(net_score) >= 4.0:
            impact = "high"
        elif headline_conflict or abs(net_score) >= 2.0 or (macro_sentiment_classification or "").lower().startswith("extreme"):
            impact = "medium"

        tradability_flag = "allowed"
        if headline_conflict or impact == "high":
            tradability_flag = "caution"

        return {
            "asset": asset,
            "impact": impact,
            "direction": direction,
            "headline_conflict": headline_conflict,
            "tradability_flag": tradability_flag,
            "freshness_minutes": freshness_minutes,
            "source_counts": source_counts,
            "macro_sentiment_value": _safe_float(macro_sentiment_value, 0.0) if macro_sentiment_value is not None else None,
            "macro_sentiment_classification": macro_sentiment_classification,
        }
