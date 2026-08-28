from __future__ import annotations

from src.alpha_engine.models import AlphaFeatures
from src.forecasting.decision import (
    ForecastDecisionPolicy,
    forecast_reliability_weight,
    momentum_continuation_score,
    reliability_aware_direction,
)
from src.forecasting.models import ForecastBundle, ForecastHorizon


def _horizon(
    days: int,
    *,
    probability: float,
    samples: int,
    status: str,
    hit_rate: float | None,
    expected_return: float = 0.5,
    alpha: float = 0.2,
    confidence: float = 0.8,
) -> ForecastHorizon:
    direction = (
        "bullish" if probability >= 0.58 else "bearish" if probability <= 0.42 else "neutral"
    )
    return ForecastHorizon(
        horizon_days=days,
        raw_probability_up=probability,
        probability_up=probability,
        expected_return_pct=expected_return,
        expected_alpha_vs_spy_pct=alpha,
        p10_return_pct=-1.5,
        p50_return_pct=max(0.1, expected_return),
        p90_return_pct=2.5,
        expected_mfe_pct=2.0,
        expected_mae_pct=-1.0,
        evidence_coverage=0.9,
        forecast_confidence=confidence,
        calibration_samples=samples,
        calibration_status=status,
        regime="risk_on",
        champion_model="calibrated_ensemble",
        challenger_model="momentum_challenger",
        challenger_probability_up=probability,
        direction=direction,
        score=probability * 100.0,
        diagnostics={"historical_direction_hit_rate": hit_rate},
    )


def _bundle(h1: ForecastHorizon, h5: ForecastHorizon, h20: ForecastHorizon) -> ForecastBundle:
    h10 = _horizon(
        10,
        probability=0.50,
        samples=0,
        status="prior_only",
        hit_rate=None,
        expected_return=0.0,
        alpha=0.0,
    )
    return ForecastBundle(
        symbol="TEST",
        instrument_type="STOCK",
        effective_trade_date="2026-08-28",
        regime="risk_on",
        model_version="v7.4-test",
        horizons={"1d": h1, "5d": h5, "10d": h10, "20d": h20},
        primary_horizon="5d",
        champion_model="calibrated_ensemble",
        challenger_model="momentum_challenger",
        promotion_status="observing",
        evidence_coverage=0.9,
    )


def _strong_continuation_features() -> AlphaFeatures:
    return AlphaFeatures(
        trend=68.0,
        momentum=88.0,
        relative_strength=86.0,
        sector_relative_strength=75.0,
        volume_confirmation=68.0,
        market_regime=82.0,
    )


def test_low_sample_bearish_direction_is_research_only() -> None:
    horizon = _horizon(
        5,
        probability=0.38,
        samples=36,
        status="shrunk",
        hit_rate=0.25,
        expected_return=-0.5,
    )
    assert forecast_reliability_weight(horizon) == 0.0
    assert reliability_aware_direction(horizon) == "neutral"


def test_poor_mature_hit_rate_is_zero_weight_and_mid_hit_rate_is_low_weight() -> None:
    poor = _horizon(
        5,
        probability=0.35,
        samples=80,
        status="mature",
        hit_rate=0.44,
    )
    middling = _horizon(
        5,
        probability=0.60,
        samples=80,
        status="mature",
        hit_rate=0.49,
    )
    validated = _horizon(
        5,
        probability=0.60,
        samples=80,
        status="mature",
        hit_rate=0.56,
    )
    assert forecast_reliability_weight(poor) == 0.0
    assert 0.0 < forecast_reliability_weight(middling) < 0.10
    assert forecast_reliability_weight(validated) == 0.8


def test_momentum_continuation_requires_multi_factor_risk_on_confirmation() -> None:
    strong = momentum_continuation_score(_strong_continuation_features(), "risk_on")
    assert strong >= 0.65
    weak_volume = AlphaFeatures(
        trend=68.0,
        momentum=88.0,
        relative_strength=86.0,
        volume_confirmation=20.0,
        market_regime=82.0,
    )
    assert momentum_continuation_score(weak_volume, "risk_on") == 0.0
    assert momentum_continuation_score(_strong_continuation_features(), "risk_off") == 0.0


def test_low_reliability_bearish_forecast_cannot_force_avoid() -> None:
    h1 = _horizon(1, probability=0.40, samples=44, status="shrunk", hit_rate=0.50)
    h5 = _horizon(
        5,
        probability=0.35,
        samples=36,
        status="shrunk",
        hit_rate=0.25,
        expected_return=-0.8,
        alpha=-0.2,
    )
    h20 = _horizon(20, probability=0.40, samples=0, status="prior_only", hit_rate=None)
    decision = ForecastDecisionPolicy().decide(
        _bundle(h1, h5, h20),
        risk_score=35.0,
        opportunity_score=75.0,
        features=_strong_continuation_features(),
    )
    assert decision.decision == "WAIT"
    assert "5d_reliability_below_trading_floor" in decision.gates
    assert "continuation" in decision.rationale


def test_strong_continuation_suppresses_reliable_bearish_avoid_but_not_hard_risk() -> None:
    h1 = _horizon(1, probability=0.40, samples=100, status="mature", hit_rate=0.60)
    h5 = _horizon(
        5,
        probability=0.35,
        samples=100,
        status="mature",
        hit_rate=0.60,
        expected_return=-0.8,
        alpha=-0.2,
    )
    h20 = _horizon(
        20,
        probability=0.40,
        samples=100,
        status="mature",
        hit_rate=0.60,
        expected_return=-0.3,
    )
    policy = ForecastDecisionPolicy()
    continuation_decision = policy.decide(
        _bundle(h1, h5, h20),
        risk_score=40.0,
        opportunity_score=75.0,
        features=_strong_continuation_features(),
    )
    assert continuation_decision.decision == "WAIT"
    assert "conflicts with a strong risk-on momentum" in continuation_decision.rationale

    no_continuation = policy.decide(
        _bundle(h1, h5, h20),
        risk_score=40.0,
        opportunity_score=75.0,
        features=AlphaFeatures(
            trend=45.0,
            momentum=40.0,
            relative_strength=45.0,
            volume_confirmation=40.0,
            market_regime=82.0,
        ),
    )
    assert no_continuation.decision == "AVOID"

    hard_risk = policy.decide(
        _bundle(h1, h5, h20),
        risk_score=80.0,
        opportunity_score=75.0,
        features=_strong_continuation_features(),
    )
    assert hard_risk.decision == "AVOID"
    assert "hard_risk_gate" in hard_risk.gates
