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

    cryptopanic_public: bool = _bool("CRYPTOPANIC_PUBLIC", True)
    cryptopanic_posts_url: str = os.getenv("CRYPTOPANIC_POSTS_URL", "https://cryptopanic.com/api/developer/v2/posts/")
    cryptopanic_currencies: List[str] = field(default_factory=lambda: _csv("CRYPTOPANIC_CURRENCIES", "BTC,ETH,SOL"))
    cryptopanic_filter: str = os.getenv("CRYPTOPANIC_FILTER", "hot")
    cryptopanic_kind: str = os.getenv("CRYPTOPANIC_KIND", "news")
    cryptopanic_max_items: int = int(os.getenv("CRYPTOPANIC_MAX_ITEMS", "15"))
    cryptopanic_max_age_minutes: int = int(os.getenv("CRYPTOPANIC_MAX_AGE_MINUTES", "1440"))
    cryptopanic_min_interval_seconds: int = int(os.getenv("CRYPTOPANIC_MIN_INTERVAL_SECONDS", "28800"))
    cryptopanic_timeout_seconds: int = int(os.getenv("CRYPTOPANIC_TIMEOUT_SECONDS", "15"))
    cryptopanic_cache_path: str = os.getenv("CRYPTOPANIC_CACHE_PATH", "/tmp/cryptopanic_cache.json")
    cryptopanic_monthly_cap: int = int(os.getenv("CRYPTOPANIC_MONTHLY_CAP", "100"))
    cryptopanic_usage_path: str = os.getenv("CRYPTOPANIC_USAGE_PATH", "/tmp/cryptopanic_usage.json")
    cryptopanic_quota_safety_factor: float = float(os.getenv("CRYPTOPANIC_QUOTA_SAFETY_FACTOR", "1.10"))
    cryptopanic_user_agent: str = os.getenv(
        "CRYPTOPANIC_USER_AGENT",
        "ai-perps-hl-bot/1.0 (+https://railway.app)",
    )

    cmc_min_interval_seconds: int = int(os.getenv("CMC_MIN_INTERVAL_SECONDS", "900"))
    cmc_timeout_seconds: int = int(os.getenv("CMC_TIMEOUT_SECONDS", "15"))
    cmc_market_include_global_metrics: bool = _bool("CMC_MARKET_INCLUDE_GLOBAL_METRICS", False)
    cmc_market_symbols: List[str] = field(default_factory=lambda: _csv("CMC_MARKET_SYMBOLS", "BTC,ETH,SOL"))
    cmc_cache_path: str = os.getenv("CMC_CACHE_PATH", "/tmp/cmc_cache.json")

    alternative_me_fng_url: str = os.getenv("ALTERNATIVE_ME_FNG_URL", "https://api.alternative.me/fng/")
    alternative_me_min_interval_seconds: int = int(os.getenv("ALTERNATIVE_ME_MIN_INTERVAL_SECONDS", "3600"))
    alternative_me_timeout_seconds: int = int(os.getenv("ALTERNATIVE_ME_TIMEOUT_SECONDS", "15"))
    alternative_me_cache_path: str = os.getenv("ALTERNATIVE_ME_CACHE_PATH", "/tmp/alternative_me_fng_cache.json")

    coinjournal_rss_url: str = os.getenv("COINJOURNAL_RSS_URL", "https://coinjournal.net/feed/")
    coinjournal_timeout_seconds: int = int(os.getenv("COINJOURNAL_TIMEOUT_SECONDS", "15"))
    coinjournal_max_items: int = int(os.getenv("COINJOURNAL_MAX_ITEMS", "15"))
    coinjournal_max_age_minutes: int = int(os.getenv("COINJOURNAL_MAX_AGE_MINUTES", "1440"))
    coinjournal_min_interval_seconds: int = int(os.getenv("COINJOURNAL_MIN_INTERVAL_SECONDS", "21600"))
    coinjournal_cache_path: str = os.getenv("COINJOURNAL_CACHE_PATH", "/tmp/coinjournal_cache.json")

    news_events_cache_path: str = os.getenv("NEWS_EVENTS_CACHE_PATH", "/tmp/news_events_seen.json")


settings = Settings()
