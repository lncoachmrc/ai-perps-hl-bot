from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv


load_dotenv()


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _csv(name: str, default: str) -> List[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(slots=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    port: int = int(os.getenv("PORT", "8080"))

    dry_run: bool = _bool("DRY_RUN", True)
    shadow_mode: bool = _bool("SHADOW_MODE", True)
    start_on_boot: bool = _bool("START_ON_BOOT", True)

    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
    openai_timeout_seconds: int = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "20"))

    hyperliquid_private_key: str = os.getenv("HYPERLIQUID_PRIVATE_KEY", "")
    hyperliquid_account_address: str = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS", "")
    hyperliquid_vault_address: str = os.getenv("HYPERLIQUID_VAULT_ADDRESS", "")

    database_url: str = os.getenv("DATABASE_URL", "")

    coinmarketcap_api_key: str = os.getenv("COINMARKETCAP_API_KEY", "")
    cryptopanic_api_key: str = os.getenv("CRYPTOPANIC_API_KEY", "")
    coinjournal_api_key: str = os.getenv("COINJOURNAL_API_KEY", "")

    universe_symbols: List[str] = field(default_factory=lambda: _csv("UNIVERSE_SYMBOLS", "BTC,ETH,SOL"))
    quote_symbol: str = os.getenv("QUOTE_SYMBOL", "USDC")
    base_equity_usdc: float = float(os.getenv("BASE_EQUITY_USDC", "1000"))

    max_open_positions: int = int(os.getenv("MAX_OPEN_POSITIONS", "2"))
    max_leverage: float = float(os.getenv("MAX_LEVERAGE", "2"))
    risk_per_trade_pct: float = float(os.getenv("RISK_PER_TRADE_PCT", "0.20"))
    daily_stop_pct: float = float(os.getenv("DAILY_STOP_PCT", "1.00"))
    weekly_stop_pct: float = float(os.getenv("WEEKLY_STOP_PCT", "3.00"))
    loop_interval_seconds: int = int(os.getenv("LOOP_INTERVAL_SECONDS", "30"))

    live_initial_size_multiplier_cap: float = float(os.getenv("LIVE_INITIAL_SIZE_MULTIPLIER_CAP", "0.10"))
    live_max_order_notional_usdc: float = float(os.getenv("LIVE_MAX_ORDER_NOTIONAL_USDC", "25"))
    live_order_slippage: float = float(os.getenv("LIVE_ORDER_SLIPPAGE", "0.01"))
    hyperliquid_execution_timeout_seconds: float = float(os.getenv("HYPERLIQUID_EXECUTION_TIMEOUT_SECONDS", "15"))


settings = Settings()
