from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from app.core.enums import DecisionAction
from app.domain.decision import JudgeDecision
from app.llm.openai_client import OpenAIClientFactory
from app.llm.schemas import JUDGE_RESPONSE_SCHEMA
from app.settings import Settings

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "config" / "prompts" / "judge_system.txt"


def _clamp_float(value: Any, minimum: float, maximum: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, minimum), maximum)


def _clamp_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, minimum), maximum)


def _load_system_prompt() -> str:
    try:
        prompt = _PROMPT_PATH.read_text(encoding="utf-8").strip()
        if prompt:
            return prompt
    except FileNotFoundError:
        logger.warning("Judge system prompt file not found", extra={"path": str(_PROMPT_PATH)})
    except OSError:
        logger.exception("Failed to read judge system prompt", extra={"path": str(_PROMPT_PATH)})

    return (
        "You are the final trading judge for a crypto perpetuals system. "
        "Default to NO_TRADE when evidence is mixed. "
        "Never override hard risk constraints. "
        "Output only structured JSON matching the schema."
    )


class JudgeLLM:
    def __init__(self, enabled: bool = False, model: str = "gpt-5.4-mini") -> None:
        self.enabled = enabled
        self.model = model
        self.system_prompt = _load_system_prompt()
        self.client: Optional[Any] = None

        if self.enabled:
            settings = Settings()
            if settings.openai_api_key:
                self.client = OpenAIClientFactory.build(settings)
            else:
                logger.warning("Judge LLM enabled but OPENAI_API_KEY is missing")

    def decide(self, dossier: Dict[str, Any]) -> JudgeDecision:
        if not self.enabled or self.client is None:
            logger.info(
                "Judge LLM unavailable, defaulting to NO_TRADE",
                extra={"model": self.model, "enabled": self.enabled},
            )
            return self._no_trade_decision("llm_unavailable")

        try:
            return self._decide_with_openai(dossier)
        except Exception:
            logger.exception("Judge LLM call failed, defaulting to NO_TRADE", extra={"model": self.model})
            return self._no_trade_decision("judge_llm_error")

    def _decide_with_openai(self, dossier: Dict[str, Any]) -> JudgeDecision:
        response = self.client.responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": self.system_prompt},
                {
                    "role": "user",
                    "content": (
                        "Evaluate this trading dossier. "
                        "Return only a JSON object that matches the required schema.\n\n"
                        f"{json.dumps(dossier, ensure_ascii=False, sort_keys=True, default=str)}"
                    ),
                },
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "judge_response",
                    "schema": JUDGE_RESPONSE_SCHEMA,
                    "strict": True,
                }
            },
        )

        raw_text = (getattr(response, "output_text", "") or "").strip()
        if not raw_text:
            logger.warning("Judge LLM returned empty output", extra={"model": self.model})
            return self._no_trade_decision("empty_llm_output")

        payload = json.loads(raw_text)
        decision = self._decision_from_payload(payload)

        logger.info(
            "Judge decision created",
            extra={
                "action": decision.action.value,
                "confidence": decision.confidence,
                "model": self.model,
            },
        )
        return decision

    def _decision_from_payload(self, payload: Dict[str, Any]) -> JudgeDecision:
        try:
            action = DecisionAction(str(payload.get("action", DecisionAction.NO_TRADE.value)))
        except ValueError:
            action = DecisionAction.NO_TRADE

        confidence = _clamp_float(payload.get("confidence"), 0.0, 1.0, 0.0)
        size_multiplier = _clamp_float(payload.get("size_multiplier"), 0.0, 1.0, 0.0)
        ttl_minutes = _clamp_int(payload.get("ttl_minutes"), 1, 240, 30)

        reasons_raw = payload.get("reasons", [])
        if not isinstance(reasons_raw, list):
            reasons_raw = []
        reasons = [str(item).strip() for item in reasons_raw if str(item).strip()][:5]

        stop_logic = str(payload.get("stop_logic", "use_quant_invalidation")).strip() or "use_quant_invalidation"
        take_profit_logic = (
            str(payload.get("take_profit_logic", "1.5R_or_trailing")).strip() or "1.5R_or_trailing"
        )

        if action in {DecisionAction.NO_TRADE, DecisionAction.HOLD}:
            size_multiplier = 0.0

        return JudgeDecision(
            action=action,
            confidence=confidence,
            size_multiplier=size_multiplier,
            ttl_minutes=ttl_minutes,
            reasons=reasons or ["llm_decision"],
            stop_logic=stop_logic,
            take_profit_logic=take_profit_logic,
        )

    def _no_trade_decision(self, reason: str) -> JudgeDecision:
        return JudgeDecision(
            action=DecisionAction.NO_TRADE,
            confidence=0.0,
            size_multiplier=0.0,
            ttl_minutes=30,
            reasons=[reason],
            stop_logic="use_quant_invalidation",
            take_profit_logic="1.5R_or_trailing",
        )
