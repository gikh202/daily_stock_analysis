from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence

from .accuracy import (
    HORIZONS,
    HORIZON_BEARISH_THRESHOLD,
    HORIZON_BULLISH_THRESHOLD,
    build_horizon_forecasts,
    classify_instrument,
)
from .accuracy_lab import build_shadow_forecasts, wilson_interval
from .replay import _features_at, _finite, load_sqlite_series


STRATEGY_RETURN_METHOD = "gross_directional_position_v1"
YEARLY_WALK_FORWARD_METHOD = "raw_and_global_non_overlapping_by_calendar_year_v2"
SELECTIVITY_ANALYSIS_METHOD = "directional_margin_filter_then_global_non_overlap_v1"
SELECTIVITY_MARGIN_THRESHOLDS = (0.0, 2.0, 5.0, 10.0)
ALPHA_TARGET_METHOD = "spy_relative_directional_filter_then_global_non_overlap_v1"
ALPHA_CALIBRATION_METHOD = "fixed_directional_margin_buckets_v1"
REGIME_MATRIX_METHOD = "global_alpha_non_overlap_then_asof_spy_regime_partition_v1"
ALPHA_CALIBRATION_BUCKETS = (
    (0.0, 2.0, "0-2pt"),
    (2.0, 5.0, "2-5pt"),
    (5.0, 10.0, "5-10pt"),
    (10.0, None, "10pt+"),
)


@dataclass(frozen=True)
class AccuracyReplayObservation:
    variant: str
    code: str
    as_of: str
    as_of_index: int
    horizon_days: int
    score: Optional[float]
    direction: str
    future_return_pct: float
    directional_hit: int
    excess_vs_spy_pct: Optional[float]
    strategy_return_pct: float
    strategy_excess_vs_spy_pct: Optional[float]
    alpha_target_hit: Optional[int] = None
    alpha_trade_return_pct: Optional[float] = None
    spy_trend_regime: Optional[str] = None
    spy_vol_regime: Optional[str] = None


def _spy_future_return(
    series: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    as_of: str,
    horizon: int,
) -> Optional[float]:
    rows = list(series.get("SPY") or ())
    date_to_index = {str(row.get("date") or ""): index for index, row in enumerate(rows)}
    index = date_to_index.get(str(as_of))
    if index is None or index + int(horizon) >= len(rows):
        return None
    start = _finite(rows[index].get("close"))
    end = _finite(rows[index + int(horizon)].get("close"))
    if start is None or end is None or start <= 0:
        return None
    return (end / start - 1.0) * 100.0


def _trailing_return(closes: Sequence[float], days: int) -> Optional[float]:
    if len(closes) <= int(days) or closes[-int(days) - 1] <= 0:
        return None
    return (closes[-1] / closes[-int(days) - 1] - 1.0) * 100.0


def _annualized_volatility(closes: Sequence[float], window: int) -> Optional[float]:
    if len(closes) < int(window) + 1:
        return None
    returns = []
    sample = closes[-int(window) - 1 :]
    for left, right in zip(sample, sample[1:]):
        if left > 0:
            returns.append((right / left - 1.0) * 100.0)
    if len(returns) < 2:
        return None
    return statistics.pstdev(returns) * math.sqrt(252.0)


def _spy_market_regime(
    series: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    as_of: str,
) -> tuple[str, str]:
    rows = list(series.get("SPY") or ())
    date_to_index = {str(row.get("date") or ""): index for index, row in enumerate(rows)}
    index = date_to_index.get(str(as_of))
    if index is None:
        return "unknown", "unknown"
    closes = [
        value
        for value in (_finite(row.get("close")) for row in rows[: index + 1])
        if value is not None and value > 0
    ]
    ret20 = _trailing_return(closes, 20)
    ret60 = _trailing_return(closes, 60)
    if ret20 is None or ret60 is None:
        trend = "unknown"
    elif ret20 > 0.0 and ret60 > 0.0:
        trend = "up"
    elif ret20 < 0.0 and ret60 < 0.0:
        trend = "down"
    else:
        trend = "mixed"

    vol20 = _annualized_volatility(closes, 20)
    vol60 = _annualized_volatility(closes, 60)
    if vol20 is None or vol60 is None:
        volatility = "unknown"
    else:
        volatility = "expanding" if vol20 >= vol60 else "contracting"
    return trend, volatility


def _directional_position(direction: str) -> float:
    normalized = str(direction or "").strip().lower()
    if normalized == "bullish":
        return 1.0
    if normalized == "bearish":
        return -1.0
    return 0.0


def replay_accuracy_lab(
    series: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    codes: Optional[Sequence[str]] = None,
    min_lookback: int = 60,
    horizons: Sequence[int] = HORIZONS,
    neutral_band_pct: float = 2.0,
) -> list[AccuracyReplayObservation]:
    selected = [
        str(code).upper()
        for code in (codes or series.keys())
        if str(code).upper() not in {"SPY", "QQQ"}
    ]
    benchmarks = {
        code: {str(row.get("date")): dict(row) for row in series.get(code, ()) if row.get("date")}
        for code in ("SPY", "QQQ")
    }
    max_horizon = max(int(value) for value in horizons)
    observations: list[AccuracyReplayObservation] = []

    for code in selected:
        rows = list(series.get(code) or ())
        if len(rows) <= int(min_lookback) + max_horizon:
            continue
        instrument = classify_instrument(code, {})
        for index in range(int(min_lookback), len(rows) - max_horizon):
            current = _finite(rows[index].get("close"))
            if current is None or current <= 0:
                continue
            features = _features_at(code, rows, index, benchmarks)
            variants: Dict[str, Dict[str, Dict[str, Any]]] = {
                "champion": build_horizon_forecasts(features, instrument_type=instrument),
                **build_shadow_forecasts(features, instrument_type=instrument),
            }
            as_of = str(rows[index].get("date") or "")
            spy_trend_regime, spy_vol_regime = _spy_market_regime(series, as_of=as_of)
            for horizon in horizons:
                horizon_i = int(horizon)
                future = _finite(rows[index + horizon_i].get("close"))
                if future is None or future <= 0:
                    continue
                future_return = (future / current - 1.0) * 100.0
                spy_return = _spy_future_return(series, as_of=as_of, horizon=horizon_i)
                underlying_excess = None if spy_return is None else future_return - spy_return
                for variant, forecast in variants.items():
                    block = forecast.get(f"{horizon_i}d") or {}
                    direction = str(block.get("direction") or "neutral")
                    if direction == "bullish":
                        hit = int(future_return > 0.0)
                    elif direction == "bearish":
                        hit = int(future_return < 0.0)
                    else:
                        hit = int(abs(future_return) <= abs(float(neutral_band_pct)))
                    position = _directional_position(direction)
                    strategy_return = position * future_return
                    strategy_excess = (
                        None if spy_return is None else strategy_return - spy_return
                    )
                    alpha_target_hit = None
                    alpha_trade_return = None
                    if position != 0.0 and underlying_excess is not None:
                        alpha_target_hit = int(
                            underlying_excess > 0.0 if position > 0.0 else underlying_excess < 0.0
                        )
                        alpha_trade_return = position * underlying_excess
                    observations.append(
                        AccuracyReplayObservation(
                            variant=variant,
                            code=code,
                            as_of=as_of,
                            as_of_index=index,
                            horizon_days=horizon_i,
                            score=_finite(block.get("score")),
                            direction=direction,
                            future_return_pct=round(future_return, 6),
                            directional_hit=hit,
                            excess_vs_spy_pct=(
                                None if underlying_excess is None else round(underlying_excess, 6)
                            ),
                            strategy_return_pct=round(strategy_return, 6),
                            strategy_excess_vs_spy_pct=(
                                None if strategy_excess is None else round(strategy_excess, 6)
                            ),
                            alpha_target_hit=alpha_target_hit,
                            alpha_trade_return_pct=(
                                None if alpha_trade_return is None else round(alpha_trade_return, 6)
                            ),
                            spy_trend_regime=spy_trend_regime,
                            spy_vol_regime=spy_vol_regime,
                        )
                    )
    return observations


def _metric(rows: Sequence[AccuracyReplayObservation]) -> Dict[str, Any]:
    n = len(rows)
    hits = sum(int(item.directional_hit) for item in rows)
    low, high = wilson_interval(hits, n)
    strategy_returns = [item.strategy_return_pct for item in rows]
    strategy_excess = [
        item.strategy_excess_vs_spy_pct
        for item in rows
        if item.strategy_excess_vs_spy_pct is not None
    ]
    underlying_returns = [item.future_return_pct for item in rows]
    underlying_excess = [
        item.excess_vs_spy_pct for item in rows if item.excess_vs_spy_pct is not None
    ]
    return {
        "samples": n,
        "directional_hit_rate_pct": None if n == 0 else round(100.0 * hits / n, 2),
        "hit_rate_ci95_low_pct": low,
        "hit_rate_ci95_high_pct": high,
        "avg_return_pct": None if not strategy_returns else round(statistics.fmean(strategy_returns), 4),
        "avg_excess_vs_spy_pct": None if not strategy_excess else round(statistics.fmean(strategy_excess), 4),
        "avg_underlying_return_pct": None if not underlying_returns else round(statistics.fmean(underlying_returns), 4),
        "avg_underlying_excess_vs_spy_pct": None if not underlying_excess else round(statistics.fmean(underlying_excess), 4),
    }


def _alpha_metric(rows: Sequence[AccuracyReplayObservation]) -> Dict[str, Any]:
    eligible = [
        item
        for item in rows
        if item.alpha_target_hit is not None and item.alpha_trade_return_pct is not None
    ]
    n = len(eligible)
    hits = sum(int(item.alpha_target_hit or 0) for item in eligible)
    low, high = wilson_interval(hits, n)
    alpha_returns = [float(item.alpha_trade_return_pct) for item in eligible]
    return {
        "samples": n,
        "alpha_hit_rate_pct": None if n == 0 else round(100.0 * hits / n, 2),
        "alpha_hit_ci95_low_pct": low,
        "alpha_hit_ci95_high_pct": high,
        "avg_alpha_trade_return_pct": (
            None if not alpha_returns else round(statistics.fmean(alpha_returns), 4)
        ),
        "median_alpha_trade_return_pct": (
            None if not alpha_returns else round(statistics.median(alpha_returns), 4)
        ),
    }


def _non_overlapping(rows: Sequence[AccuracyReplayObservation], horizon: int) -> list[AccuracyReplayObservation]:
    selected: list[AccuracyReplayObservation] = []
    last: Dict[str, int] = {}
    for item in sorted(rows, key=lambda row: (row.code, row.as_of_index, row.variant)):
        previous = last.get(item.code)
        if previous is None or item.as_of_index - previous >= int(horizon):
            selected.append(item)
            last[item.code] = item.as_of_index
    return selected


def _signal_margin_points(item: AccuracyReplayObservation) -> Optional[float]:
    score = _finite(item.score)
    horizon = int(item.horizon_days)
    direction = str(item.direction or "").strip().lower()
    if score is None:
        return None
    if direction == "bullish":
        threshold = HORIZON_BULLISH_THRESHOLD.get(horizon)
        return None if threshold is None else max(0.0, float(score) - float(threshold))
    if direction == "bearish":
        threshold = HORIZON_BEARISH_THRESHOLD.get(horizon)
        return None if threshold is None else max(0.0, float(threshold) - float(score))
    return None


def _selectivity_analysis(
    rows: Sequence[AccuracyReplayObservation],
    horizon: int,
) -> list[Dict[str, Any]]:
    total = len(rows)
    directional = [item for item in rows if _signal_margin_points(item) is not None]
    directional_total = len(directional)
    result: list[Dict[str, Any]] = []
    for threshold in SELECTIVITY_MARGIN_THRESHOLDS:
        qualified = [
            item
            for item in directional
            if (_signal_margin_points(item) or 0.0) >= float(threshold)
        ]
        independent = _non_overlapping(qualified, int(horizon))
        result.append(
            {
                "min_margin_points": float(threshold),
                "participation_rate_pct": (
                    0.0 if total <= 0 else round(100.0 * len(qualified) / total, 2)
                ),
                "directional_capture_rate_pct": (
                    0.0
                    if directional_total <= 0
                    else round(100.0 * len(qualified) / directional_total, 2)
                ),
                "raw": _metric(qualified),
                "non_overlapping": _metric(independent),
            }
        )
    return result


def _alpha_target_analysis(
    rows: Sequence[AccuracyReplayObservation],
    horizon: int,
) -> Dict[str, Any]:
    directional = [item for item in rows if item.alpha_target_hit is not None]
    independent = _non_overlapping(directional, int(horizon))
    return {
        "raw": _alpha_metric(directional),
        "non_overlapping": _alpha_metric(independent),
    }


def _alpha_calibration(
    rows: Sequence[AccuracyReplayObservation],
    horizon: int,
) -> list[Dict[str, Any]]:
    eligible = [item for item in rows if item.alpha_target_hit is not None]
    total = len(eligible)
    result: list[Dict[str, Any]] = []
    for minimum, maximum, label in ALPHA_CALIBRATION_BUCKETS:
        qualified = []
        for item in eligible:
            margin = _signal_margin_points(item)
            if margin is None or margin < minimum:
                continue
            if maximum is not None and margin >= maximum:
                continue
            qualified.append(item)
        independent = _non_overlapping(qualified, int(horizon))
        result.append(
            {
                "label": label,
                "min_margin_points": minimum,
                "max_margin_points_exclusive": maximum,
                "directional_share_pct": (
                    0.0 if total <= 0 else round(100.0 * len(qualified) / total, 2)
                ),
                "raw": _alpha_metric(qualified),
                "non_overlapping": _alpha_metric(independent),
            }
        )
    return result


def _regime_matrix(
    rows: Sequence[AccuracyReplayObservation],
    horizon: int,
) -> list[Dict[str, Any]]:
    eligible = [item for item in rows if item.alpha_target_hit is not None]
    independent = _non_overlapping(eligible, int(horizon))
    keys = sorted(
        {
            (item.spy_trend_regime or "unknown", item.spy_vol_regime or "unknown")
            for item in eligible
        }
    )
    result: list[Dict[str, Any]] = []
    for trend_regime, vol_regime in keys:
        raw_rows = [
            item
            for item in eligible
            if (item.spy_trend_regime or "unknown") == trend_regime
            and (item.spy_vol_regime or "unknown") == vol_regime
        ]
        independent_rows = [
            item
            for item in independent
            if (item.spy_trend_regime or "unknown") == trend_regime
            and (item.spy_vol_regime or "unknown") == vol_regime
        ]
        result.append(
            {
                "spy_trend_regime": trend_regime,
                "spy_vol_regime": vol_regime,
                "raw": _alpha_metric(raw_rows),
                "non_overlapping": _alpha_metric(independent_rows),
            }
        )
    return result


def summarize_accuracy_replay(
    observations: Sequence[AccuracyReplayObservation],
    *,
    min_samples: int = 50,
    promotion_min_samples: int = 100,
) -> Dict[str, Any]:
    rows: list[Dict[str, Any]] = []
    variants = sorted({item.variant for item in observations}, key=lambda value: (value != "champion", value))
    horizons = sorted({item.horizon_days for item in observations})
    for variant in variants:
        for horizon in horizons:
            bucket = [
                item for item in observations
                if item.variant == variant and item.horizon_days == horizon
            ]
            if not bucket:
                continue
            independent = _non_overlapping(bucket, horizon)
            yearly = []
            for year in sorted({item.as_of[:4] for item in bucket if len(item.as_of) >= 4}):
                yearly_rows = [item for item in bucket if item.as_of.startswith(year)]
                yearly_independent = [
                    item for item in independent if item.as_of.startswith(year)
                ]
                raw_metric = _metric(yearly_rows)
                independent_metric = _metric(yearly_independent)
                yearly.append(
                    {
                        "year": year,
                        "raw": raw_metric,
                        "non_overlapping": independent_metric,
                        # Preserve the pre-v2 flattened yearly fields as the raw view for
                        # downstream readers while the report migrates to explicit views.
                        **raw_metric,
                    }
                )
            rows.append(
                {
                    "variant": variant,
                    "horizon_days": horizon,
                    "raw": _metric(bucket),
                    "non_overlapping": _metric(independent),
                    "yearly_walk_forward": yearly,
                    "selectivity_analysis": _selectivity_analysis(bucket, horizon),
                    "alpha_target": _alpha_target_analysis(bucket, horizon),
                    "alpha_calibration": _alpha_calibration(bucket, horizon),
                    "regime_matrix": _regime_matrix(bucket, horizon),
                }
            )

    champion = {
        int(item["horizon_days"]): item
        for item in rows
        if item["variant"] == "champion"
    }
    candidates = []
    floor = max(int(promotion_min_samples), int(min_samples))
    for item in rows:
        if item["variant"] == "champion":
            continue
        base = champion.get(int(item["horizon_days"])) or {}
        challenger = item.get("non_overlapping") or {}
        champion_metric = base.get("non_overlapping") or {}
        n = int(challenger.get("samples") or 0)
        base_n = int(champion_metric.get("samples") or 0)
        hit = challenger.get("directional_hit_rate_pct")
        base_hit = champion_metric.get("directional_hit_rate_pct")
        low = challenger.get("hit_rate_ci95_low_pct")
        base_low = champion_metric.get("hit_rate_ci95_low_pct")
        challenger_excess = challenger.get("avg_excess_vs_spy_pct")
        base_excess = champion_metric.get("avg_excess_vs_spy_pct")
        candidate = bool(
            n >= floor
            and base_n >= floor
            and hit is not None
            and base_hit is not None
            and low is not None
            and base_low is not None
            and float(low) > 50.0
            and float(low) >= float(base_low)
            and float(hit) >= float(base_hit) + 2.0
            and (
                challenger_excess is None
                or base_excess is None
                or float(challenger_excess) >= float(base_excess)
            )
        )
        item["hit_rate_delta_vs_champion_pp"] = (
            None if hit is None or base_hit is None else round(float(hit) - float(base_hit), 2)
        )
        item["promotion_candidate"] = candidate
        if candidate:
            candidates.append({"variant": item["variant"], "horizon_days": item["horizon_days"]})

    return {
        "method": "strict no-lookahead rolling price-feature replay",
        "scope": "price/volume/benchmark features only; current SEC/FRED snapshots are excluded from historical replay",
        "strategy_return_method": STRATEGY_RETURN_METHOD,
        "yearly_walk_forward_method": YEARLY_WALK_FORWARD_METHOD,
        "selectivity_analysis_method": SELECTIVITY_ANALYSIS_METHOD,
        "selectivity_margin_thresholds": list(SELECTIVITY_MARGIN_THRESHOLDS),
        "alpha_target_method": ALPHA_TARGET_METHOD,
        "alpha_calibration_method": ALPHA_CALIBRATION_METHOD,
        "alpha_calibration_buckets": [
            {
                "label": label,
                "min_margin_points": minimum,
                "max_margin_points_exclusive": maximum,
            }
            for minimum, maximum, label in ALPHA_CALIBRATION_BUCKETS
        ],
        "regime_matrix_method": REGIME_MATRIX_METHOD,
        "alpha_target_definition": {
            "benchmark": "SPY",
            "bullish_hit": "stock_return_pct - spy_return_pct > 0",
            "bearish_hit": "stock_return_pct - spy_return_pct < 0",
            "neutral": "excluded",
            "alpha_trade_return": "directional_position * (stock_return_pct - spy_return_pct)",
            "trading_costs": "excluded",
        },
        "strategy_return_definition": {
            "bullish_position": 1.0,
            "bearish_position": -1.0,
            "neutral_position": 0.0,
            "benchmark": "SPY long-only",
            "trading_costs": "excluded",
        },
        "minimum_samples": max(3, int(min_samples)),
        "promotion_min_samples": floor,
        "observations": len(observations),
        "results": rows,
        "promotion_candidates": candidates,
        "auto_promotion": False,
        "auto_weight_tuning": False,
    }


def replay_stock_db_accuracy_lab(
    stock_db_path: str,
    *,
    codes: Optional[Sequence[str]] = None,
    min_samples: int = 50,
    promotion_min_samples: int = 100,
) -> Dict[str, Any]:
    requested = list(codes or [])
    source_codes = list(dict.fromkeys(requested + ["SPY", "QQQ"])) if requested else None
    series = load_sqlite_series(stock_db_path, source_codes)
    observations = replay_accuracy_lab(series, codes=requested or None)
    return summarize_accuracy_replay(
        observations,
        min_samples=min_samples,
        promotion_min_samples=promotion_min_samples,
    )