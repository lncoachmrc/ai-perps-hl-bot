from __future__ import annotations

from openai import OpenAI

from app.settings import Settings


class OpenAIClientFactory:
    @staticmethod
    def build(settings: Settings) -> OpenAI:
        return OpenAI(api_key=settings.openai_api_key, timeout=settings.openai_timeout_seconds)
