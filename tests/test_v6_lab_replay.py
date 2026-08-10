from __future__ import annotations

from src.v6_daily.lab_replay import (
    AccuracyReplayObservation,
    replay_accuracy_lab,
    summarize_accuracy_replay,
)


def _series_with_future(multiplier: float) -> dict:
    result = {"MSFT": [], "SPY": [], "QQQ": []}
    for day in range(1, 141):
        date_value = f"2025-{((day - 1) // 28) + 1:02d}-{((day - 1) % 28) + 1:02d}"
        base = 100.0 + day * 0.25
        future_adjustment = 0.0 if day <= 81 else (day - 81) * multiplier
        result["MSFT"].append(
            {
                "date": date_value,
                "open": base + future_adjustment - 0.1,
                "high": base + future_adjustment + 0.8,
                "low": base + future_adjustment - 0.8,
                "close": base + future_adjustment,
                "volume": 1_000_000 + day * 100,
            }
        )
        result["SPY"].append(
            {
                "date": date_value,
                "open": 100 + day * 0.11,
                "high": 100 + day * 0.11 + 0.5,
                "low": 100 + day * 0.11 - 0.5,
                "close": 100 + day * 0.11,
                "volume": 2_000_000 + day,
            }
        )
        result["QQQ"].append(
            {
                "date": date_value,
                "open": 100 + day * 0.16,
                "high": 100 + day * 0.16 + 0.5,
                "low": 100 + day * 0.16 - 0.5,
                "close": 100 + day * 0.16,
                "volume": 2_500_000 + day,
            }
        )
    return result


def test_champion_and_challenger_replay_do_not_leak_future_prices() -> None:
    first_series = _series_with_future(0.2)
    second_series = _series_with_future(4.0)
    first = replay_accuracy_lab(first_series, codes=["MSFT"], min_lookback=60)
    second = replay_accuracy_lab(second_series, codes=["MSFT"], min_lookback=60)

    key_date = first_series["MSFT"][80]["date"]
    variants = {"champion", "trend_guard", "momentum_focus", "relative_strength_focus"}
    for variant in variants:
        left = next(
            item
            for item in first
            if item.as_of == key_date and item.horizon_days == 5 and item.variant == variant
        )
        right = next(
            item
            for item in second
            if item.as_of == key_date and item.horizon_days == 5 and item.variant == variant
        )
        assert left.score == right.score
        assert left.direction == right.direction
        assert left.spy_trend_regime == right.spy_trend_regime
        assert left.spy_vol_regime == right.spy_vol_regime
        # The outcome is allowed to differ because only the future path changed.
        assert left.future_return_pct != right.future_return_pct


def test_replay_strategy_return_matches_forecast_direction() -> None:
    observations = replay_accuracy_lab(
        _series_with_future(0.4),
        codes=["MSFT"],
        min_lookback=60,
    )
    assert observations

    for item in observations:
        if item.direction == "bullish":
            expected = item.future_return_pct
        elif item.direction == "bearish":
            expected = -item.future_return_pct
        else:
            expected = 0.0
        assert item.strategy_return_pct == round(expected, 6)


def test_strategy_metrics_are_variant_specific_on_same_underlying_path() -> None:
    observations = [
        AccuracyReplayObservation(
            variant="champion",
            code="MSFT",
            as_of="2025-01-02",
            as_of_index=60,
            horizon_days=5,
            score=20.0,
            direction="bullish",
            future_return_pct=10.0,
            directional_hit=1,
            excess_vs_spy_pct=8.0,
            strategy_return_pct=10.0,
            strategy_excess_vs_spy_pct=8.0,
        ),
        AccuracyReplayObservation(
            variant="trend_guard",
            code="MSFT",
            as_of="2025-01-02",
            as_of_index=60,
            horizon_days=5,
            score=-20.0,
            direction="bearish",
            future_return_pct=10.0,
            directional_hit=0,
            excess_vs_spy_pct=8.0,
            strategy_return_pct=-10.0,
            strategy_excess_vs_spy_pct=-12.0,
        ),
    ]

    summary = summarize_accuracy_replay(
        observations,
        min_samples=3,
        promotion_min_samples=3,
    )
    champion = next(item for item in summary["results"] if item["variant"] == "champion")
    challenger = next(item for item in summary["results"] if item["variant"] == "trend_guard")

    assert summary["strategy_return_method"] == "gross_directional_position_v1"
    assert champion["raw"]["avg_underlying_return_pct"] == 10.0
    assert challenger["raw"]["avg_underlying_return_pct"] == 10.0
    assert champion["raw"]["avg_underlying_excess_vs_spy_pct"] == 8.0
    assert challenger["raw"]["avg_underlying_excess_vs_spy_pct"] == 8.0
    assert champion["raw"]["avg_return_pct"] == 10.0
    assert challenger["raw"]["avg_return_pct"] == -10.0
    assert champion["raw"]["avg_excess_vs_spy_pct"] == 8.0
    assert challenger["raw"]["avg_excess_vs_spy_pct"] == -12.0


def test_alpha_target_distinguishes_absolute_direction_from_spy_relative_value() -> None:
    observations = [
        AccuracyReplayObservation(
            variant="champion",
            code="MSFT",
            as_of="2025-01-02",
            as_of_index=0,
            horizon_days=5,
            score=65.0,
            direction="bullish",
            future_return_pct=2.0,
            directional_hit=1,
            excess_vs_spy_pct=-3.0,
            strategy_return_pct=2.0,
            strategy_excess_vs_spy_pct=-3.0,
            alpha_target_hit=0,
            alpha_trade_return_pct=-3.0,
            spy_trend_regime="up",
            spy_vol_regime="expanding",
        ),
        AccuracyReplayObservation(
            variant="champion",
            code="MSFT",
            as_of="2025-01-09",
            as_of_index=5,
            horizon_days=5,
            score=35.0,
            direction="bearish",
            future_return_pct=-1.0,
            directional_hit=1,
            excess_vs_spy_pct=-2.0,
            strategy_return_pct=1.0,
            strategy_excess_vs_spy_pct=0.0,
            alpha_target_hit=1,
            alpha_trade_return_pct=2.0,
            spy_trend_regime="up",
            spy_vol_regime="contracting",
        ),
    ]

    summary = summarize_accuracy_replay(observations, min_samples=3, promotion_min_samples=3)
    result = summary["results"][0]
    alpha = result["alpha_target"]

    assert result["raw"]["directional_hit_rate_pct"] == 100.0
    assert alpha["raw"]["samples"] == 2
    assert alpha["raw"]["alpha_hit_rate_pct"] == 50.0
    assert alpha["raw"]["avg_alpha_trade_return_pct"] == -0.5
    assert alpha["non_overlapping"]["samples"] == 2
    calibration = {item["label"]: item for item in result["alpha_calibration"]}
    assert calibration["5-10pt"]["raw"]["samples"] == 2
    assert calibration["5-10pt"]["non_overlapping"]["samples"] == 2
    assert sum(item["raw"]["samples"] for item in result["regime_matrix"]) == 2
    assert sum(item["non_overlapping"]["samples"] for item in result["regime_matrix"]) == 2


def test_selectivity_filters_before_non_overlap_and_excludes_neutral() -> None:
    observations = [
        AccuracyReplayObservation(
            variant="champion",
            code="MSFT",
            as_of="2025-01-02",
            as_of_index=0,
            horizon_days=5,
            score=61.0,
            direction="bullish",
            future_return_pct=-2.0,
            directional_hit=0,
            excess_vs_spy_pct=-2.5,
            strategy_return_pct=-2.0,
            strategy_excess_vs_spy_pct=-2.5,
        ),
        AccuracyReplayObservation(
            variant="champion",
            code="MSFT",
            as_of="2025-01-03",
            as_of_index=1,
            horizon_days=5,
            score=70.0,
            direction="bullish",
            future_return_pct=4.0,
            directional_hit=1,
            excess_vs_spy_pct=3.0,
            strategy_return_pct=4.0,
            strategy_excess_vs_spy_pct=3.0,
        ),
        AccuracyReplayObservation(
            variant="champion",
            code="MSFT",
            as_of="2025-01-10",
            as_of_index=6,
            horizon_days=5,
            score=55.0,
            direction="neutral",
            future_return_pct=0.5,
            directional_hit=1,
            excess_vs_spy_pct=0.0,
            strategy_return_pct=0.0,
            strategy_excess_vs_spy_pct=-0.5,
        ),
    ]

    summary = summarize_accuracy_replay(observations, min_samples=3, promotion_min_samples=3)
    result = summary["results"][0]
    slices = {item["min_margin_points"]: item for item in result["selectivity_analysis"]}

    assert summary["selectivity_analysis_method"] == "directional_margin_filter_then_global_non_overlap_v1"
    assert summary["selectivity_margin_thresholds"] == [0.0, 2.0, 5.0, 10.0]
    assert slices[0.0]["raw"]["samples"] == 2
    assert slices[0.0]["non_overlapping"]["samples"] == 1
    assert slices[0.0]["participation_rate_pct"] == 66.67
    assert slices[0.0]["directional_capture_rate_pct"] == 100.0
    # Filter first: the weak Jan-02 signal is removed, so the stronger overlapping
    # Jan-03 signal is allowed to become the selected independent observation.
    assert slices[5.0]["raw"]["samples"] == 1
    assert slices[5.0]["non_overlapping"]["samples"] == 1
    assert slices[5.0]["non_overlapping"]["avg_return_pct"] == 4.0
    assert slices[5.0]["non_overlapping"]["avg_excess_vs_spy_pct"] == 3.0
    assert slices[10.0]["non_overlapping"]["samples"] == 1


def test_selectivity_neutral_only_bucket_reports_zero_capture() -> None:
    observations = [
        AccuracyReplayObservation(
            variant="champion",
            code="MSFT",
            as_of="2025-01-02",
            as_of_index=0,
            horizon_days=5,
            score=55.0,
            direction="neutral",
            future_return_pct=0.5,
            directional_hit=1,
            excess_vs_spy_pct=0.0,
            strategy_return_pct=0.0,
            strategy_excess_vs_spy_pct=-0.5,
        )
    ]

    summary = summarize_accuracy_replay(observations, min_samples=3, promotion_min_samples=3)
    slices = summary["results"][0]["selectivity_analysis"]

    assert slices
    assert all(item["raw"]["samples"] == 0 for item in slices)
    assert all(item["non_overlapping"]["samples"] == 0 for item in slices)
    assert all(item["participation_rate_pct"] == 0.0 for item in slices)
    assert all(item["directional_capture_rate_pct"] == 0.0 for item in slices)


def test_yearly_non_overlapping_uses_global_selection_across_year_boundary() -> None:
    observations = [
        AccuracyReplayObservation(
            variant="champion",
            code="MSFT",
            as_of="2024-12-31",
            as_of_index=4,
            horizon_days=5,
            score=10.0,
            direction="bullish",
            future_return_pct=1.0,
            directional_hit=1,
            excess_vs_spy_pct=0.5,
            strategy_return_pct=1.0,
            strategy_excess_vs_spy_pct=0.5,
        ),
        AccuracyReplayObservation(
            variant="champion",
            code="MSFT",
            as_of="2025-01-02",
            as_of_index=5,
            horizon_days=5,
            score=10.0,
            direction="bullish",
            future_return_pct=1.0,
            directional_hit=1,
            excess_vs_spy_pct=0.5,
            strategy_return_pct=1.0,
            strategy_excess_vs_spy_pct=0.5,
        ),
        AccuracyReplayObservation(
            variant="champion",
            code="MSFT",
            as_of="2025-01-09",
            as_of_index=10,
            horizon_days=5,
            score=10.0,
            direction="bullish",
            future_return_pct=1.0,
            directional_hit=1,
            excess_vs_spy_pct=0.5,
            strategy_return_pct=1.0,
            strategy_excess_vs_spy_pct=0.5,
        ),
    ]

    summary = summarize_accuracy_replay(observations, min_samples=3, promotion_min_samples=3)
    result = summary["results"][0]
    by_year = {item["year"]: item for item in result["yearly_walk_forward"]}

    assert summary["yearly_walk_forward_method"] == "raw_and_global_non_overlapping_by_calendar_year_v2"
    assert by_year["2024"]["raw"]["samples"] == 1
    assert by_year["2024"]["non_overlapping"]["samples"] == 1
    assert by_year["2025"]["raw"]["samples"] == 2
    # The 2025-01-02 forecast overlaps the selected 2024-12-31 5D window,
    # so the yearly independent view must not restart sampling at Jan 1.
    assert by_year["2025"]["non_overlapping"]["samples"] == 1
    assert sum(item["raw"]["samples"] for item in by_year.values()) == result["raw"]["samples"]
    assert sum(item["non_overlapping"]["samples"] for item in by_year.values()) == result["non_overlapping"]["samples"]


def test_accuracy_replay_reports_non_overlapping_walk_forward_and_safe_policy() -> None:
    observations = replay_accuracy_lab(
        _series_with_future(0.4),
        codes=["MSFT"],
        min_lookback=60,
    )
    summary = summarize_accuracy_replay(
        observations,
        min_samples=3,
        promotion_min_samples=6,
    )

    assert summary["method"] == "strict no-lookahead rolling price-feature replay"
    assert summary["strategy_return_method"] == "gross_directional_position_v1"
    assert summary["yearly_walk_forward_method"] == "raw_and_global_non_overlapping_by_calendar_year_v2"
    assert summary["selectivity_analysis_method"] == "directional_margin_filter_then_global_non_overlap_v1"
    assert summary["selectivity_margin_thresholds"] == [0.0, 2.0, 5.0, 10.0]
    assert summary["alpha_target_method"] == "spy_relative_directional_filter_then_global_non_overlap_v1"
    assert summary["alpha_calibration_method"] == "fixed_directional_margin_buckets_v1"
    assert summary["regime_matrix_method"] == "global_alpha_non_overlap_then_asof_spy_regime_partition_v1"
    assert [item["label"] for item in summary["alpha_calibration_buckets"]] == [
        "0-2pt",
        "2-5pt",
        "5-10pt",
        "10pt+",
    ]
    assert summary["auto_promotion"] is False
    assert summary["auto_weight_tuning"] is False
    assert summary["observations"] > 0
    assert "SEC/FRED" in summary["scope"]

    results = summary["results"]
    assert {item["variant"] for item in results} == {
        "champion",
        "trend_guard",
        "momentum_focus",
        "relative_strength_focus",
    }
    champion_20d = next(
        item for item in results if item["variant"] == "champion" and item["horizon_days"] == 20
    )
    assert champion_20d["raw"]["samples"] > champion_20d["non_overlapping"]["samples"]
    assert champion_20d["non_overlapping"]["hit_rate_ci95_low_pct"] is not None
    assert champion_20d["non_overlapping"]["avg_return_pct"] is not None
    assert champion_20d["non_overlapping"]["avg_excess_vs_spy_pct"] is not None
    assert champion_20d["non_overlapping"]["avg_underlying_return_pct"] is not None
    yearly = champion_20d["yearly_walk_forward"]
    assert yearly
    assert all("raw" in item and "non_overlapping" in item for item in yearly)
    assert sum(item["raw"]["samples"] for item in yearly) == champion_20d["raw"]["samples"]
    assert (
        sum(item["non_overlapping"]["samples"] for item in yearly)
        == champion_20d["non_overlapping"]["samples"]
    )
    selectivity = champion_20d["selectivity_analysis"]
    assert [item["min_margin_points"] for item in selectivity] == [0.0, 2.0, 5.0, 10.0]
    assert all("raw" in item and "non_overlapping" in item for item in selectivity)

    alpha = champion_20d["alpha_target"]
    assert "raw" in alpha and "non_overlapping" in alpha
    assert alpha["non_overlapping"]["samples"] <= alpha["raw"]["samples"]
    calibration = champion_20d["alpha_calibration"]
    assert [item["label"] for item in calibration] == ["0-2pt", "2-5pt", "5-10pt", "10pt+"]
    assert all("raw" in item and "non_overlapping" in item for item in calibration)
    regime = champion_20d["regime_matrix"]
    assert regime
    assert sum(item["raw"]["samples"] for item in regime) == alpha["raw"]["samples"]
    assert (
        sum(item["non_overlapping"]["samples"] for item in regime)
        == alpha["non_overlapping"]["samples"]
    )

    challenger = next(item for item in results if item["variant"] != "champion")
    assert "promotion_candidate" in challenger
    assert "hit_rate_delta_vs_champion_pp" in challenger
