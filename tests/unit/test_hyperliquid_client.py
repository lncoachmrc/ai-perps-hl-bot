import math

from app.exchange.hyperliquid.client import HyperliquidClient


def _client(*, dry_run=True, shadow_mode=True):
    client = HyperliquidClient(dry_run=dry_run, shadow_mode=shadow_mode)
    client._size_decimals = lambda asset: 4
    return client


def test_build_order_plan_close_uses_full_position_size_and_bypasses_entry_cap():
    client = _client(dry_run=False, shadow_mode=True)
    client.get_market_snapshot = lambda asset: {"asset": asset, "mark_price": 2000.0}
    client.get_account_state = lambda: {
        "equity": 1000.0,
        "available_margin": 800.0,
        "open_positions": [
            {
                "asset": "ETH",
                "side": "short",
                "size": 2.0,
                "size_signed": -2.0,
                "entry_price": 2050.0,
                "mark_price": 2000.0,
                "pnl_usd": 100.0,
                "leverage": 2.0,
            }
        ],
    }
    client.live_max_order_notional_usdc = 25.0

    plan = client._build_order_plan({"asset": "ETH", "action": "CLOSE", "size_multiplier": 0.0})

    assert plan["ok"] is True
    assert plan["action"] == "CLOSE"
    assert plan["position_side"] == "short"
    assert plan["is_buy"] is True
    assert math.isclose(plan["rounded_size"], 2.0)
    assert math.isclose(plan["order_notional_usdc"], 4000.0)
    assert plan["order_notional_usdc"] > client.live_max_order_notional_usdc


def test_build_order_plan_reduce_uses_fraction_of_open_position():
    client = _client(dry_run=False, shadow_mode=True)
    client.get_market_snapshot = lambda asset: {"asset": asset, "mark_price": 100.0}
    client.get_account_state = lambda: {
        "equity": 1000.0,
        "available_margin": 800.0,
        "open_positions": [
            {
                "asset": "SOL",
                "side": "long",
                "size": 10.0,
                "size_signed": 10.0,
                "entry_price": 90.0,
                "mark_price": 100.0,
                "pnl_usd": 100.0,
                "leverage": 2.0,
            }
        ],
    }

    plan = client._build_order_plan({"asset": "SOL", "action": "REDUCE", "size_multiplier": 0.25})

    assert plan["ok"] is True
    assert plan["action"] == "REDUCE"
    assert plan["position_side"] == "long"
    assert plan["is_buy"] is False
    assert math.isclose(plan["rounded_size"], 2.5)
    assert math.isclose(plan["effective_size_multiplier"], 0.25)
    assert math.isclose(plan["order_notional_usdc"], 250.0)


def test_place_order_close_live_calls_market_open_with_exit_side_and_full_size():
    client = _client(dry_run=False, shadow_mode=False)
    client.get_market_snapshot = lambda asset: {"asset": asset, "mark_price": 2500.0}
    client.get_account_state = lambda: {
        "equity": 1000.0,
        "available_margin": 800.0,
        "open_positions": [
            {
                "asset": "ETH",
                "side": "long",
                "size": 0.4,
                "size_signed": 0.4,
                "entry_price": 2450.0,
                "mark_price": 2500.0,
                "pnl_usd": 20.0,
                "leverage": 2.0,
            }
        ],
    }

    captured = {}

    class _StubExchange:
        def market_open(self, asset, is_buy, size, slippage):
            captured["asset"] = asset
            captured["is_buy"] = is_buy
            captured["size"] = size
            captured["slippage"] = slippage
            return {"status": "ok", "response": {"data": {"statuses": [{"filled": {"avgPx": "2500", "oid": "1"}}]}}}

    client._exchange = _StubExchange()

    result = client.place_order({"asset": "ETH", "action": "CLOSE", "size_multiplier": 0.0})

    assert result["accepted"] is True
    assert result["sent_to_exchange"] is True
    assert captured["asset"] == "ETH"
    assert captured["is_buy"] is False
    assert math.isclose(captured["size"], 0.4)


def test_place_order_reduce_rejects_when_open_position_is_missing():
    client = _client(dry_run=False, shadow_mode=True)
    client.get_market_snapshot = lambda asset: {"asset": asset, "mark_price": 100.0}
    client.get_account_state = lambda: {
        "equity": 1000.0,
        "available_margin": 1000.0,
        "open_positions": [],
    }

    result = client.place_order({"asset": "BTC", "action": "REDUCE", "size_multiplier": 0.5})

    assert result["accepted"] is False
    assert result["sent_to_exchange"] is False
    assert result["error"] == "missing_open_position:BTC"


def test_place_order_live_err_response_is_rejected():
    client = _client(dry_run=False, shadow_mode=False)
    client.get_market_snapshot = lambda asset: {"asset": asset, "mark_price": 2050.0}
    client.get_account_state = lambda: {
        "equity": 1000.0,
        "available_margin": 800.0,
        "open_positions": [],
    }

    class _StubExchange:
        def market_open(self, asset, is_buy, size, slippage):
            return {"status": "err", "response": {"data": {"statuses": [{"error": "exchange_rejected"}]}}}

    client._exchange = _StubExchange()

    result = client.place_order({"asset": "ETH", "action": "ENTER_SHORT", "size_multiplier": 0.1})

    assert result["accepted"] is False
    assert result["sent_to_exchange"] is True
    assert result["order_status"] == "error"
    assert result["error"] == "exchange_rejected"


def test_place_order_live_unknown_response_is_rejected():
    client = _client(dry_run=False, shadow_mode=False)
    client.get_market_snapshot = lambda asset: {"asset": asset, "mark_price": 2050.0}
    client.get_account_state = lambda: {
        "equity": 1000.0,
        "available_margin": 800.0,
        "open_positions": [],
    }

    class _StubExchange:
        def market_open(self, asset, is_buy, size, slippage):
            return {"status": "unknown"}

    client._exchange = _StubExchange()

    result = client.place_order({"asset": "ETH", "action": "ENTER_SHORT", "size_multiplier": 0.1})

    assert result["accepted"] is False
    assert result["sent_to_exchange"] is True
    assert result["order_status"] == "unknown"
    assert result["error"] == "order_status:unknown"
