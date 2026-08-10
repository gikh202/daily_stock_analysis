from __future__ import annotations

import math
import statistics
from typing import Any, Dict, Mapping, Optional, Sequence

from .lab_replay import (
    ALPHA_CALIBRATION_BUCKETS,
    AccuracyReplayObservation,
    _alpha_metric,
    _non_overlapping,
    _signal_margin_points,
    replay_accuracy_lab,
)
from .replay import load_sqlite_series


RESEARCH_GOVERNANCE_VERSION = "v6.4"
ALPHA_YEARLY_WALK_FORWARD_METHOD = "global_alpha_non_overlap_then_calendar_year_v1"
COMMON_TIMELINE_CALIBRATION_METHOD = "global_alpha_non_overlap_then_fixed_margin_buckets_v1"
MULTIPLE_TESTING_METHOD = "holm_bonferroni_exact_one_sided_binomial_v1"
DIRECTION_DIAGNOSTICS_METHOD = "global_alpha_non_overlap_by_direction_v1"
COST_SENSITIVITY_METHOD = "global_alpha_non_overlap_fixed_total_cost_bps_v1"
FORWARD_WATCH_METHOD = "frozen_candidates_global_non_overlap_post_freeze_v1"
ALPHA_COST_BPS = (0, 10, 20, 40)
FORWARD_FREEZE_DATE = "2026-08-10"
FROZEN_ALPHA_CANDIDATES = (
    ("momentum_focus", 10),
    ("relative_strength_focus", 20),
)


def _eligible_alpha(
    rows: Sequence[AccuracyReplayObservation],
) -> list[AccuracyReplayObservation]:
    return [
        item
        for item in rows
        if item.alpha_target_hit is not None and item.alpha_trade_return_pct is not None
    ]


def _alpha_yearly_walk_forward(
    rows: Sequence[AccuracyReplayObservation],
    horizon: int,
) -> list[Dict[str, Any]]:
    eligible = _eligible_alpha(rows)
    independent = _non_overlapping(eligible, int(horizon))
    years = sorted({item.as_of[:4] for item in eligible if len(item.as_of) >= 4})
    result: list[Dict[str, Any]] = []
    for year in years:
        raw_rows = [item for item in eligible if item.as_of.startswith(year)]
        independent_rows = [item for item in independent if item.as_of.startswith(year)]
        result.append(
            {
                "year": year,
                "raw": _alpha_metric(raw_rows),
                "non_overlapping": _alpha_metric(independent_rows),
            }
        )
    return result


def _bucket_for_margin(margin: Optional[float]) -> Optional[str]:
    if margin is None:
        return None
    for minimum, maximum, label in ALPHA_CALIBRATION_BUCKETS:
        if margin < float(minimum):
            continue
        if maximum is not None and margin >= float(maximum):
            continue
        return label
    return None


def _common_timeline_calibration(
    rows: Sequence[AccuracyReplayObservation],
    horizon: int,
) -> Dict[str, Any]:
    eligible = _eligible_alpha(rows)
    independent = _non_overlapping(eligible, int(horizon))
    scored = [item for item in eligible if _signal_margin_points(item) is not None]
    independent_scored = [
        item for item in independent if _signal_margin_points(item) is not None
    ]
    buckets: list[Dict[str, Any]] = []
    for minimum, maximum, label in ALPHA_CALIBRATION_BUCKETS:
        raw_rows = [
            item
            for item in scored
            if _bucket_for_margin(_signal_margin_points(item)) == label
        ]
        independent_rows = [
            item
            for item in independent_scored
            if _bucket_for_margin(_signal_margin_points(item)) == label
        ]
        buckets.append(
            {
                "label": label,
                "min_margin_points": minimum,
                "max_margin_points_exclusive": maximum,
                "raw_share_pct": (
                    0.0 if not eligible else round(100.0 * len(raw_rows) / len(eligible), 2)
                ),
                "independent_share_pct": (
                    0.0
                    if not independent
                    else round(100.0 * len(independent_rows) / len(independent), 2)
                ),
                "raw": _alpha_metric(raw_rows),
                "non_overlapping": _alpha_metric(independent_rows),
            }
        )
    unscored_raw = [item for item in eligible if _signal_margin_points(item) is None]
    unscored_independent = [
        item for item in independent if _signal_margin_points(item) is None
    ]
    return {
        "eligible_samples": len(eligible),
        "independent_samples": len(independent),
        "scored_samples": len(scored),
        "independent_scored_samples": len(independent_scored),
        "unscored": {
            "raw": _alpha_metric(unscored_raw),
            "non_overlapping": _alpha_metric(unscored_independent),
        },
        "buckets": buckets,
    }


def _direction_diagnostics(
    rows: Sequence[AccuracyReplayObservation],
    horizon: int,
) -> list[Dict[str, Any]]:
    eligible = _eligible_alpha(rows)
    independent = _non_overlapping(eligible, int(horizon))
    result: list[Dict[str, Any]] = []
    for direction in ("bullish", "bearish"):
        raw_rows = [item for item in eligible if item.direction == direction]
        independent_rows = [item for item in independent if item.direction == direction]
        result.append(
            {
                "direction": direction,
                "raw_share_pct": (
                    0.0 if not eligible else round(100.0 * len(raw_rows) / len(eligible), 2)
                ),
                "independent_share_pct": (
                    0.0
                    if not independent
                    else round(100.0 * len(independent_rows) / len(independent), 2)
                ),
                "raw": _alpha_metric(raw_rows),
                "non_overlapping": _alpha_metric(independent_rows),
            }
        )
    return result


def _net_alpha_metric(
    rows: Sequence[AccuracyReplayObservation],
    *,
    total_cost_bps: int,
) -> Dict[str, Any]:
    eligible = _eligible_alpha(rows)
    cost_pct = float(total_cost_bps) / 100.0
    net_returns = [
        float(item.alpha_trade_return_pct) - cost_pct
        for item in eligible
        if item.alpha_trade_return_pct is not None
    ]
    n = len(net_returns)
    positive = sum(1 for value in net_returns if value > 0.0)
    from .accuracy_lab import wilson_interval

    low, high = wilson_interval(positive, n)
    return {
        "samples": n,
        "total_cost_bps": int(total_cost_bps),
        "avg_net_alpha_trade_return_pct": (
            None if not net_returns else round(statistics.fmean(net_returns), 4)
        ),
        "median_net_alpha_trade_return_pct": (
            None if not net_returns else round(statistics.median(net_returns), 4)
        ),
        "positive_net_alpha_rate_pct": (
            None if n == 0 else round(100.0 * positive / n, 2)
        ),
        "positive_net_alpha_ci95_low_pct": low,
        "positive_net_alpha_ci95_high_pct": high,
    }


def _cost_sensitivity(
    rows: Sequence[AccuracyReplayObservation],
    horizon: int,
) -> list[Dict[str, Any]]:
    independent = _non_overlapping(_eligible_alpha(rows), int(horizon))
    return [
        _net_alpha_metric(independent, total_cost_bps=int(cost_bps))
        for cost_bps in ALPHA_COST_BPS
    ]


def _exact_one_sided_binomial_pvalue(hits: int, n: int) -> float:
    n_i = max(0, int(n))
    hits_i = min(max(0, int(hits)), n_i)
    if n_i <= 0:
        return 1.0
    numerator = sum(math.comb(n_i, value) for value in range(hits_i, n_i + 1))
    return float(numerator / (2 ** n_i))


def _holm_adjust(p_values: Sequence[float]) -> list[float]:
    count = len(p_values)
    if count == 0:
        return []
    indexed = sorted(
        enumerate(float(max(0.0, min(1.0, value))) for value in p_values),
        key=lambda item: (item[1], item[0]),
    )
    adjusted = [1.0] * count
    running = 0.0
    for rank, (original_index, p_value) in enumerate(indexed):
        candidate = min(1.0, (count - rank) * p_value)
        running = max(running, candidate)
        adjusted[original_index] = min(1.0, running)
    return adjusted


def _multiple_testing(
    payload: Mapping[str, Any],
    observations: Sequence[AccuracyReplayObservation],
) -> tuple[Dict[tuple[str, int], Dict[str, Any]], list[Dict[str, Any]]]:
    result_rows = list(payload.get("results") or [])
    lookup = {
        (str(item.get("variant") or ""), int(item.get("horizon_days") or 0)): item
        for item in result_rows
    }
    keys = sorted(
        lookup,
        key=lambda item: (item[0] != "champion", item[0], item[1]),
    )
    raw_p_values: list[float] = []
    temporary: list[tuple[tuple[str, int], int, int, float]] = []
    for key in keys:
        variant, horizon = key
        rows = [
            item
            for item in observations
            if item.variant == variant and int(item.horizon_days) == int(horizon)
        ]
        independent = _non_overlapping(_eligible_alpha(rows), int(horizon))
        n = len(independent)
        hits = sum(int(item.alpha_target_hit or 0) for item in independent)
        raw_p = _exact_one_sided_binomial_pvalue(hits, n)
        raw_p_values.append(raw_p)
        temporary.append((key, n, hits, raw_p))

    adjusted_values = _holm_adjust(raw_p_values)
    mapping: Dict[tuple[str, int], Dict[str, Any]] = {}
    candidates: list[Dict[str, Any]] = []
    floor = max(
        int(payload.get("minimum_samples") or 50),
        int(payload.get("promotion_min_samples") or 100),
    )
    for (key, n, hits, raw_p), adjusted in zip(temporary, adjusted_values):
        base = lookup[key]
        alpha_metric = (base.get("alpha_target") or {}).get("non_overlapping") or {}
        ci_low = alpha_metric.get("alpha_hit_ci95_low_pct")
        avg_alpha = alpha_metric.get("avg_alpha_trade_return_pct")
        variant, horizon = key
        significant = bool(adjusted < 0.05)
        research_candidate = bool(
            variant != "champion"
            and n >= floor
            and ci_low is not None
            and float(ci_low) > 50.0
            and avg_alpha is not None
            and float(avg_alpha) > 0.0
            and significant
        )
        record = {
            "family_size": len(keys),
            "samples": n,
            "hits": hits,
            "exact_one_sided_p_value": round(raw_p, 8),
            "holm_adjusted_p_value": round(float(adjusted), 8),
            "holm_significant_05": significant,
            "alpha_research_candidate": research_candidate,
        }
        mapping[key] = record
        if research_candidate:
            candidates.append(
                {
                    "variant": variant,
                    "horizon_days": horizon,
                    "holm_adjusted_p_value": record["holm_adjusted_p_value"],
                }
            )
    return mapping, candidates


def _forward_watch(
    payload: Mapping[str, Any],
    observations: Sequence[AccuracyReplayObservation],
) -> Dict[str, Any]:
    floor = max(3, int(payload.get("minimum_samples") or 50))
    records: list[Dict[str, Any]] = []
    raw_forward_p_values: list[float] = []
    for variant, horizon in FROZEN_ALPHA_CANDIDATES:
        rows = [
            item
            for item in observations
            if item.variant == variant and int(item.horizon_days) == int(horizon)
        ]
        independent = _non_overlapping(_eligible_alpha(rows), int(horizon))
        discovery = [item for item in independent if item.as_of < FORWARD_FREEZE_DATE]
        forward = [item for item in independent if item.as_of >= FORWARD_FREEZE_DATE]
        forward_metric = _alpha_metric(forward)
        n = int(forward_metric.get("samples") or 0)
        hits = sum(int(item.alpha_target_hit or 0) for item in forward)
        raw_p = _exact_one_sided_binomial_pvalue(hits, n)
        raw_forward_p_values.append(raw_p)
        records.append(
            {
                "variant": variant,
                "horizon_days": horizon,
                "freeze_date": FORWARD_FREEZE_DATE,
                "selection_state": "frozen",
                "discovery": _alpha_metric(discovery),
                "forward": forward_metric,
                "_raw_forward_p": raw_p,
            }
        )

    adjusted_values = _holm_adjust(raw_forward_p_values)
    ready = 0
    for record, adjusted in zip(records, adjusted_values):
        forward = record["forward"]
        n = int(forward.get("samples") or 0)
        ci_low = forward.get("alpha_hit_ci95_low_pct")
        avg_alpha = forward.get("avg_alpha_trade_return_pct")
        significant = bool(adjusted < 0.05)
        if n == 0:
            status = "waiting_for_outcomes"
        elif n < floor:
            status = "collecting"
        elif (
            ci_low is not None
            and float(ci_low) > 50.0
            and avg_alpha is not None
            and float(avg_alpha) > 0.0
            and significant
        ):
            status = "ready_for_manual_review"
            ready += 1
        else:
            status = "not_confirmed"
        raw_p = record.pop("_raw_forward_p")
        record["exact_one_sided_p_value"] = round(raw_p, 8)
        record["holm_adjusted_p_value"] = round(float(adjusted), 8)
        record["holm_significant_05"] = significant
        record["minimum_forward_samples"] = floor
        record["status"] = status

    return {
        "method": FORWARD_WATCH_METHOD,
        "freeze_date": FORWARD_FREEZE_DATE,
        "selection_basis": (
            "Frozen after the 2026-08-10 V6.3 historical discovery; "
            "post-freeze observations cannot reselect the watched candidates."
        ),
        "candidate_family_size": len(records),
        "ready_for_manual_review": ready,
        "auto_promotion": False,
        "candidates": records,
    }


def enrich_accuracy_payload(
    payload: Mapping[str, Any],
    observations: Sequence[AccuracyReplayObservation],
) -> Dict[str, Any]:
    enriched: Dict[str, Any] = dict(payload)
    result_rows = [dict(item) for item in list(payload.get("results") or [])]
    multiple_testing, alpha_candidates = _multiple_testing(payload, observations)

    for item in result_rows:
        variant = str(item.get("variant") or "")
        horizon = int(item.get("horizon_days") or 0)
        rows = [
            observation
            for observation in observations
            if observation.variant == variant
            and int(observation.horizon_days) == horizon
        ]
        item["alpha_yearly_walk_forward"] = _alpha_yearly_walk_forward(rows, horizon)
        item["alpha_calibration_common_timeline"] = _common_timeline_calibration(
            rows, horizon
        )
        item["alpha_direction_diagnostics"] = _direction_diagnostics(rows, horizon)
        item["alpha_cost_sensitivity"] = _cost_sensitivity(rows, horizon)
        item["alpha_multiple_testing"] = multiple_testing.get(
            (variant, horizon),
            {
                "family_size": len(multiple_testing),
                "samples": 0,
                "hits": 0,
                "exact_one_sided_p_value": 1.0,
                "holm_adjusted_p_value": 1.0,
                "holm_significant_05": False,
                "alpha_research_candidate": False,
            },
        )
        item["alpha_research_candidate"] = bool(
            item["alpha_multiple_testing"].get("alpha_research_candidate")
        )

    enriched["results"] = result_rows
    enriched["research_governance_version"] = RESEARCH_GOVERNANCE_VERSION
    enriched["alpha_yearly_walk_forward_method"] = ALPHA_YEARLY_WALK_FORWARD_METHOD
    enriched["alpha_calibration_common_timeline_method"] = (
        COMMON_TIMELINE_CALIBRATION_METHOD
    )
    enriched["alpha_multiple_testing_method"] = MULTIPLE_TESTING_METHOD
    enriched["alpha_direction_diagnostics_method"] = DIRECTION_DIAGNOSTICS_METHOD
    enriched["alpha_cost_sensitivity_method"] = COST_SENSITIVITY_METHOD
    enriched["alpha_cost_sensitivity_bps"] = list(ALPHA_COST_BPS)
    enriched["alpha_research_candidates"] = alpha_candidates
    enriched["forward_alpha_watch"] = _forward_watch(enriched, observations)
    enriched["research_governance"] = {
        "production_change_allowed": False,
        "automatic_production_action": "none",
        "manual_review_required": True,
        "multiple_testing_control": True,
        "frozen_forward_validation": True,
        "reason": (
            "Historical research can nominate hypotheses, but production weights, "
            "thresholds and Champion promotion remain manually reviewed and require "
            "forward/out-of-sample confirmation."
        ),
    }
    enriched["auto_promotion"] = False
    enriched["auto_weight_tuning"] = False
    validate_research_governance(enriched)
    return enriched


def enrich_accuracy_payload_from_stock_db(
    payload: Mapping[str, Any],
    stock_db_path: str,
    *,
    codes: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    requested = [str(code).upper() for code in (codes or []) if str(code).strip()]
    source_codes = (
        list(dict.fromkeys(requested + ["SPY", "QQQ"])) if requested else None
    )
    series = load_sqlite_series(stock_db_path, source_codes)
    observations = replay_accuracy_lab(series, codes=requested or None)
    return enrich_accuracy_payload(payload, observations)


def validate_research_governance(payload: Mapping[str, Any]) -> None:
    if payload.get("research_governance_version") != RESEARCH_GOVERNANCE_VERSION:
        raise ValueError("invalid research governance version")
    if payload.get("auto_promotion") is not False:
        raise ValueError("research governance must keep auto_promotion=false")
    if payload.get("auto_weight_tuning") is not False:
        raise ValueError("research governance must keep auto_weight_tuning=false")
    if payload.get("alpha_multiple_testing_method") != MULTIPLE_TESTING_METHOD:
        raise ValueError("invalid multiple-testing method")
    if list(payload.get("alpha_cost_sensitivity_bps") or []) != list(ALPHA_COST_BPS):
        raise ValueError("invalid alpha cost sensitivity grid")

    results = list(payload.get("results") or [])
    if not results:
        raise ValueError("research governance requires replay results")
    expected_family_size = len(results)
    for item in results:
        horizon = int(item.get("horizon_days") or 0)
        alpha = item.get("alpha_target") or {}
        alpha_raw_n = int((alpha.get("raw") or {}).get("samples") or 0)
        alpha_independent_n = int(
            (alpha.get("non_overlapping") or {}).get("samples") or 0
        )

        yearly = list(item.get("alpha_yearly_walk_forward") or [])
        if alpha_raw_n > 0 and not yearly:
            raise ValueError(f"missing alpha yearly walk-forward for {horizon}D")
        if sum(int((year.get("raw") or {}).get("samples") or 0) for year in yearly) != alpha_raw_n:
            raise ValueError(f"alpha yearly raw partition mismatch for {horizon}D")
        if (
            sum(
                int((year.get("non_overlapping") or {}).get("samples") or 0)
                for year in yearly
            )
            != alpha_independent_n
        ):
            raise ValueError(
                f"alpha yearly independent partition mismatch for {horizon}D"
            )

        common = item.get("alpha_calibration_common_timeline") or {}
        buckets = list(common.get("buckets") or [])
        if [bucket.get("label") for bucket in buckets] != [
            "0-2pt",
            "2-5pt",
            "5-10pt",
            "10pt+",
        ]:
            raise ValueError(f"invalid common-timeline calibration buckets for {horizon}D")
        independent_bucket_n = sum(
            int((bucket.get("non_overlapping") or {}).get("samples") or 0)
            for bucket in buckets
        )
        independent_unscored_n = int(
            ((common.get("unscored") or {}).get("non_overlapping") or {}).get(
                "samples"
            )
            or 0
        )
        if independent_bucket_n + independent_unscored_n != alpha_independent_n:
            raise ValueError(
                f"common-timeline calibration partition mismatch for {horizon}D"
            )

        directions = list(item.get("alpha_direction_diagnostics") or [])
        if [entry.get("direction") for entry in directions] != ["bullish", "bearish"]:
            raise ValueError(f"invalid direction diagnostics for {horizon}D")
        if (
            sum(
                int((entry.get("non_overlapping") or {}).get("samples") or 0)
                for entry in directions
            )
            != alpha_independent_n
        ):
            raise ValueError(f"direction diagnostics partition mismatch for {horizon}D")

        costs = list(item.get("alpha_cost_sensitivity") or [])
        if [int(entry.get("total_cost_bps") or 0) for entry in costs] != list(
            ALPHA_COST_BPS
        ):
            raise ValueError(f"invalid cost sensitivity grid for {horizon}D")
        if any(int(entry.get("samples") or 0) != alpha_independent_n for entry in costs):
            raise ValueError(f"cost sensitivity sample mismatch for {horizon}D")
        avg_values = [
            entry.get("avg_net_alpha_trade_return_pct")
            for entry in costs
            if entry.get("avg_net_alpha_trade_return_pct") is not None
        ]
        if any(
            float(right) > float(left) + 1e-9
            for left, right in zip(avg_values, avg_values[1:])
        ):
            raise ValueError(f"cost sensitivity must not improve with higher cost for {horizon}D")

        testing = item.get("alpha_multiple_testing") or {}
        if int(testing.get("family_size") or 0) != expected_family_size:
            raise ValueError("multiple-testing family size mismatch")
        raw_p = float(testing.get("exact_one_sided_p_value") or 0.0)
        adjusted_p = float(testing.get("holm_adjusted_p_value") or 0.0)
        if not 0.0 <= raw_p <= 1.0 or not 0.0 <= adjusted_p <= 1.0:
            raise ValueError("invalid multiple-testing probability")
        if adjusted_p + 1e-12 < raw_p:
            raise ValueError("Holm adjusted p-value cannot be below raw p-value")

    watch = payload.get("forward_alpha_watch") or {}
    if watch.get("method") != FORWARD_WATCH_METHOD:
        raise ValueError("invalid forward watch method")
    if watch.get("freeze_date") != FORWARD_FREEZE_DATE:
        raise ValueError("invalid forward watch freeze date")
    if watch.get("auto_promotion") is not False:
        raise ValueError("forward watch must not auto-promote")
    watched = list(watch.get("candidates") or [])
    expected = list(FROZEN_ALPHA_CANDIDATES)
    actual = [
        (str(item.get("variant") or ""), int(item.get("horizon_days") or 0))
        for item in watched
    ]
    if actual != expected:
        raise ValueError("frozen forward candidate set drifted")


def _fmt(value: Any, *, suffix: str = "", digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "N/A"


def _alpha_ci(metric: Mapping[str, Any]) -> str:
    low = metric.get("alpha_hit_ci95_low_pct")
    high = metric.get("alpha_hit_ci95_high_pct")
    if low is None or high is None:
        return "N/A"
    return f"{_fmt(low, suffix='%')}–{_fmt(high, suffix='%')}"


def render_research_governance_markdown(payload: Mapping[str, Any]) -> str:
    lines = [
        "## V6.4 统计治理摘要",
        "",
        "> V6.4 不改生产模型，而是把历史发现、统计显著性、成本敏感度与冻结后的 forward 验证拆开，避免在同一份历史数据上反复挑最优参数。",
        "",
        f"- 多重检验：**{payload.get('alpha_multiple_testing_method', '-')}**",
        f"- Alpha 年度稳定性：**{payload.get('alpha_yearly_walk_forward_method', '-')}**",
        f"- 统一时间轴校准：**{payload.get('alpha_calibration_common_timeline_method', '-')}**",
        f"- 成本敏感度：**{payload.get('alpha_cost_sensitivity_method', '-')}**",
        f"- 冻结候选 Forward Watch：**{(payload.get('forward_alpha_watch') or {}).get('method', '-')}**",
        f"- 经 Holm-Bonferroni 后的 Alpha 研究候选：**{len(payload.get('alpha_research_candidates') or [])}**",
        "",
        "## Alpha 多重检验（全模型 × 全周期）",
        "",
        "| 模型 | 周期 | 独立N | Alpha命中 | 平均Alpha | 原始p | Holm调整p | Holm<0.05 | Alpha研究候选 |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for item in list(payload.get("results") or []):
        alpha = (item.get("alpha_target") or {}).get("non_overlapping") or {}
        testing = item.get("alpha_multiple_testing") or {}
        lines.append(
            "| {variant} | {h}D | {n} | {hit} | {avg} | {p} | {adj} | {sig} | {candidate} |".format(
                variant=item.get("variant") or "-",
                h=item.get("horizon_days") or "-",
                n=alpha.get("samples", 0),
                hit=_fmt(alpha.get("alpha_hit_rate_pct"), suffix="%"),
                avg=_fmt(alpha.get("avg_alpha_trade_return_pct"), suffix="%"),
                p=_fmt(testing.get("exact_one_sided_p_value"), digits=4),
                adj=_fmt(testing.get("holm_adjusted_p_value"), digits=4),
                sig="是" if testing.get("holm_significant_05") else "否",
                candidate="是（研究）" if item.get("alpha_research_candidate") else "否",
            )
        )

    lines.extend(["", "## Champion Alpha 年度 Walk-forward", ""])
    lines.append(
        "| 周期 | 年份 | 原始N | 非重叠N | Alpha命中 | 95% CI | 平均Alpha | 中位Alpha |"
    )
    lines.append("|---:|---|---:|---:|---:|---|---:|---:|")
    for item in list(payload.get("results") or []):
        if item.get("variant") != "champion":
            continue
        for year in list(item.get("alpha_yearly_walk_forward") or []):
            independent = year.get("non_overlapping") or {}
            lines.append(
                "| {h}D | {year} | {raw_n} | {n} | {hit} | {ci} | {avg} | {median} |".format(
                    h=item.get("horizon_days") or "-",
                    year=year.get("year") or "-",
                    raw_n=(year.get("raw") or {}).get("samples", 0),
                    n=independent.get("samples", 0),
                    hit=_fmt(independent.get("alpha_hit_rate_pct"), suffix="%"),
                    ci=_alpha_ci(independent),
                    avg=_fmt(
                        independent.get("avg_alpha_trade_return_pct"), suffix="%"
                    ),
                    median=_fmt(
                        independent.get("median_alpha_trade_return_pct"), suffix="%"
                    ),
                )
            )

    lines.extend(["", "## Champion Common-timeline Score Calibration", ""])
    lines.append(
        "> 与 V6.3 的“每个桶先过滤再 non-overlap”策略视图并列；这里先生成唯一全局 Alpha non-overlap 时间轴，再把同一批独立样本分桶，专门用于公平比较不同分数余量的校准质量。"
    )
    lines.append("")
    lines.append(
        "| 周期 | 分数余量桶 | 独立占比 | 非重叠N | Alpha命中 | 95% CI | 平均Alpha |"
    )
    lines.append("|---:|---|---:|---:|---:|---|---:|")
    for item in list(payload.get("results") or []):
        if item.get("variant") != "champion":
            continue
        common = item.get("alpha_calibration_common_timeline") or {}
        for bucket in list(common.get("buckets") or []):
            independent = bucket.get("non_overlapping") or {}
            lines.append(
                "| {h}D | {label} | {share} | {n} | {hit} | {ci} | {avg} |".format(
                    h=item.get("horizon_days") or "-",
                    label=bucket.get("label") or "-",
                    share=_fmt(bucket.get("independent_share_pct"), suffix="%"),
                    n=independent.get("samples", 0),
                    hit=_fmt(independent.get("alpha_hit_rate_pct"), suffix="%"),
                    ci=_alpha_ci(independent),
                    avg=_fmt(
                        independent.get("avg_alpha_trade_return_pct"), suffix="%"
                    ),
                )
            )

    lines.extend(["", "## Champion 方向暴露诊断", ""])
    lines.append(
        "| 周期 | 方向 | 独立占比 | 非重叠N | Alpha命中 | 95% CI | 平均Alpha |"
    )
    lines.append("|---:|---|---:|---:|---:|---|---:|")
    for item in list(payload.get("results") or []):
        if item.get("variant") != "champion":
            continue
        for direction in list(item.get("alpha_direction_diagnostics") or []):
            independent = direction.get("non_overlapping") or {}
            lines.append(
                "| {h}D | {direction} | {share} | {n} | {hit} | {ci} | {avg} |".format(
                    h=item.get("horizon_days") or "-",
                    direction=direction.get("direction") or "-",
                    share=_fmt(direction.get("independent_share_pct"), suffix="%"),
                    n=independent.get("samples", 0),
                    hit=_fmt(independent.get("alpha_hit_rate_pct"), suffix="%"),
                    ci=_alpha_ci(independent),
                    avg=_fmt(
                        independent.get("avg_alpha_trade_return_pct"), suffix="%"
                    ),
                )
            )

    lines.extend(["", "## Champion Alpha 成本敏感度", ""])
    lines.append(
        "> 这里把固定总摩擦成本直接从每条独立 Alpha Spread 中扣除，仅做压力测试；不包含真实滑点、借券可得性、融资、税费或组合仓位，因此仍不是可执行 P&L。"
    )
    lines.append("")
    lines.append(
        "| 周期 | 总成本 | 非重叠N | 平均净Alpha | 中位净Alpha | 净Alpha>0 | 95% CI |"
    )
    lines.append("|---:|---:|---:|---:|---:|---:|---|")
    for item in list(payload.get("results") or []):
        if item.get("variant") != "champion":
            continue
        for cost in list(item.get("alpha_cost_sensitivity") or []):
            ci = "N/A"
            low = cost.get("positive_net_alpha_ci95_low_pct")
            high = cost.get("positive_net_alpha_ci95_high_pct")
            if low is not None and high is not None:
                ci = f"{_fmt(low, suffix='%')}–{_fmt(high, suffix='%')}"
            lines.append(
                "| {h}D | {cost}bps | {n} | {avg} | {median} | {positive} | {ci} |".format(
                    h=item.get("horizon_days") or "-",
                    cost=cost.get("total_cost_bps", 0),
                    n=cost.get("samples", 0),
                    avg=_fmt(
                        cost.get("avg_net_alpha_trade_return_pct"), suffix="%"
                    ),
                    median=_fmt(
                        cost.get("median_net_alpha_trade_return_pct"), suffix="%"
                    ),
                    positive=_fmt(
                        cost.get("positive_net_alpha_rate_pct"), suffix="%"
                    ),
                    ci=ci,
                )
            )

    watch = payload.get("forward_alpha_watch") or {}
    lines.extend(["", "## 冻结候选 Forward Watch", ""])
    lines.append(
        f"> 冻结日期：**{watch.get('freeze_date', '-')}**。候选集合冻结后不再根据同一历史区间重新选择；只有冻结日及之后的新预测/成熟 outcome 才能提供 forward confirmation。"
    )
    lines.append("")
    lines.append(
        "| 候选 | 周期 | Discovery N | Discovery Alpha命中 | Forward N | Forward Alpha命中 | Forward 95% CI | Forward平均Alpha | Holm调整p | 状态 |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---|---:|---:|---|")
    for record in list(watch.get("candidates") or []):
        discovery = record.get("discovery") or {}
        forward = record.get("forward") or {}
        lines.append(
            "| {variant} | {h}D | {dn} | {dhit} | {fn} | {fhit} | {ci} | {avg} | {p} | {status} |".format(
                variant=record.get("variant") or "-",
                h=record.get("horizon_days") or "-",
                dn=discovery.get("samples", 0),
                dhit=_fmt(discovery.get("alpha_hit_rate_pct"), suffix="%"),
                fn=forward.get("samples", 0),
                fhit=_fmt(forward.get("alpha_hit_rate_pct"), suffix="%"),
                ci=_alpha_ci(forward),
                avg=_fmt(forward.get("avg_alpha_trade_return_pct"), suffix="%"),
                p=_fmt(record.get("holm_adjusted_p_value"), digits=4),
                status=record.get("status") or "-",
            )
        )

    lines.extend(
        [
            "",
            "### V6.4 生产边界",
            "",
            "- 历史研究只负责提出假设；不会根据一次历史最优结果自动修改 Champion、阈值、Regime Gate 或权重。",
            "- Alpha 候选先经过全模型/周期 Holm-Bonferroni 多重检验；冻结候选随后只使用新产生的 forward 样本验证。",
            "- `auto_promotion=false`、`auto_weight_tuning=false` 保持硬关闭；任何生产变更仍需要独立 PR、人工审查和明确回滚方案。",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "ALPHA_COST_BPS",
    "ALPHA_YEARLY_WALK_FORWARD_METHOD",
    "COMMON_TIMELINE_CALIBRATION_METHOD",
    "COST_SENSITIVITY_METHOD",
    "DIRECTION_DIAGNOSTICS_METHOD",
    "FORWARD_FREEZE_DATE",
    "FORWARD_WATCH_METHOD",
    "FROZEN_ALPHA_CANDIDATES",
    "MULTIPLE_TESTING_METHOD",
    "RESEARCH_GOVERNANCE_VERSION",
    "enrich_accuracy_payload",
    "enrich_accuracy_payload_from_stock_db",
    "render_research_governance_markdown",
    "validate_research_governance",
]
