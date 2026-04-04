from app.services.outcome_evaluator_service import classify_outcome


def test_classify_outcome_correct_no_trade():
    label, score = classify_outcome(
        effective_action="NO_TRADE",
        position_side="flat",
        future_return_pct=0.04,
        neutral_band_pct=0.10,
    )
    assert label == "correct_no_trade"
    assert score == 0.5


def test_classify_outcome_missed_long_opportunity():
    label, score = classify_outcome(
        effective_action="NO_TRADE",
        position_side="flat",
        future_return_pct=0.35,
        neutral_band_pct=0.10,
    )
    assert label == "missed_long_opportunity"
    assert score == -0.5


def test_classify_outcome_good_close_long():
    label, score = classify_outcome(
        effective_action="CLOSE",
        position_side="long",
        future_return_pct=-0.40,
        neutral_band_pct=0.10,
    )
    assert label == "good_close_long"
    assert score == 0.75


def test_classify_outcome_hold_wrong_long():
    label, score = classify_outcome(
        effective_action="HOLD",
        position_side="long",
        future_return_pct=-0.30,
        neutral_band_pct=0.10,
    )
    assert label == "hold_was_wrong_long"
    assert score == -0.75
