import math

from src.alpha_engine.features import AlphaFeatureAdapter


def _snapshot():
    return {
        "trend_result": {
            "signal_score": 82,
            "current_price": 200,
            "support_levels": [188, 194],
            "resistance_levels": [220, 230],
        },
        "prediction_context": {
            "horizons": {
                "5d": {
                    "target_return_pct": 2.0,
                    "excess_vs_spy_pct": 1.0,
                    "excess_vs_qqq_pct": 0.5,
                },
                "20d": {
                    "target_return_pct": 8.0,
                    "excess_vs_spy_pct": 4.0,
                    "excess_vs_qqq_pct": 3.0,
                },
                "60d": {
                    "target_return_pct": 15.0,
                    "excess_vs_spy_pct": 8.0,
                    "excess_vs_qqq_pct": 5.0,
                },
            },
            "realized_vol_20d_pct": 24,
        },
        "market_regime": {
            "regime": "risk_on",
            "market_breadth": {"breadth": "broad"},
        },
        "earnings_event": {"days_until_earnings": 20},
        "rvol": 1.4,
    }


def test_feature_adapter_uses_structured_evidence_only():
    adapted = AlphaFeatureAdapter.from_snapshot(_snapshot())
    f = adapted.features

    assert f.trend == 82
    assert f.momentum is not None and f.momentum > 50
    assert f.relative_strength is not None and f.relative_strength > 50
    assert f.volume_confirmation is not None and f.volume_confirmation > 50
    assert f.market_regime == 90
    assert f.volatility_risk is not None
    assert f.event_risk == 30
    assert f.catalyst is None  # no fake sentiment from news count/prose
    assert adapted.current_price == 200
    assert adapted.support == 194
    assert adapted.resistance == 220


def test_missing_structured_data_remains_missing_not_neutral():
    adapted = AlphaFeatureAdapter.from_snapshot(
        {"trend_result": {"current_price": 100, "signal_score": 70}}
    )
    f = adapted.features

    assert f.trend == 70
    assert f.momentum is None
    assert f.relative_strength is None
    assert f.market_regime is None
    assert f.volatility_risk is None
    assert f.event_risk is None
    assert f.data_quality < 50


def test_non_finite_provider_values_do_not_enter_features():
    adapted = AlphaFeatureAdapter.from_snapshot(
        {
            "trend_result": {"current_price": 100, "signal_score": float("nan")},
            "prediction_context": {
                "horizons": {
                    "20d": {"target_return_pct": float("inf")},
                    "60d": {"target_return_pct": float("-inf")},
                }
            },
        }
    )
    assert adapted.features.trend is None
    assert adapted.features.momentum is None
    assert math.isfinite(adapted.features.data_quality)
