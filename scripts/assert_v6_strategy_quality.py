#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

MIN_ALPHA_SAMPLES = 100
MIN_DIRECTIONAL_HIT_PCT = 50.0
MIN_ALPHA_HIT_PCT = 50.0
MAX_HIT_REGRESSION_PP = 1.0
MAX_ALPHA_RETURN_REGRESSION_PCT = 0.05
MAX_UNDERLYING_EXCESS_REGRESSION_PCT = 0.05


def _load(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _champions(payload: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    out: dict[int, Mapping[str, Any]] = {}
    for item in payload.get("results") or []:
        if not isinstance(item, Mapping) or item.get("variant") != "champion":
            continue
        horizon = int(item.get("horizon_days") or 0)
        if horizon > 0:
            out[horizon] = item
    return out


def _require_metric(metric: Mapping[str, Any], key: str, *, label: str) -> float:
    value = _num(metric.get(key))
    if value is None:
        raise AssertionError(f"{label}: missing metric {key}")
    return value


def _validate_head(payload: Mapping[str, Any]) -> list[str]:
    if int(payload.get("observations") or 0) <= 0:
        raise AssertionError("strategy backtest produced zero observations")

    champions = _champions(payload)
    if not champions:
        raise AssertionError("strategy backtest produced no champion horizons")

    summaries: list[str] = []
    mature_horizons = 0
    for horizon, item in sorted(champions.items()):
        independent = item.get("non_overlapping") or {}
        alpha = (item.get("alpha_target") or {}).get("non_overlapping") or {}
        samples = int(independent.get("samples") or 0)
        alpha_samples = int(alpha.get("samples") or 0)
        hit = _require_metric(independent, "directional_hit_rate_pct", label=f"{horizon}D")
        underlying_excess = _require_metric(
            independent,
            "avg_underlying_excess_vs_spy_pct",
            label=f"{horizon}D",
        )
        alpha_hit = _require_metric(alpha, "alpha_hit_rate_pct", label=f"{horizon}D alpha")
        alpha_return = _require_metric(
            alpha,
            "avg_alpha_trade_return_pct",
            label=f"{horizon}D alpha",
        )

        # The long/short strategy-vs-long-only-SPY number is intentionally not
        # used as an absolute gate: it mixes different beta exposures. We gate
        # the two like-for-like research questions instead:
        #   1) did the underlying selections beat SPY on average?
        #   2) did the signed alpha target produce positive relative return?
        if alpha_samples >= MIN_ALPHA_SAMPLES:
            mature_horizons += 1
            if hit < MIN_DIRECTIONAL_HIT_PCT:
                raise AssertionError(f"{horizon}D directional hit below 50%: {hit:.2f}%")
            if underlying_excess <= 0:
                raise AssertionError(
                    f"{horizon}D underlying selections do not beat SPY: {underlying_excess:.4f}%"
                )
            if alpha_hit < MIN_ALPHA_HIT_PCT:
                raise AssertionError(f"{horizon}D alpha hit below 50%: {alpha_hit:.2f}%")
            if alpha_return <= 0:
                raise AssertionError(
                    f"{horizon}D signed alpha return is not positive: {alpha_return:.4f}%"
                )

        summaries.append(
            f"{horizon}D N={samples} alphaN={alpha_samples} hit={hit:.2f}% "
            f"underlying_vs_spy={underlying_excess:.4f}% alpha_hit={alpha_hit:.2f}% "
            f"alpha_return={alpha_return:.4f}%"
        )

    if mature_horizons <= 0:
        raise AssertionError(
            f"no champion horizon has at least {MIN_ALPHA_SAMPLES} independent alpha samples"
        )

    promotion_min_samples = int(payload.get("promotion_min_samples") or 0)
    for item in payload.get("results") or []:
        if not isinstance(item, Mapping) or not item.get("promotion_candidate"):
            continue
        independent = item.get("non_overlapping") or {}
        alpha = (item.get("alpha_target") or {}).get("non_overlapping") or {}
        samples = int(independent.get("samples") or 0)
        alpha_samples = int(alpha.get("samples") or 0)
        alpha_ci_low = _num(alpha.get("alpha_hit_ci95_low_pct"))
        alpha_return = _num(alpha.get("avg_alpha_trade_return_pct"))
        underlying_excess = _num(independent.get("avg_underlying_excess_vs_spy_pct"))
        delta = _num(item.get("hit_rate_delta_vs_champion_pp"))
        required = max(MIN_ALPHA_SAMPLES, promotion_min_samples)
        if samples < required or alpha_samples < required:
            raise AssertionError(
                f"promotion candidate {item.get('variant')} {item.get('horizon_days')}D lacks independent samples"
            )
        if alpha_ci_low is None or alpha_ci_low <= 50.0:
            raise AssertionError(
                f"promotion candidate {item.get('variant')} {item.get('horizon_days')}D lacks statistically positive alpha hit CI"
            )
        if alpha_return is None or alpha_return <= 0:
            raise AssertionError("promotion candidate has non-positive alpha return")
        if underlying_excess is None or underlying_excess <= 0:
            raise AssertionError("promotion candidate does not beat SPY on underlying selection")
        if delta is None or delta <= 0:
            raise AssertionError("promotion candidate does not improve directional hit rate")

    return summaries


def _validate_no_regression(head: Mapping[str, Any], base: Mapping[str, Any]) -> None:
    head_champions = _champions(head)
    base_champions = _champions(base)
    if set(head_champions) != set(base_champions):
        raise AssertionError(
            f"champion horizon set drift: head={sorted(head_champions)} base={sorted(base_champions)}"
        )

    for horizon in sorted(head_champions):
        h = head_champions[horizon]
        b = base_champions[horizon]
        hi = h.get("non_overlapping") or {}
        bi = b.get("non_overlapping") or {}
        ha = (h.get("alpha_target") or {}).get("non_overlapping") or {}
        ba = (b.get("alpha_target") or {}).get("non_overlapping") or {}

        head_hit = _require_metric(hi, "directional_hit_rate_pct", label=f"head {horizon}D")
        base_hit = _require_metric(bi, "directional_hit_rate_pct", label=f"base {horizon}D")
        if head_hit + MAX_HIT_REGRESSION_PP < base_hit:
            raise AssertionError(
                f"{horizon}D directional hit regression: head={head_hit:.2f}% base={base_hit:.2f}%"
            )

        head_alpha = _require_metric(ha, "avg_alpha_trade_return_pct", label=f"head {horizon}D alpha")
        base_alpha = _require_metric(ba, "avg_alpha_trade_return_pct", label=f"base {horizon}D alpha")
        if head_alpha + MAX_ALPHA_RETURN_REGRESSION_PCT < base_alpha:
            raise AssertionError(
                f"{horizon}D alpha-return regression: head={head_alpha:.4f}% base={base_alpha:.4f}%"
            )

        head_excess = _require_metric(
            hi,
            "avg_underlying_excess_vs_spy_pct",
            label=f"head {horizon}D",
        )
        base_excess = _require_metric(
            bi,
            "avg_underlying_excess_vs_spy_pct",
            label=f"base {horizon}D",
        )
        if head_excess + MAX_UNDERLYING_EXCESS_REGRESSION_PCT < base_excess:
            raise AssertionError(
                f"{horizon}D SPY-selection regression: head={head_excess:.4f}% base={base_excess:.4f}%"
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--head", required=True)
    parser.add_argument("--base", required=True)
    args = parser.parse_args()

    head = _load(args.head)
    base = _load(args.base)
    summaries = _validate_head(head)
    _validate_no_regression(head, base)
    print("V6 strategy quality gate: PASS")
    for line in summaries:
        print("  " + line)


if __name__ == "__main__":
    main()
