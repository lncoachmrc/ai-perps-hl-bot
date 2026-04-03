from datetime import datetime, timezone

from app.core.enums import DecisionAction
from app.domain.decision import JudgeDecision
from app.risk.risk_gate import RiskGate
from app.settings import Settings


def _decision(action: DecisionAction = DecisionAction.ENTER_LONG, size_multiplier: float = 0.5) -> JudgeDecision:
    return JudgeDecision(
        action=action,
        confidence=0.8,
        size_multiplier=size_multiplier,
        ttl_minutes=30,
    )


def _settings(**overrides) -> Settings:
    settings = Settings()
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def test_risk_gate_allows_no_trade():
    gate = RiskGate(_settings())
    result = gate.evaluate(
        "BTC",
        _decision(DecisionAction.NO_TRADE),
        {"open_positions": []},
    )
    assert result.allowed is True
    assert result.final_action == "NO_TRADE"


def test_risk_gate_blocks_entry_when_daily_stop_is_hit():
    settings = _settings(daily_stop_pct=1.0, weekly_stop_pct=3.0, database_url="")
    gate = RiskGate(settings, now_fn=lambda: datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc))
    gate._baseline_cache[("day", "2026-04-03")] = 1000.0
    gate._baseline_cache[("week", "2026-W14")] = 1000.0

    result = gate.evaluate(
        "BTC",
        _decision(DecisionAction.ENTER_LONG),
        {"equity": 989.0, "open_positions": []},
    )

    assert result.allowed is False
    assert result.final_action == "NO_TRADE"
    assert result.reason == "daily_or_weekly_stop_reached"


def test_risk_gate_blocks_entry_when_weekly_stop_is_hit():
    settings = _settings(daily_stop_pct=1.0, weekly_stop_pct=3.0, database_url="")
    gate = RiskGate(settings, now_fn=lambda: datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc))
    gate._baseline_cache[("day", "2026-04-03")] = 990.0
    gate._baseline_cache[("week", "2026-W14")] = 1000.0

    result = gate.evaluate(
        "BTC",
        _decision(DecisionAction.ENTER_LONG),
        {"equity": 969.0, "open_positions": []},
    )

    assert result.allowed is False
    assert result.final_action == "NO_TRADE"
    assert result.reason == "daily_or_weekly_stop_reached"


def test_risk_gate_allows_close_even_when_stop_is_hit():
    settings = _settings(daily_stop_pct=1.0, weekly_stop_pct=3.0, database_url="")
    gate = RiskGate(settings, now_fn=lambda: datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc))
    gate._baseline_cache[("day", "2026-04-03")] = 1000.0
    gate._baseline_cache[("week", "2026-W14")] = 1000.0

    result = gate.evaluate(
        "BTC",
        _decision(DecisionAction.CLOSE, size_multiplier=0.0),
        {"equity": 989.0, "open_positions": []},
    )

    assert result.allowed is True
    assert result.final_action == "CLOSE"
    assert result.final_size_multiplier == 1.0
    assert result.reason == "stop_limit_exit_allowed"


def test_risk_gate_allows_reduce_even_when_stop_is_hit():
    settings = _settings(daily_stop_pct=1.0, weekly_stop_pct=3.0, database_url="")
    gate = RiskGate(settings, now_fn=lambda: datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc))
    gate._baseline_cache[("day", "2026-04-03")] = 1000.0
    gate._baseline_cache[("week", "2026-W14")] = 1000.0

    result = gate.evaluate(
        "BTC",
        _decision(DecisionAction.REDUCE, size_multiplier=0.5),
        {"equity": 989.0, "open_positions": []},
    )

    assert result.allowed is True
    assert result.final_action == "REDUCE"
    assert result.final_size_multiplier == 0.5
    assert result.reason == "stop_limit_exit_allowed"


def test_risk_gate_does_not_cap_reduce_in_shadow_mode():
    settings = _settings(dry_run=False, shadow_mode=True, database_url="")
    gate = RiskGate(settings)

    result = gate.evaluate(
        "SOL",
        _decision(DecisionAction.REDUCE, size_multiplier=0.5),
        {"equity": 1000.0, "open_positions": [{"asset": "SOL"}]},
    )

    assert result.allowed is True
    assert result.final_action == "REDUCE"
    assert result.final_size_multiplier == 0.5
    assert result.reason == "shadow_mode_exit_allowed"


def test_risk_gate_does_not_cap_close_in_shadow_mode():
    settings = _settings(dry_run=False, shadow_mode=True, database_url="")
    gate = RiskGate(settings)

    result = gate.evaluate(
        "SOL",
        _decision(DecisionAction.CLOSE, size_multiplier=0.0),
        {"equity": 1000.0, "open_positions": [{"asset": "SOL"}]},
    )

    assert result.allowed is True
    assert result.final_action == "CLOSE"
    assert result.final_size_multiplier == 1.0
    assert result.reason == "shadow_mode_exit_allowed"


def test_risk_gate_does_not_block_exit_when_max_open_positions_is_reached():
    settings = _settings(dry_run=False, shadow_mode=True, max_open_positions=1, database_url="")
    gate = RiskGate(settings)

    result = gate.evaluate(
        "SOL",
        _decision(DecisionAction.REDUCE, size_multiplier=0.5),
        {"equity": 1000.0, "open_positions": [{"asset": "SOL"}]},
    )

    assert result.allowed is True
    assert result.final_action == "REDUCE"
    assert result.final_size_multiplier == 0.5


def test_risk_gate_creates_in_memory_baseline_without_database():
    settings = _settings(daily_stop_pct=1.0, weekly_stop_pct=3.0, database_url="")
    gate = RiskGate(settings, now_fn=lambda: datetime(2026, 4, 3, 12, 0, tzinfo=timezone.utc))

    result = gate.evaluate(
        "BTC",
        _decision(DecisionAction.ENTER_LONG),
        {"equity": 1000.0, "open_positions": []},
    )

    assert result.allowed is True
    assert gate._baseline_cache[("day", "2026-04-03")] == 1000.0
    assert gate._baseline_cache[("week", "2026-W14")] == 1000.0
