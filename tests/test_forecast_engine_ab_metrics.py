from __future__ import annotations

import pytest

from scripts.run_forecast_engine_ab_walk_forward import _summary


def _row(
    probability: float,
    realized_return: float,
    *,
    expected_return: float = 1.0,
    expected_alpha: float = 0.5,
    realized_alpha: float | None = 0.4,
    direction: str = "bullish",
) -> dict:
    return {
        "probability_up": probability,
        "expected_return_pct": expected_return,
        "expected_alpha_pct": expected_alpha,
        "realized_return_pct": realized_return,
        "realized_alpha_pct": realized_alpha,
        "direction": direction,
        "calibration_status": "mature",
    }


def test_summary_measures_direction_skill_against_majority_baseline() -> None:
    rows = [
        _row(0.70, 1.0),
        _row(0.70, 2.0),
        _row(0.30, -1.0, direction="bearish"),
        _row(0.70, 0.5),
    ]
    result = _summary(rows)

    assert result["positive_rate_pct"] == pytest.approx(75.0)
    assert result["majority_baseline_accuracy_pct"] == pytest.approx(75.0)
    assert result["directional_accuracy_pct"] == pytest.approx(100.0)
    assert result["direction_skill_pp"] == pytest.approx(25.0)
    assert result["base_rate_brier_score"] == pytest.approx(0.1875)


def test_summary_scores_only_constructive_buy_candidates() -> None:
    rows = [
        _row(0.65, 2.0, realized_alpha=1.0),
        _row(0.62, -1.0, realized_alpha=-0.5),
        _row(
            0.70,
            3.0,
            expected_alpha=-0.2,
            realized_alpha=1.2,
        ),
        _row(
            0.55,
            1.0,
            expected_alpha=0.5,
            realized_alpha=0.6,
            direction="neutral",
        ),
    ]
    result = _summary(rows)

    assert result["buy_candidate_samples"] == 2
    assert result["buy_candidate_win_rate_pct"] == pytest.approx(50.0)
    assert result["buy_candidate_mean_return_pct"] == pytest.approx(0.5)
    assert result["buy_candidate_positive_alpha_rate_pct"] == pytest.approx(50.0)
    assert result["buy_candidate_mean_alpha_pct"] == pytest.approx(0.25)
