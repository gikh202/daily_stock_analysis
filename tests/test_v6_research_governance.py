from __future__ import annotations

from src.v6_daily.lab_replay import AccuracyReplayObservation, summarize_accuracy_replay
from src.v6_daily.research_governance import (
    FORWARD_FREEZE_DATE,
    FROZEN_ALPHA_CANDIDATES,
    enrich_accuracy_payload,
)


def _obs(
    *,
    variant: str = "champion",
    as_of: str,
    index: int,
    horizon: int = 5,
    score: float = 65.0,
    direction: str = "bullish",
    alpha_hit: int = 1,
    alpha_return: float = 1.0,
) -> AccuracyReplayObservation:
    future_return = 2.0 if direction == "bullish" else -2.0
    underlying_alpha = alpha_return if direction == "bullish" else -alpha_return
    return AccuracyReplayObservation(
        variant=variant,
        code="MSFT",
        as_of=as_of,
        as_of_index=index,
        horizon_days=horizon,
        score=score,
        direction=direction,
        future_return_pct=future_return,
        directional_hit=1,
        excess_vs_spy_pct=underlying_alpha,
        strategy_return_pct=abs(future_return),
        strategy_excess_vs_spy_pct=alpha_return,
        alpha_target_hit=alpha_hit,
        alpha_trade_return_pct=alpha_return,
        spy_trend_regime="up",
        spy_vol_regime="contracting",
    )


def _payload(observations: list[AccuracyReplayObservation]) -> dict:
    return summarize_accuracy_replay(
        observations,
        min_samples=3,
        promotion_min_samples=3,
    )


def test_common_timeline_calibration_uses_one_global_independent_set() -> None:
    observations = [
        _obs(as_of="2025-01-02", index=0, score=61.0, alpha_return=0.4),
        _obs(as_of="2025-01-03", index=1, score=70.0, alpha_return=1.2),
        _obs(as_of="2025-01-10", index=5, score=66.0, alpha_return=0.8),
    ]

    enriched = enrich_accuracy_payload(_payload(observations), observations)
    result = enriched["results"][0]
    alpha_n = result["alpha_target"]["non_overlapping"]["samples"]
    common = result["alpha_calibration_common_timeline"]
    bucket_n = sum(
        item["non_overlapping"]["samples"] for item in common["buckets"]
    )
    unscored_n = common["unscored"]["non_overlapping"]["samples"]

    assert alpha_n == 2
    assert bucket_n + unscored_n == alpha_n
    by_label = {item["label"]: item for item in common["buckets"]}
    # Jan-03 is a 10pt-margin observation but overlaps Jan-02. Under a common
    # timeline it cannot become independently selected just because its bucket
    # is evaluated separately.
    assert by_label["10pt+"]["raw"]["samples"] == 1
    assert by_label["10pt+"]["non_overlapping"]["samples"] == 0


def test_alpha_yearly_walk_forward_does_not_restart_non_overlap_at_new_year() -> None:
    observations = [
        _obs(as_of="2024-12-31", index=4, alpha_return=0.5),
        _obs(as_of="2025-01-02", index=5, alpha_return=0.6),
        _obs(as_of="2025-01-09", index=10, alpha_return=0.7),
    ]

    enriched = enrich_accuracy_payload(_payload(observations), observations)
    yearly = enriched["results"][0]["alpha_yearly_walk_forward"]
    by_year = {item["year"]: item for item in yearly}

    assert by_year["2024"]["non_overlapping"]["samples"] == 1
    assert by_year["2025"]["raw"]["samples"] == 2
    assert by_year["2025"]["non_overlapping"]["samples"] == 1
    assert (
        sum(item["non_overlapping"]["samples"] for item in yearly)
        == enriched["results"][0]["alpha_target"]["non_overlapping"]["samples"]
    )


def test_cost_sensitivity_is_monotonic_and_keeps_same_independent_sample() -> None:
    observations = [
        _obs(as_of="2025-01-02", index=0, alpha_return=0.6),
        _obs(as_of="2025-01-09", index=5, alpha_return=0.3),
        _obs(as_of="2025-01-16", index=10, alpha_return=-0.1, alpha_hit=0),
    ]

    enriched = enrich_accuracy_payload(_payload(observations), observations)
    costs = enriched["results"][0]["alpha_cost_sensitivity"]

    assert [item["total_cost_bps"] for item in costs] == [0, 10, 20, 40]
    assert len({item["samples"] for item in costs}) == 1
    averages = [item["avg_net_alpha_trade_return_pct"] for item in costs]
    assert averages == sorted(averages, reverse=True)


def test_holm_control_is_never_more_optimistic_than_raw_p_value() -> None:
    observations: list[AccuracyReplayObservation] = []
    for variant, hits in (("champion", 5), ("momentum_focus", 6)):
        for offset in range(8):
            observations.append(
                _obs(
                    variant=variant,
                    as_of=f"2025-02-{offset + 1:02d}",
                    index=offset * 5,
                    alpha_hit=1 if offset < hits else 0,
                    alpha_return=0.5 if offset < hits else -0.5,
                )
            )

    enriched = enrich_accuracy_payload(_payload(observations), observations)
    for item in enriched["results"]:
        testing = item["alpha_multiple_testing"]
        assert testing["holm_adjusted_p_value"] >= testing["exact_one_sided_p_value"]
    challenger = next(
        item for item in enriched["results"] if item["variant"] == "momentum_focus"
    )
    if challenger["alpha_research_candidate"]:
        assert challenger["alpha_multiple_testing"]["holm_significant_05"] is True
        assert (
            challenger["alpha_target"]["non_overlapping"]["alpha_hit_ci95_low_pct"]
            > 50.0
        )


def test_frozen_forward_watch_uses_only_post_freeze_observations() -> None:
    observations: list[AccuracyReplayObservation] = []
    for variant, horizon in FROZEN_ALPHA_CANDIDATES:
        observations.extend(
            [
                _obs(
                    variant=variant,
                    horizon=horizon,
                    as_of="2026-08-07",
                    index=0,
                    alpha_return=0.8,
                ),
                _obs(
                    variant=variant,
                    horizon=horizon,
                    as_of=FORWARD_FREEZE_DATE,
                    index=horizon,
                    alpha_return=0.9,
                ),
            ]
        )

    enriched = enrich_accuracy_payload(_payload(observations), observations)
    watch = enriched["forward_alpha_watch"]

    assert watch["freeze_date"] == FORWARD_FREEZE_DATE
    assert watch["auto_promotion"] is False
    assert [
        (item["variant"], item["horizon_days"]) for item in watch["candidates"]
    ] == list(FROZEN_ALPHA_CANDIDATES)
    for item in watch["candidates"]:
        assert item["discovery"]["samples"] == 1
        assert item["forward"]["samples"] == 1
        assert item["status"] == "collecting"


def test_research_governance_never_enables_automatic_production_changes() -> None:
    observations = [
        _obs(as_of="2025-01-02", index=0),
        _obs(as_of="2025-01-09", index=5),
        _obs(as_of="2025-01-16", index=10),
    ]

    enriched = enrich_accuracy_payload(_payload(observations), observations)

    assert enriched["research_governance_version"] == "v6.4"
    assert enriched["auto_promotion"] is False
    assert enriched["auto_weight_tuning"] is False
    assert enriched["research_governance"]["production_change_allowed"] is False
    assert enriched["research_governance"]["automatic_production_action"] == "none"
