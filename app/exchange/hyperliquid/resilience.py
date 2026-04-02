from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

T = TypeVar("T")


def is_rate_limit_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return True

    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    if response_status == 429:
        return True

    message = str(exc)
    return "(429," in message or " 429 " in message or message.startswith("429,")


def call_with_rate_limit_retry(
    fn: Callable[[], T],
    *,
    logger: logging.Logger,
    operation: str,
    attempts: int = 3,
    base_delay_seconds: float = 0.35,
    max_delay_seconds: float = 1.50,
) -> T:
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_error = exc
            should_retry = is_rate_limit_error(exc) and attempt < attempts
            if not should_retry:
                raise

            delay = min(base_delay_seconds * (2 ** (attempt - 1)), max_delay_seconds)
            logger.warning(
                "⏳ Hyperliquid rate limited | operation=%s | attempt=%s/%s | retry_in=%.2fs",
                operation,
                attempt,
                attempts,
                delay,
            )
            time.sleep(delay)

    if last_error is not None:
        raise last_error

    raise RuntimeError(f"Retry loop exhausted for operation={operation}")
