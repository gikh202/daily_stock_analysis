from __future__ import annotations

from scripts.run_forecast_engine_ab_walk_forward import _summary


def test_ab_summary_reports_majority_skill_inverse_and_directional_alpha() -> None:
    rows = [
        {
            "probability_up": 0.70,
            "realized_return_pct": 1.0,
            "realized_excess_vs_spy_pct": 0.8,
            "expected_return_pct": 0.9,
            "direction": "bullish",
            "calibration_status": "mature",
        },
        {
            "probability_up": 0.30,
            "realized_return_pct": -1.0,
            "realized_excess_vs_spy_pct": -0.6,
            "expected_return_pct": -0.8,
            "direction": "bearish",
            "calibration_status": "mature",
        },
        {
            "probability_up": 0.65,
            "realized_return_pct": 0.5,
            "realized_excess_vs_spy_pct": 0.4,
            "expected_return_pct": 0.4,
            "direction": "bullish",
            "calibration_status": "mature",
        },
        {
            "probability_up": 0.35,
            "realized_return_pct": -0.5,
            "realized_excess_vs_spy_pct": -0.2,
            "expected_return_pct": -0.4,
            "direction": "bearish",
            "calibration_status": "mature",
        },
    ]
    summary = _summary(rows)
    assert summary["positive_rate_pct"] == 50.0
    assert summary["majority_baseline_accuracy_pct"] == 50.0
    assert summary["directional_accuracy_pct"] == 100.0
    assert summary["direction_skill_pp"] == 50.0
    assert summary["inverse_directional_accuracy_pct"] == 0.0
    assert summary["directional_alpha_pct"] == 0.5
    assert summary["alpha_samples"] == 4


def test_ab_summary_exposes_zero_skill_for_majority_only_prediction() -> None:
    rows = []
    for index in range(10):
        positive = index < 7
        rows.append(
            {
                "probability_up": 0.60,
                "realized_return_pct": 1.0 if positive else -1.0,
                "realized_excess_vs_spy_pct": 0.5 if positive else -0.5,
                "expected_return_pct": 0.2,
                "direction": "bullish",
                "calibration_status": "mature",
            }
        )
    summary = _summary(rows)
    assert summary["majority_baseline_accuracy_pct"] == 70.0
    assert summary["directional_accuracy_pct"] == 70.0
    assert summary["direction_skill_pp"] == 0.0
    assert summary["inverse_directional_accuracy_pct"] == 30.0
