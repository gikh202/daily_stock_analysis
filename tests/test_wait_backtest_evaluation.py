from __future__ import annotations

import pytest

from scripts.evaluate_wait_backtest import enrich_wait_backtest


def _payload(*, hit_rate: float = 0.60, alpha: float = 0.05, samples: int = 20):
    rows = []
    for index in range(samples):
        hit = index < round(samples * hit_rate)
        rows.append(
            {
                "signal_price": 100.0,
                "expected_better_price": 99.7 if hit else 99.7,
                "better_entry_hit": hit,
            }
        )
    return {
        "wait_sample_count": samples,
        "better_entry_hit_rate": hit_rate,
        "entry_timing_alpha": {"avg_vs_immediate_pct": alpha},
        "promotion_check": {},
        "rows": rows,
    }


def test_realized_improvement_uses_expected_entry_only_for_filled_waits():
    result = enrich_wait_backtest(_payload(), transaction_cost_hurdle_pct=0.10)
    assert result["realized_entry_improvement_samples"] == 12
    assert result["avg_realized_entry_improvement_pct"] == pytest.approx(0.30)
    assert result["promotion_check"]["realized_improvement_gt_cost_hurdle"] is True
    assert result["promotion_check"]["eligible"] is True


def test_promotion_fails_when_improvement_does_not_clear_cost_hurdle():
    payload = _payload()
    for row in payload["rows"]:
        row["expected_better_price"] = 99.95
    result = enrich_wait_backtest(payload, transaction_cost_hurdle_pct=0.10)
    assert result["avg_realized_entry_improvement_pct"] == pytest.approx(0.05)
    assert result["promotion_check"]["realized_improvement_gt_cost_hurdle"] is False
    assert result["promotion_check"]["eligible"] is False


def test_promotion_stays_fail_closed_below_sample_and_hit_thresholds():
    result = enrich_wait_backtest(
        _payload(hit_rate=0.21, alpha=0.01, samples=19),
        transaction_cost_hurdle_pct=0.10,
    )
    assert result["promotion_check"]["sample_count_ok"] is False
    assert result["promotion_check"]["hit_rate_gt_50pct"] is False
    assert result["promotion_check"]["eligible"] is False
