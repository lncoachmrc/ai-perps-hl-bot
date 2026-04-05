from __future__ import annotations

import json
import logging
import math
import os
import time
from typing import Any, Dict, List, Tuple

from app.exchange.hyperliquid.resilience import call_with_rate_limit_retry

logger = logging.getLogger(__name__)

try:
    from eth_account import Account
    from hyperliquid.exchange import Exchange
    from hyperliquid.info import Info
    from hyperliquid.utils import constants

    _HL_SDK_AVAILABLE = True
except Exception:  # pragma: no cover - import safety for environments not fully bootstrapped
    Account = None  # type: ignore[assignment]
    Exchange = None  # type: ignore[assignment]
    Info = None  # type: ignore[assignment]
    constants = None  # type: ignore[assignment]
    _HL_SDK_AVAILABLE = False


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(out):
        return float(default)
    return out


def _clean_env(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip().strip('"').strip("'")


def _normalize_address(value: str | None) -> str:
    cleaned = _clean_env(value)
    return cleaned.lower() if cleaned else ""


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _mask_address(value: str) -> str:
    if len(value) <= 12:
        return value
    return f"{value[:6]}...{value[-4:]}"


def _mask_error(message: str) -> str:
    if not message:
        return ""
    private_key = _clean_env(os.getenv("HYPERLIQUID_PRIVATE_KEY"))
    if private_key:
        message = message.replace(private_key, "***")
    account = _clean_env(os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS"))
    if account:
        message = message.replace(account, _mask_address(account))
    vault = _clean_env(os.getenv("HYPERLIQUID_VAULT_ADDRESS"))
    if vault:
        message = message.replace(vault, _mask_address(vault))
    return message


def _sanitize_for_log(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: Dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            key_lower = key_str.lower()
            if key_lower in {"signature", "private_key", "secret_key", "api_key"}:
                sanitized[key_str] = "***"
            else:
                sanitized[key_str] = _sanitize_for_log(item)
        return sanitized

    if isinstance(value, list):
        return [_sanitize_for_log(item) for item in value]

    if isinstance(value, tuple):
        return tuple(_sanitize_for_log(item) for item in value)

    if isinstance(value, str):
        return _mask_error(value)

    return value


def _stringify_for_log(value: Any, max_len: int = 1500) -> str:
    try:
        text = json.dumps(_sanitize_for_log(value), ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        text = _mask_error(repr(value))
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


class HyperliquidClient:
    def __init__(self, dry_run: bool = True, shadow_mode: bool = True) -> None:
        self.dry_run = dry_run
        self.shadow_mode = shadow_mode

        self.account_address = _normalize_address(os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS"))
        self.vault_address = _normalize_address(os.getenv("HYPERLIQUID_VAULT_ADDRESS"))
        self.private_key = _clean_env(os.getenv("HYPERLIQUID_PRIVATE_KEY"))
        self.signer_address = ""
        self.allow_different_signer = _env_flag("ALLOW_DIFFERENT_SIGNER", False)
        self.block_on_signer_mismatch = _env_flag("DEPLOY_FAILSAFE_BLOCK_ON_SIGNER_MISMATCH", False)

        if (
            self.account_address
            and self.vault_address
            and self.account_address == self.vault_address
        ):
            logger.warning(
                "⚠️ HYPERLIQUID_VAULT_ADDRESS equals HYPERLIQUID_ACCOUNT_ADDRESS | treating vault address as unset for direct account trading"
            )
            self.vault_address = ""

        self.read_address = self.vault_address or self.account_address

        self.base_equity_usdc = _safe_float(os.getenv("BASE_EQUITY_USDC"), 1000.0)
        self.live_initial_size_multiplier_cap = max(
            0.0,
            min(_safe_float(os.getenv("LIVE_INITIAL_SIZE_MULTIPLIER_CAP"), 0.10), 1.0),
        )
        self.live_max_order_notional_usdc = max(0.0, _safe_float(os.getenv("LIVE_MAX_ORDER_NOTIONAL_USDC"), 25.0))
        self.live_order_slippage = max(0.001, _safe_float(os.getenv("LIVE_ORDER_SLIPPAGE"), 0.01))
        self.execution_timeout_seconds = max(
            1.0,
            _safe_float(os.getenv("HYPERLIQUID_EXECUTION_TIMEOUT_SECONDS"), 15.0),
        )

        self._info = None
        self._exchange = None
        self._global_state_cache: Tuple[Dict[str, Any], List[Dict[str, Any]]] | None = None
        self._global_state_timestamp = 0.0
        self._all_mids_cache: Dict[str, Any] = {}
        self._all_mids_timestamp = 0.0
        self._orderbook_cache: Dict[str, Dict[str, Any]] = {}
        self._orderbook_timestamp: Dict[str, float] = {}
        self._oi_history: Dict[str, List[Tuple[float, float]]] = {}
        self._global_state_ttl_seconds = 20.0
        self._all_mids_ttl_seconds = 5.0
        self._orderbook_ttl_seconds = 8.0

        if not _HL_SDK_AVAILABLE:
            logger.warning("⚠️ Hyperliquid SDK not available | market reads and live execution will use placeholders")
            return

        try:
            empty_spot_meta = {"universe": [], "tokens": []}
            self._info = Info(
                constants.MAINNET_API_URL,
                skip_ws=True,
                spot_meta=empty_spot_meta,
                timeout=self.execution_timeout_seconds,
            )
            logger.info(
                "📡 Hyperliquid read client ready | network=mainnet | address=%s | dry_run=%s",
                _mask_address(self.read_address) if self.read_address else "missing",
                self.dry_run,
            )
        except Exception:
            logger.exception("❌ Hyperliquid read client init failed | falling back to placeholders")
            self._info = None

        if self.dry_run:
            logger.info("🧪 Hyperliquid execution mode | mode=dry-run | real_orders_enabled=no")
        elif self.shadow_mode:
            logger.info("👥 Hyperliquid execution mode | mode=shadow | real_orders_enabled=no")
        else:
            self._init_live_exchange()

    def _init_live_exchange(self) -> None:
        if self._exchange is not None:
            return
        if not _HL_SDK_AVAILABLE:
            logger.error("❌ Live execution unavailable | Hyperliquid SDK missing")
            return
        if not self.private_key:
            logger.error("❌ Live execution unavailable | HYPERLIQUID_PRIVATE_KEY missing")
            return
        if not self.account_address:
            logger.error("❌ Live execution unavailable | HYPERLIQUID_ACCOUNT_ADDRESS missing")
            return

        try:
            wallet = Account.from_key(self.private_key)
            self.signer_address = _normalize_address(getattr(wallet, "address", ""))
            using_vault = bool(self.vault_address)
            signer_matches_account = bool(self.signer_address) and self.signer_address == self.account_address
            signer_matches_vault = bool(self.signer_address and self.vault_address) and self.signer_address == self.vault_address

            logger.info(
                "🔐 Hyperliquid signer check | signer=%s | account=%s | vault=%s | using_vault=%s | signer_matches_account=%s | signer_matches_vault=%s | allow_different_signer=%s | block_on_signer_mismatch=%s",
                _mask_address(self.signer_address) if self.signer_address else "missing",
                _mask_address(self.account_address),
                _mask_address(self.vault_address) if self.vault_address else "none",
                "yes" if using_vault else "no",
                "yes" if signer_matches_account else "no",
                "yes" if signer_matches_vault else "no",
                "yes" if self.allow_different_signer else "no",
                "yes" if self.block_on_signer_mismatch else "no",
            )

            direct_account_signer_mismatch = (not using_vault) and (not signer_matches_account)
            if direct_account_signer_mismatch:
                logger.warning(
                    "⚠️ Hyperliquid direct-account signer mismatch detected | signer=%s | account=%s | vault=none",
                    _mask_address(self.signer_address) if self.signer_address else "missing",
                    _mask_address(self.account_address),
                )
                if self.block_on_signer_mismatch and not self.allow_different_signer:
                    logger.error(
                        "❌ Live execution blocked | signer/account mismatch on direct account mode and DEPLOY_FAILSAFE_BLOCK_ON_SIGNER_MISMATCH=true"
                    )
                    self._exchange = None
                    return

            self._exchange = Exchange(
                wallet,
                constants.MAINNET_API_URL,
                vault_address=self.vault_address or None,
                account_address=self.account_address,
                timeout=self.execution_timeout_seconds,
            )
            logger.warning(
                "🚨 Hyperliquid execution mode | mode=live | real_orders_enabled=yes | signer=%s | account=%s | vault=%s | "
                "using_vault=%s | live_size_cap=%s | max_order_notional_usdc=%s",
                _mask_address(self.signer_address) if self.signer_address else "missing",
                _mask_address(self.account_address),
                _mask_address(self.vault_address) if self.vault_address else "none",
                "yes" if using_vault else "no",
                f"{self.live_initial_size_multiplier_cap:.2f}",
                f"{self.live_max_order_notional_usdc:.2f}",
            )
        except Exception:
            logger.exception("❌ Live exchange init failed | real orders disabled")
            self._exchange = None

    def _global_state(self) -> Tuple[Dict[str, Any], List[Dict[str, Any]]] | None:
        if self._info is None:
            return None

        now = time.time()
        if self._global_state_cache is not None and (now - self._global_state_timestamp) < self._global_state_ttl_seconds:
            return self._global_state_cache

        try:
            data = call_with_rate_limit_retry(
                self._info.meta_and_asset_ctxs,
                logger=logger,
                operation="meta_and_asset_ctxs",
            )
            if isinstance(data, (list, tuple)) and len(data) >= 2:
                universe = data[0] if isinstance(data[0], dict) else {}
                contexts = data[1] if isinstance(data[1], list) else []
                self._global_state_cache = (universe, contexts)
                self._global_state_timestamp = now
                return self._global_state_cache
        except Exception:
            if self._global_state_cache is not None:
                logger.warning(
                    "⚠️ Hyperliquid meta_and_asset_ctxs failed | using stale cache | age_s=%.1f",
                    now - self._global_state_timestamp,
                )
                return self._global_state_cache
            logger.exception("❌ Hyperliquid meta_and_asset_ctxs failed")

        return None

    def _all_mids(self) -> Dict[str, Any]:
        if self._info is None:
            return {}

        now = time.time()
        if self._all_mids_cache and (now - self._all_mids_timestamp) < self._all_mids_ttl_seconds:
            return self._all_mids_cache

        try:
            data = call_with_rate_limit_retry(
                self._info.all_mids,
                logger=logger,
                operation="all_mids",
            )
            if isinstance(data, dict):
                self._all_mids_cache = data
                self._all_mids_timestamp = now
                return self._all_mids_cache
        except Exception:
            if self._all_mids_cache:
                logger.warning(
                    "⚠️ Hyperliquid all_mids failed | using stale cache | age_s=%.1f",
                    now - self._all_mids_timestamp,
                )
                return self._all_mids_cache
            logger.exception("❌ Hyperliquid all_mids failed")

        return {}

    def _orderbook_snapshot(self, asset: str) -> Dict[str, Any]:
        if self._info is None:
            return {}

        now = time.time()
        cached = self._orderbook_cache.get(asset)
        if cached is not None and (now - self._orderbook_timestamp.get(asset, 0.0)) < self._orderbook_ttl_seconds:
            return cached

        try:
            snapshot = call_with_rate_limit_retry(
                lambda: self._info.l2_snapshot(asset),
                logger=logger,
                operation=f"l2_snapshot:{asset.upper()}",
            )
            if isinstance(snapshot, dict):
                self._orderbook_cache[asset] = snapshot
                self._orderbook_timestamp[asset] = now
                return snapshot
        except Exception:
            if cached is not None:
                logger.warning(
                    "⚠️ Hyperliquid l2_snapshot failed | asset=%s | using stale cache | age_s=%.1f",
                    asset,
                    now - self._orderbook_timestamp.get(asset, 0.0),
                )
                return cached
            logger.exception("❌ Hyperliquid l2_snapshot failed | asset=%s", asset)

        return {}

    def _get_asset_meta(self, asset: str) -> Dict[str, Any]:
        state = self._global_state()
        if state is None:
            return {}

        universe, _contexts = state
        universe_list = universe.get("universe", []) if isinstance(universe, dict) else []
        if not isinstance(universe_list, list):
            return {}

        for meta in universe_list:
            if isinstance(meta, dict) and str(meta.get("name", "")).upper() == asset.upper():
                return meta
        return {}

    def _get_asset_ctx(self, asset: str) -> Dict[str, Any]:
        state = self._global_state()
        if state is None:
            return {}

        universe, contexts = state
        universe_list = universe.get("universe", []) if isinstance(universe, dict) else []
        if not isinstance(universe_list, list):
            return {}

        for idx, meta in enumerate(universe_list):
            if isinstance(meta, dict) and str(meta.get("name", "")).upper() == asset.upper():
                if idx < len(contexts) and isinstance(contexts[idx], dict):
                    return contexts[idx]
                break
        return {}

    def _spread_bps(self, asset: str, mark_price: float) -> float:
        snapshot = self._orderbook_snapshot(asset)
        levels = snapshot.get("levels") if isinstance(snapshot, dict) else None
        if not isinstance(levels, list) or len(levels) < 2:
            return 0.0

        bids = levels[0] or []
        asks = levels[1] or []
        best_bid = _safe_float(bids[0].get("px")) if bids else 0.0
        best_ask = _safe_float(asks[0].get("px")) if asks else 0.0

        reference_price = mark_price if mark_price > 0 else ((best_bid + best_ask) / 2.0 if best_bid > 0 and best_ask > 0 else 0.0)
        if reference_price <= 0 or best_bid <= 0 or best_ask <= 0:
            return 0.0

        return max(0.0, ((best_ask - best_bid) / reference_price) * 10_000.0)

    def _open_interest_delta_1h(self, asset: str, open_interest: float) -> float:
        if open_interest <= 0:
            return 0.0

        now = time.time()
        history = self._oi_history.setdefault(asset.upper(), [])
        history.append((now, open_interest))
        cutoff = now - (2 * 60 * 60)
        self._oi_history[asset.upper()] = [(ts, value) for ts, value in history if ts >= cutoff]
        history = self._oi_history[asset.upper()]

        baseline: Tuple[float, float] | None = None
        for sample in history:
            age = now - sample[0]
            if age >= 55 * 60:
                baseline = sample
                break

        if baseline is None:
            return 0.0

        previous_oi = baseline[1]
        if previous_oi <= 0:
            return 0.0

        return ((open_interest - previous_oi) / previous_oi) * 100.0

    def _infer_regime_hint(self, mark_price: float, spread_bps: float, funding_rate: float, open_interest_delta_1h: float) -> str:
        if mark_price <= 0:
            return "unknown"
        if spread_bps >= 8.0:
            return "stressed"
        if abs(funding_rate) >= 0.0005 or abs(open_interest_delta_1h) >= 5.0:
            return "momentum"
        return "balanced"

    def get_account_state(self) -> Dict[str, Any]:
        fallback = {
            "equity": 1000.0,
            "available_margin": 1000.0,
            "open_positions": [],
        }

        if self._info is None:
            return fallback

        if not self.read_address:
            logger.warning("⚠️ HYPERLIQUID_ACCOUNT_ADDRESS missing | using placeholder account state")
            return fallback

        try:
            user_state = self._info.user_state(self.read_address)
            margin = user_state.get("marginSummary", {}) if isinstance(user_state, dict) else {}
            equity = _safe_float(margin.get("accountValue", 0.0))
            margin_used = _safe_float(margin.get("totalMarginUsed", 0.0))
            withdrawable_raw = None
            if isinstance(user_state, dict):
                withdrawable_raw = user_state.get("withdrawable")
            if withdrawable_raw is None and isinstance(margin, dict):
                withdrawable_raw = margin.get("withdrawable")
            derived_available = max(0.0, equity - margin_used)
            withdrawable = _safe_float(withdrawable_raw, derived_available)
            available_margin = min(withdrawable, derived_available) if withdrawable_raw is not None else derived_available

            mids = self._all_mids()
            open_positions: List[Dict[str, Any]] = []
            for item in user_state.get("assetPositions", []) if isinstance(user_state, dict) else []:
                position = item.get("position", item) if isinstance(item, dict) else {}
                coin = str(position.get("coin", "")).upper()
                size_signed = _safe_float(position.get("szi", 0.0))
                if not coin or size_signed == 0.0:
                    continue

                entry_price = _safe_float(position.get("entryPx", 0.0))
                mark_price = _safe_float(mids.get(coin), entry_price)
                pnl_usd = _safe_float(position.get("unrealizedPnl", 0.0))
                if pnl_usd == 0.0 and entry_price > 0 and mark_price > 0:
                    pnl_usd = (mark_price - entry_price) * size_signed

                leverage = position.get("leverage", {}) if isinstance(position, dict) else {}
                open_positions.append(
                    {
                        "asset": coin,
                        "side": "long" if size_signed > 0 else "short",
                        "size": abs(size_signed),
                        "size_signed": size_signed,
                        "entry_price": entry_price,
                        "mark_price": mark_price,
                        "pnl_usd": pnl_usd,
                        "leverage": _safe_float(leverage.get("value", 0.0)),
                    }
                )

            return {
                "equity": equity if equity > 0 else fallback["equity"],
                "available_margin": available_margin if available_margin >= 0 else fallback["available_margin"],
                "open_positions": open_positions,
            }
        except Exception:
            logger.exception("❌ Hyperliquid user_state failed | using placeholder account state")
            return fallback

    def get_market_snapshot(self, asset: str) -> Dict[str, Any]:
        fallback = {
            "asset": asset,
            "mark_price": 0.0,
            "spread_bps": 2.0,
            "funding_rate": 0.0,
            "open_interest_delta_1h": 0.0,
            "regime_hint": "unknown",
        }

        asset = asset.upper()

        if self._info is None:
            return fallback

        ctx = self._get_asset_ctx(asset)
        mark_price = _safe_float(ctx.get("markPx", 0.0))
        if mark_price <= 0:
            mids = self._all_mids()
            mark_price = _safe_float(mids.get(asset), 0.0)

        funding_rate = _safe_float(ctx.get("funding", 0.0))
        open_interest = _safe_float(ctx.get("openInterest", 0.0))
        spread_bps = self._spread_bps(asset, mark_price)
        open_interest_delta_1h = self._open_interest_delta_1h(asset, open_interest)
        regime_hint = self._infer_regime_hint(mark_price, spread_bps, funding_rate, open_interest_delta_1h)

        return {
            "asset": asset,
            "mark_price": mark_price if mark_price > 0 else fallback["mark_price"],
            "spread_bps": spread_bps if spread_bps >= 0 else fallback["spread_bps"],
            "funding_rate": funding_rate,
            "open_interest_delta_1h": open_interest_delta_1h,
            "regime_hint": regime_hint,
        }

    def _size_decimals(self, asset: str) -> int:
        meta = self._get_asset_meta(asset)
        value = meta.get("szDecimals", 0) if isinstance(meta, dict) else 0
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    def _round_size_down(self, asset: str, raw_size: float) -> float:
        sz_decimals = self._size_decimals(asset)
        step = 10 ** sz_decimals
        rounded = math.floor(max(raw_size, 0.0) * step) / step
        if sz_decimals == 0:
            return float(int(rounded))
        return float(f"{rounded:.{sz_decimals}f}")

    def _find_open_position(self, asset: str) -> Dict[str, Any] | None:
        account_state = self.get_account_state()
        open_positions = account_state.get("open_positions", [])
        if not isinstance(open_positions, list):
            return None

        asset = str(asset).upper()
        for position in open_positions:
            if not isinstance(position, dict):
                continue
            if str(position.get("asset", "")).upper() != asset:
                continue

            size = abs(_safe_float(position.get("size", position.get("size_signed", 0.0))))
            if size <= 0:
                continue

            size_signed = _safe_float(position.get("size_signed", 0.0))
            side = str(position.get("side", "flat")).lower()
            if side not in {"long", "short"}:
                if size_signed > 0:
                    side = "long"
                elif size_signed < 0:
                    side = "short"
                else:
                    continue

            return {
                "asset": asset,
                "side": side,
                "size": size,
                "size_signed": size_signed,
                "entry_price": _safe_float(position.get("entry_price", 0.0)),
                "mark_price": _safe_float(position.get("mark_price", 0.0)),
                "pnl_usd": _safe_float(position.get("pnl_usd", 0.0)),
                "leverage": _safe_float(position.get("leverage", 0.0)),
            }
        return None

    def _build_exit_order_plan(
        self,
        *,
        asset: str,
        action: str,
        size_multiplier: float,
        mark_price: float,
    ) -> Dict[str, Any]:
        position = self._find_open_position(asset)
        if position is None:
            return {"ok": False, "error": f"missing_open_position:{asset}"}

        position_size = _safe_float(position.get("size", 0.0))
        if position_size <= 0:
            return {"ok": False, "error": "non_positive_position_size"}

        if action == "CLOSE":
            exit_fraction = 1.0
        else:
            if size_multiplier <= 0:
                return {"ok": False, "error": "non_positive_reduce_size_multiplier"}
            exit_fraction = min(size_multiplier, 1.0)

        raw_size = position_size * exit_fraction
        rounded_size = self._round_size_down(asset, raw_size)
        if rounded_size <= 0:
            return {
                "ok": False,
                "error": "rounded_size_zero",
                "asset": asset,
                "mark_price": mark_price,
                "requested_notional_usdc": raw_size * mark_price,
            }

        side = str(position.get("side", "flat")).lower()
        if side == "long":
            is_buy = False
        elif side == "short":
            is_buy = True
        else:
            return {"ok": False, "error": f"unsupported_position_side:{side}"}

        return {
            "ok": True,
            "asset": asset,
            "action": action,
            "is_buy": is_buy,
            "mark_price": mark_price,
            "size_multiplier": size_multiplier,
            "effective_size_multiplier": exit_fraction,
            "requested_notional_usdc": raw_size * mark_price,
            "order_notional_usdc": rounded_size * mark_price,
            "raw_size": raw_size,
            "rounded_size": rounded_size,
            "sz_decimals": self._size_decimals(asset),
            "slippage": self.live_order_slippage,
            "is_exit": True,
            "position_side": side,
            "position_size": position_size,
        }

    def _build_order_plan(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        asset = str(payload.get("asset", "")).upper()
        action = str(payload.get("action", "")).upper()
        size_multiplier = max(0.0, _safe_float(payload.get("size_multiplier", 0.0)))

        if not asset:
            return {"ok": False, "error": "missing_asset"}
        if action not in {"ENTER_LONG", "ENTER_SHORT", "REDUCE", "CLOSE"}:
            return {"ok": False, "error": f"unsupported_action:{action}"}

        market = self.get_market_snapshot(asset)
        mark_price = _safe_float(market.get("mark_price", 0.0))
        if mark_price <= 0:
            position = self._find_open_position(asset)
            if position is not None:
                mark_price = max(
                    _safe_float(position.get("mark_price", 0.0)),
                    _safe_float(position.get("entry_price", 0.0)),
                )
        if mark_price <= 0:
            return {"ok": False, "error": "missing_mark_price"}

        if action in {"REDUCE", "CLOSE"}:
            return self._build_exit_order_plan(
                asset=asset,
                action=action,
                size_multiplier=size_multiplier,
                mark_price=mark_price,
            )

        effective_multiplier = min(size_multiplier, self.live_initial_size_multiplier_cap)
        desired_notional = self.base_equity_usdc * effective_multiplier
        order_notional_usdc = min(desired_notional, self.live_max_order_notional_usdc)
        if order_notional_usdc <= 0:
            return {"ok": False, "error": "non_positive_notional"}

        raw_size = order_notional_usdc / mark_price
        rounded_size = self._round_size_down(asset, raw_size)
        if rounded_size <= 0:
            return {
                "ok": False,
                "error": "rounded_size_zero",
                "asset": asset,
                "mark_price": mark_price,
                "requested_notional_usdc": order_notional_usdc,
            }

        return {
            "ok": True,
            "asset": asset,
            "action": action,
            "is_buy": action == "ENTER_LONG",
            "mark_price": mark_price,
            "size_multiplier": size_multiplier,
            "effective_size_multiplier": effective_multiplier,
            "requested_notional_usdc": desired_notional,
            "order_notional_usdc": order_notional_usdc,
            "raw_size": raw_size,
            "rounded_size": rounded_size,
            "sz_decimals": self._size_decimals(asset),
            "slippage": self.live_order_slippage,
            "is_exit": False,
        }

    def _extract_order_status(self, response: Dict[str, Any]) -> Tuple[str, str]:
        if not isinstance(response, dict):
            return "unknown", ""

        raw_status = str(response.get("status", "unknown")).strip().lower()
        status_aliases = {
            "ok": "ok",
            "success": "ok",
            "filled": "filled",
            "resting": "resting",
            "err": "error",
            "error": "error",
            "blocked": "blocked",
            "unknown": "unknown",
        }
        status = status_aliases.get(raw_status, raw_status or "unknown")
        details: List[str] = []

        def add_detail(value: Any) -> None:
            if value is None:
                return
            text = _mask_error(str(value).strip())
            if not text:
                return
            if text in {"ok", "success", "unknown", "n/a"}:
                return
            if text not in details:
                details.append(text)

        def walk(value: Any, depth: int = 0) -> None:
            if depth > 5 or len(details) >= 10:
                return
            if isinstance(value, dict):
                for key, item in value.items():
                    key_lower = str(key).lower()
                    if key_lower in {"error", "err", "message", "msg", "detail", "response"} and not isinstance(item, (dict, list, tuple)):
                        add_detail(item)
                    walk(item, depth + 1)
                return
            if isinstance(value, (list, tuple)):
                for item in value:
                    walk(item, depth + 1)
                return
            if isinstance(value, str):
                lowered = value.lower()
                if any(token in lowered for token in ("error", "reject", "invalid", "insufficient", "cancel", "failed", "margin", "notional", "reduce only")):
                    add_detail(value)

        response_obj = response.get("response", {})
        data = response_obj.get("data", {}) if isinstance(response_obj, dict) else {}
        statuses = data.get("statuses", []) if isinstance(data, dict) else []
        if not statuses and isinstance(response.get("statuses"), list):
            statuses = response.get("statuses", [])

        if raw_status not in {"ok", "success"}:
            status = status_aliases.get(raw_status, raw_status or "unknown")
            for key in ("error", "err", "message", "msg", "detail"):
                if key in response:
                    add_detail(response.get(key))
            if isinstance(statuses, list) and statuses:
                walk(statuses)
            else:
                response_without_status = {key: value for key, value in response.items() if key != "status"}
                walk(response_without_status)
            detail = " | ".join(details[:4])
            return status, detail

        if isinstance(statuses, list) and statuses:
            first = statuses[0]
            if isinstance(first, dict):
                inline_error = first.get("error") or first.get("err")
                if inline_error:
                    add_detail(inline_error)
                    walk(first)
                    return "error", " | ".join(details[:4])

                if "filled" in first:
                    filled = first.get("filled", {}) or {}
                    avg_px = _mask_error(str(filled.get("avgPx", "")).strip())
                    oid = _mask_error(str(filled.get("oid", "")).strip())
                    filled_qty = _mask_error(str(filled.get("totalSz") or filled.get("sz") or filled.get("filledSz") or "").strip())
                    detail_parts = [part for part in [f"avgPx={avg_px}" if avg_px else "", f"oid={oid}" if oid else "", f"filledSz={filled_qty}" if filled_qty else ""] if part]
                    return "filled", " | ".join(detail_parts)

                if "resting" in first:
                    resting = first.get("resting", {}) or {}
                    oid = _mask_error(str(resting.get("oid", "")).strip())
                    return "resting", f"oid={oid}" if oid else ""

                first_status = str(first.get("status", "")).strip().lower()
                aliased = status_aliases.get(first_status)
                if aliased in {"error", "blocked", "filled", "resting"}:
                    walk(first)
                    detail = " | ".join(details[:4])
                    return aliased, detail

                walk(first)
            elif isinstance(first, str):
                lowered = first.strip().lower()
                if lowered and lowered not in {"ok", "success"}:
                    add_detail(first)
                    return "error", " | ".join(details[:4])

        walk(response)

        if "error" in response and status == "ok":
            status = "error"

        detail = " | ".join(details[:4])
        return status, detail

    def place_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        requested_mode = "live"
        if self.dry_run:
            requested_mode = "dry-run"
        elif self.shadow_mode:
            requested_mode = "shadow"

        logger.info(
            "📡 Hyperliquid order request | asset=%s | action=%s | size_multiplier=%s | mode=%s",
            payload.get("asset", "unknown"),
            payload.get("action", "unknown"),
            payload.get("size_multiplier", "n/a"),
            requested_mode,
        )

        plan = self._build_order_plan(payload)
        if not plan.get("ok", False):
            error = str(plan.get("error", "order_plan_failed"))
            logger.warning(
                "⚠️ Hyperliquid order skipped | asset=%s | mode=%s | error=%s",
                payload.get("asset", "unknown"),
                requested_mode,
                error,
            )
            return {
                "accepted": False,
                "dry_run": self.dry_run,
                "shadow_mode": self.shadow_mode,
                "mode": requested_mode,
                "simulated": self.dry_run or self.shadow_mode,
                "sent_to_exchange": False,
                "order_status": "skipped",
                "error": error,
                "plan": plan,
            }

        if self.dry_run:
            logger.info(
                "🧪 Simulated order only | asset=%s | action=%s | rounded_size=%s | notional_usdc=%s | mode=dry-run",
                plan["asset"],
                plan["action"],
                plan["rounded_size"],
                f'{plan["order_notional_usdc"]:.2f}',
            )
            return {
                "accepted": True,
                "dry_run": True,
                "shadow_mode": self.shadow_mode,
                "mode": "dry-run",
                "simulated": True,
                "sent_to_exchange": False,
                "order_status": "simulated_dry_run",
                "error": "",
                "plan": plan,
            }

        if self.shadow_mode:
            logger.info(
                "👥 Shadow order only | asset=%s | action=%s | rounded_size=%s | notional_usdc=%s | real_order_sent=no",
                plan["asset"],
                plan["action"],
                plan["rounded_size"],
                f'{plan["order_notional_usdc"]:.2f}',
            )
            return {
                "accepted": True,
                "dry_run": False,
                "shadow_mode": True,
                "mode": "shadow",
                "simulated": True,
                "sent_to_exchange": False,
                "order_status": "simulated_shadow",
                "error": "",
                "plan": plan,
            }

        self._init_live_exchange()
        if self._exchange is None:
            error = "live_exchange_unavailable"
            logger.error(
                "❌ Live order blocked | asset=%s | action=%s | error=%s",
                plan["asset"],
                plan["action"],
                error,
            )
            return {
                "accepted": False,
                "dry_run": False,
                "shadow_mode": False,
                "mode": "live",
                "simulated": False,
                "sent_to_exchange": False,
                "order_status": "blocked",
                "error": error,
                "plan": plan,
            }

        try:
            response = self._exchange.market_open(
                plan["asset"],
                plan["is_buy"],
                plan["rounded_size"],
                slippage=plan["slippage"],
            )
            order_status, detail = self._extract_order_status(response if isinstance(response, dict) else {})
            accepted_statuses = {"filled", "resting", "ok"}
            accepted = order_status in accepted_statuses
            response_excerpt = _stringify_for_log(response)
            error_message = ""
            if not accepted:
                error_message = detail or f"order_status:{order_status}"
            logger.warning(
                "🚨 Live order exchange response | asset=%s | action=%s | rounded_size=%s | notional_usdc=%s | order_status=%s | detail=%s | response=%s",
                plan["asset"],
                plan["action"],
                plan["rounded_size"],
                f'{plan["order_notional_usdc"]:.2f}',
                order_status,
                detail or "n/a",
                response_excerpt,
            )
            return {
                "accepted": accepted,
                "dry_run": False,
                "shadow_mode": False,
                "mode": "live",
                "simulated": False,
                "sent_to_exchange": True,
                "order_status": order_status,
                "error": error_message,
                "plan": plan,
                "response": response,
                "response_excerpt": response_excerpt,
            }
        except Exception as exc:
            message = _mask_error(str(exc))
            logger.exception(
                "❌ Live order exception | asset=%s | action=%s | error_type=%s | error=%s",
                plan["asset"],
                plan["action"],
                exc.__class__.__name__,
                message or exc.__class__.__name__,
            )
            return {
                "accepted": False,
                "dry_run": False,
                "shadow_mode": False,
                "mode": "live",
                "simulated": False,
                "sent_to_exchange": False,
                "order_status": "error",
                "error": message or exc.__class__.__name__,
                "error_type": exc.__class__.__name__,
                "plan": plan,
            }

    def cancel_all(self) -> Dict[str, Any]:
        logger.warning("🛑 Hyperliquid cancel_all invoked")
        return {"ok": True}
