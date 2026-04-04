from app.services.market_observer_service import MarketObserverService


def test_market_observer_builds_observation_with_core_fields(tmp_path):
    service = MarketObserverService(path=str(tmp_path / "observations.jsonl"), database_url="")
    observation_id = service.record(
        asset="ETH",
        market={
            "mark_price": 2049.3,
            "spread_bps": 0.49,
            "funding_rate": 0.0,
            "open_interest_delta_1h": 0.0412,
            "regime_hint": "balanced",
        },
        dossier={
            "timestamp": "2026-04-04T09:37:49.364307+00:00",
            "position_state": {"side": "flat"},
            "quant_expert": {
                "setup_score": 0.1324,
                "signal_strength": 0.20,
                "p_up": 0.43,
                "p_down": 0.57,
                "expected_move_60m": 2.38,
                "invalidation_price": 2055.45,
            },
            "prophet_expert": {"trend_bias": "neutral", "forecast_delta_4h": -5.04},
            "news_expert": {"impact": "low", "direction": "neutral", "tradability_flag": "allowed"},
            "execution_context": {"fee_estimate_bps": 4.0, "slippage_estimate_bps": 2.0},
        },
        decision={"action": "NO_TRADE"},
        risk_gate={"final_action": "NO_TRADE"},
        execution_result=None,
        loop_count=19,
    )

    assert observation_id is None
    lines = (tmp_path / "observations.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert '"asset": "ETH"' in lines[0]
    assert '"cost_estimate_bps": 6.0' in lines[0]
