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

    challenger = next(item for item in results if item["variant"] != "champion")
    assert "promotion_candidate" in challenger
    assert "hit_rate_delta_vs_champion_pp" in challenger
