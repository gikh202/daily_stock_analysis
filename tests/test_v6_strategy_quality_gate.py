from __future__ import annotations

import pytest

from scripts.assert_v6_strategy_quality import _validate_head, _validate_no_regression


def _payload(hit: float):
    return {
        "observations": 1000,
        "promotion_min_samples": 100,
        "results": [
            {
                "variant": "champion",
                "horizon_days": 20,
                "promotion_candidate": False,
                "non_overlapping": {
                    "samples": 136,
                    "directional_hit_rate_pct": hit,
                    "avg_underlying_excess_vs_spy_pct": 0.13,
                },
                "alpha_target": {
                    "non_overlapping": {
                        "samples": 136,
                        "alpha_hit_rate_pct": 56.9,
                        "avg_alpha_trade_return_pct": 0.23,
                    }
                },
            }
        ],
    }


def test_identical_baseline_floor_breach_does_not_block_unrelated_pr():
    head = _payload(49.26)
    base = _payload(49.26)
    summaries = _validate_head(head, baseline=base)
    _validate_no_regression(head, base)
    assert summaries


def test_new_floor_breach_still_fails_when_base_was_above_floor():
    head = _payload(49.26)
    base = _payload(50.10)
    with pytest.raises(AssertionError, match="directional hit below 50%"):
        _validate_head(head, baseline=base)


def test_material_regression_still_fails_even_when_both_are_below_floor():
    head = _payload(48.0)
    base = _payload(49.5)
    with pytest.raises(AssertionError):
        _validate_head(head, baseline=base)
    with pytest.raises(AssertionError, match="directional hit regression"):
        _validate_no_regression(head, base)
