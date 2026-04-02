from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import psycopg2
from psycopg2.extras import Json

logger = logging.getLogger(__name__)


class JournalService:
    def __init__(self, path: str = "logs/journal.jsonl", database_url: Optional[str] = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.database_url = (database_url or os.getenv("DATABASE_URL", "")).strip()

    def append(self, payload: Dict[str, Any]) -> None:
        self._append_to_file(payload)
        self._append_to_postgres(payload)

    def _append_to_file(self, payload: Dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        logger.debug("Journal entry written to file")

    def _append_to_postgres(self, payload: Dict[str, Any]) -> None:
        if not self.database_url:
            return

        asset = self._clean_text(payload.get("asset"))
        event_type = self._infer_event_type(payload)
        decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
        action = self._clean_text(decision.get("action"))
        reasons = decision.get("reasons")
        stop_logic = self._clean_text(decision.get("stop_logic"))
        take_profit_logic = self._clean_text(decision.get("take_profit_logic"))

        try:
            with psycopg2.connect(self.database_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO journal_events (event_type, asset, payload)
                        VALUES (%s, %s, %s)
                        """,
                        (event_type, asset, Json(payload)),
                    )

                    if action:
                        cur.execute(
                            """
                            INSERT INTO decisions (
                                asset,
                                action,
                                confidence,
                                size_multiplier,
                                ttl_minutes,
                                reasons,
                                stop_logic,
                                take_profit_logic,
                                raw_payload
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                asset or "UNKNOWN",
                                action,
                                self._to_float(decision.get("confidence")),
                                self._to_float(decision.get("size_multiplier")),
                                self._to_int(decision.get("ttl_minutes")),
                                Json(reasons if isinstance(reasons, list) else []),
                                stop_logic,
                                take_profit_logic,
                                Json(payload),
                            ),
                        )
            logger.debug("Journal entry written to PostgreSQL")
        except Exception:
            logger.exception("Journal PostgreSQL write failed; file journal preserved")

    @staticmethod
    def _infer_event_type(payload: Dict[str, Any]) -> str:
        if isinstance(payload.get("decision"), dict):
            return "decision_cycle"
        return "journal_event"

    @staticmethod
    def _clean_text(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_int(value: Any) -> Optional[int]:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
