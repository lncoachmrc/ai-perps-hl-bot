import sys
import types

_fake_psycopg2 = types.ModuleType("psycopg2")
_fake_psycopg2.connect = None
_fake_psycopg2_extras = types.ModuleType("psycopg2.extras")
_fake_psycopg2_extras.Json = lambda value: value
_fake_psycopg2.extras = _fake_psycopg2_extras
sys.modules.setdefault("psycopg2", _fake_psycopg2)
sys.modules.setdefault("psycopg2.extras", _fake_psycopg2_extras)

_fake_openai = types.ModuleType("openai")
class _OpenAI:
    def __init__(self, *args, **kwargs):
        pass
_fake_openai.OpenAI = _OpenAI
sys.modules.setdefault("openai", _fake_openai)

from app.core.enums import DecisionAction
from app.domain.decision import JudgeDecision
from app.domain.dossier import DecisionDossier
from app.strategy import orchestrator as orchestrator_module
from app.settings import Settings
from app.risk.risk_gate import RiskGateResult


def _settings(**overrides) -> Settings:
    settings = Settings()
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


class _StubQuantExpert:
    def evaluate(self, market):
        return {
            "setup_score": 0.10,
            "signal_strength": 0.10,
            "regime": "trend_down",
            "p_up": 0.40,
            "p_down": 0.60,
        }


class _StubProphetExpert:
    def evaluate(self, market):
        return {
            "trend_bias": "neutral",
            "forecast_delta_4h": 0.0,
            "interval_width": "medium",
            "changepoint_stress": "low",
        }


class _StubNewsExpert:
    def evaluate(self, asset):
        return {
            "impact": "low",
            "direction": "neutral",
            "headline_conflict": False,
            "tradability_flag": "allowed",
            "freshness_minutes": 5,
        }


class _StubJudgeLLM:
    def __init__(self, enabled, model):
        self.enabled = enabled
        self.model = model
        self.calls = []

    def decide(self, dossier):
        self.calls.append(dossier)
        return JudgeDecision(
            action=DecisionAction.HOLD,
            confidence=0.8,
            size_multiplier=0.0,
            ttl_minutes=30,
            reasons=["existing position management path"],
        )


class _StubRiskGate:
    def __init__(self, settings):
        self.settings = settings

    def evaluate(self, asset, decision, account_state):
        return RiskGateResult(
            allowed=True,
            final_action=decision.action.value,
            final_size_multiplier=0.0,
            reason="test_allowed",
        )


class _StubJournalService:
    def append(self, payload):
        return None


class _CapturingBuilder:
    def __init__(self):
        self.calls = []

    def build(
        self,
        asset,
        market_state,
        quant_expert,
        prophet_expert,
        news_expert,
        position_state,
        execution_context,
    ):
        self.calls.append(
            {
                "asset": asset,
                "position_state": position_state,
            }
        )
        return DecisionDossier(
            timestamp="2026-04-03T00:00:00+00:00",
            asset=asset,
            market_state=market_state,
            quant_expert=quant_expert,
            prophet_expert=prophet_expert,
            news_expert=news_expert,
            position_state=position_state,
            execution_context=execution_context,
        )


def test_orchestrator_passes_dry_run_and_shadow_mode_to_exchange(monkeypatch):
    captured = {}

    class _StubExchange:
        def __init__(self, dry_run, shadow_mode):
            captured["dry_run"] = dry_run
            captured["shadow_mode"] = shadow_mode

    monkeypatch.setattr(orchestrator_module, "HyperliquidClient", _StubExchange)
    monkeypatch.setattr(orchestrator_module, "QuantExpert", _StubQuantExpert)
    monkeypatch.setattr(orchestrator_module, "ProphetExpert", _StubProphetExpert)
    monkeypatch.setattr(orchestrator_module, "NewsExpert", _StubNewsExpert)
    monkeypatch.setattr(orchestrator_module, "DecisionDossierBuilder", _CapturingBuilder)
    monkeypatch.setattr(orchestrator_module, "JudgeLLM", _StubJudgeLLM)
    monkeypatch.setattr(orchestrator_module, "RiskGate", _StubRiskGate)
    monkeypatch.setattr(orchestrator_module, "JournalService", _StubJournalService)

    settings = _settings(dry_run=False, shadow_mode=False)
    orchestrator_module.Orchestrator(settings)

    assert captured == {"dry_run": False, "shadow_mode": False}


def test_orchestrator_builds_real_position_state_from_account_open_positions(monkeypatch):
    class _StubExchange:
        def __init__(self, dry_run, shadow_mode):
            self.dry_run = dry_run
            self.shadow_mode = shadow_mode

        def get_account_state(self):
            return {
                "equity": 1000.0,
                "available_margin": 900.0,
                "open_positions": [
                    {
                        "asset": "ETH",
                        "side": "short",
                        "size": 0.25,
                        "size_signed": -0.25,
                        "entry_price": 2050.0,
                        "mark_price": 2040.0,
                        "pnl_usd": 2.5,
                        "leverage": 2.0,
                    }
                ],
            }

        def get_market_snapshot(self, asset):
            return {
                "asset": asset,
                "mark_price": 2040.0,
                "spread_bps": 0.5,
                "funding_rate": 0.0,
                "open_interest_delta_1h": 0.0,
                "regime_hint": "balanced",
            }

        def place_order(self, payload):
            raise AssertionError("place_order should not be called in this test")

    monkeypatch.setattr(orchestrator_module, "HyperliquidClient", _StubExchange)
    monkeypatch.setattr(orchestrator_module, "QuantExpert", _StubQuantExpert)
    monkeypatch.setattr(orchestrator_module, "ProphetExpert", _StubProphetExpert)
    monkeypatch.setattr(orchestrator_module, "NewsExpert", _StubNewsExpert)
    monkeypatch.setattr(orchestrator_module, "DecisionDossierBuilder", _CapturingBuilder)
    monkeypatch.setattr(orchestrator_module, "JudgeLLM", _StubJudgeLLM)
    monkeypatch.setattr(orchestrator_module, "RiskGate", _StubRiskGate)
    monkeypatch.setattr(orchestrator_module, "JournalService", _StubJournalService)

    settings = _settings(universe_symbols=["ETH"], dry_run=False, shadow_mode=True)
    orchestrator = orchestrator_module.Orchestrator(settings)

    orchestrator.run_once()

    assert len(orchestrator.builder.calls) == 1
    position_state = orchestrator.builder.calls[0]["position_state"]
    assert position_state["asset"] == "ETH"
    assert position_state["side"] == "short"
    assert position_state["size"] == 0.25
    assert position_state["size_signed"] == -0.25
    assert position_state["entry_price"] == 2050.0
    assert position_state["mark_price"] == 2040.0
    assert position_state["pnl_usd"] == 2.5
    assert position_state["leverage"] == 2.0
    assert len(orchestrator.judge.calls) == 1


class _ExitJudgeLLM:
    def __init__(self, enabled, model):
        self.enabled = enabled
        self.model = model

    def decide(self, dossier):
        return JudgeDecision(
            action=DecisionAction.CLOSE,
            confidence=0.9,
            size_multiplier=0.0,
            ttl_minutes=5,
            reasons=["close open position for refresh test"],
        )


def test_orchestrator_refreshes_account_state_after_accepted_execution(monkeypatch):
    class _StubExchange:
        def __init__(self, dry_run, shadow_mode):
            self.dry_run = dry_run
            self.shadow_mode = shadow_mode
            self.account_state_calls = 0

        def get_account_state(self):
            self.account_state_calls += 1
            if self.account_state_calls == 1:
                return {
                    "equity": 1000.0,
                    "available_margin": 900.0,
                    "open_positions": [
                        {
                            "asset": "ETH",
                            "side": "short",
                            "size": 0.25,
                            "size_signed": -0.25,
                            "entry_price": 2050.0,
                            "mark_price": 2040.0,
                            "pnl_usd": 2.5,
                            "leverage": 2.0,
                        }
                    ],
                }
            return {
                "equity": 1002.0,
                "available_margin": 1002.0,
                "open_positions": [],
            }

        def get_market_snapshot(self, asset):
            return {
                "asset": asset,
                "mark_price": 2040.0,
                "spread_bps": 0.5,
                "funding_rate": 0.0,
                "open_interest_delta_1h": 0.0,
                "regime_hint": "balanced",
            }

        def place_order(self, payload):
            assert payload["action"] == "CLOSE"
            return {"accepted": True, "dry_run": False}

    monkeypatch.setattr(orchestrator_module, "HyperliquidClient", _StubExchange)
    monkeypatch.setattr(orchestrator_module, "QuantExpert", _StubQuantExpert)
    monkeypatch.setattr(orchestrator_module, "ProphetExpert", _StubProphetExpert)
    monkeypatch.setattr(orchestrator_module, "NewsExpert", _StubNewsExpert)
    monkeypatch.setattr(orchestrator_module, "DecisionDossierBuilder", _CapturingBuilder)
    monkeypatch.setattr(orchestrator_module, "JudgeLLM", _ExitJudgeLLM)
    monkeypatch.setattr(orchestrator_module, "RiskGate", _StubRiskGate)
    monkeypatch.setattr(orchestrator_module, "JournalService", _StubJournalService)

    settings = _settings(universe_symbols=["ETH"], dry_run=False, shadow_mode=True)
    orchestrator = orchestrator_module.Orchestrator(settings)

    orchestrator.run_once()

    assert orchestrator.exchange.account_state_calls == 2
