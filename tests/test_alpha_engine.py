import math

from src.alpha_engine import AlphaDecisionEngine, AlphaFeatures


def test_missing_features_reduce_confidence_instead_of_fake_neutral():
    decision = AlphaDecisionEngine().evaluate(
        "MSFT",
        AlphaFeatures(trend=85, data_quality=90),
        current_price=420,
    )
    assert decision.confidence < 0.60
    assert decision.decision == "WAIT"
    assert decision.trade_plan.action == "WAIT"
    assert decision.opportunity_score is not None
    assert decision.limitations


def test_high_risk_vetoes_strong_opportunity():
    decision = AlphaDecisionEngine().evaluate(
        "NVDA",
        AlphaFeatures(
            trend=95,
            momentum=90,
            relative_strength=95,
            volume_confirmation=90,
            fundamental_quality=90,
            catalyst=90,
            market_regime=90,
            volatility_risk=95,
            event_risk=90,
            data_quality=95,
        ),
        current_price=180,
        support=170,
        resistance=210,
        atr=6,
    )
    assert decision.opportunity_score >= 75
    assert decision.risk_score >= 75
    assert decision.decision == "AVOID"
    assert decision.trade_plan.action == "AVOID"
    assert decision.trade_plan.max_position_pct == 0


def test_good_setup_has_bounded_position_and_positive_rr():
    decision = AlphaDecisionEngine().evaluate(
        "GOOGL",
        AlphaFeatures(
            trend=88,
            momentum=78,
            relative_strength=82,
            volume_confirmation=80,
            fundamental_quality=90,
            catalyst=75,
            market_regime=85,
            volatility_risk=25,
            event_risk=20,
            data_quality=95,
        ),
        current_price=200,
        support=194,
        resistance=220,
        atr=4,
    )
    assert decision.decision == "BUY_SETUP"
    assert decision.trade_plan.action == "BUY_SETUP"
    assert 0 < decision.trade_plan.max_position_pct <= 0.15
    assert decision.trade_plan.stop_loss < 200
    assert decision.trade_plan.targets[0] > 200
    assert decision.trade_plan.risk_reward >= 1.5


def test_low_rr_gate_downgrades_top_level_decision_to_wait():
    decision = AlphaDecisionEngine().evaluate(
        "MSFT",
        AlphaFeatures(
            trend=92,
            momentum=88,
            relative_strength=90,
            volume_confirmation=85,
            fundamental_quality=90,
            catalyst=85,
            market_regime=90,
            volatility_risk=20,
            event_risk=20,
            data_quality=95,
        ),
        current_price=100,
        support=95,
        resistance=102,
        atr=4,
    )
    assert decision.opportunity_score >= 75
    assert decision.trade_plan.risk_reward < 1.5
    assert decision.trade_plan.action == "WAIT"
    assert decision.decision == "WAIT"
    assert decision.trade_plan.max_position_pct == 0
    assert any("trade-plan gate downgraded" in item for item in decision.limitations)


def test_non_finite_values_are_treated_as_missing():
    decision = AlphaDecisionEngine().evaluate(
        "TEST",
        AlphaFeatures(trend=math.nan, momentum=math.inf, data_quality=80),
    )
    assert decision.decision == "WAIT"
    assert decision.trade_plan.action == "WAIT"
    assert decision.confidence < 0.60
