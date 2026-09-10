from __future__ import annotations

import pytest

from src.forecasting.production_engine import _normalized_prediction_context
from src.forecasting.strong_signal import scope_reliability_haircut, strong_signal_skill


class _FakeHistory:
    def __init__(self, rows):
        self.rows = rows

    def _rows(self, **kwargs):
        return list(self.rows)

    def _hierarchical_scope(self, rows, **kwargs):
        return list(rows), "global", len(rows)

    @staticmethod
    def _row_probability(row, key="probability_up"):
        return row.get(key)


def test_trailing_return_is_not_reused_as_future_return_target() -> None:
    context = {
        "prediction_context": {
            "horizons": {
                "5d": {
                    "target_return_pct": 8.0,
                    "excess_vs_spy_pct": 2.0,
                    "excess_vs_qqq_pct": 4.0,
                }
            }
        }
    }
    normalized, audit = _normalized_prediction_context(context)
    block = normalized["prediction_context"]["horizons"]["5d"]
    assert "target_return_pct" not in block
    assert block["trailing_return_pct"] == 8.0
    assert block["excess_vs_spy_pct"] == 2.0
    assert "excess_vs_qqq_pct" not in block
    assert audit["5d"]["observed_excess_vs_qqq_pct"] == 4.0


def test_explicit_future_forecast_semantics_preserves_target_return() -> None:
    context = {
        "prediction_context": {
            "horizons": {
                "5d": {
                    "target_return_pct": 3.0,
                    "return_semantics": "future_forecast",
                }
            }
        }
    }
    normalized, _ = _normalized_prediction_context(context)
    assert normalized["prediction_context"]["horizons"]["5d"]["target_return_pct"] == 3.0


def test_strong_signal_skill_ignores_weak_probabilities() -> None:
    history = _FakeHistory(
        [
            {"probability_up": 0.60, "return_pct": 2.0},
            {"probability_up": 0.62, "return_pct": 1.0},
            {"probability_up": 0.40, "return_pct": -1.0},
            {"probability_up": 0.55, "return_pct": -2.0},
        ]
    )
    result = strong_signal_skill(
        history,
        as_of_date="2026-09-10",
        horizon_days=5,
        regime="risk_on",
        symbol="TEST",
        instrument_type="STOCK",
    )
    assert result["samples"] == 3
    assert result["hit_rate"] == pytest.approx(1.0)
    assert result["scope"] == "global"
    assert scope_reliability_haircut("global") == pytest.approx(0.60)
