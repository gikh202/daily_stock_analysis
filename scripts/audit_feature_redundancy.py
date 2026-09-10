from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path
from statistics import mean
from typing import Any, Mapping, Sequence


POSITIVE_FEATURES = (
    "trend",
    "momentum",
    "relative_strength",
    "sector_relative_strength",
    "volume_confirmation",
    "fundamental_quality",
    "catalyst",
    "market_regime",
    "data_quality",
)
RISK_FEATURES = (
    "volatility_risk",
    "event_risk",
    "gap_risk",
    "trend_breakdown_risk",
    "macro_risk",
)
FEATURES = POSITIVE_FEATURES + RISK_FEATURES


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _object(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    mx, my = mean(xs), mean(ys)
    dx = [value - mx for value in xs]
    dy = [value - my for value in ys]
    sx = sum(value * value for value in dx)
    sy = sum(value * value for value in dy)
    if sx <= 1e-12 or sy <= 1e-12:
        return None
    return sum(a * b for a, b in zip(dx, dy)) / math.sqrt(sx * sy)


def _oriented(name: str, value: float) -> float:
    centered = (max(0.0, min(100.0, value)) - 50.0) / 50.0
    return -centered if name in RISK_FEATURES else centered


def _composite(features: Mapping[str, float], *, omit: str | None = None) -> float | None:
    values = [
        _oriented(name, value)
        for name, value in features.items()
        if name in FEATURES and name != omit
    ]
    return mean(values) if values else None


def run(
    db_path: str | Path,
    *,
    horizon_days: int = 5,
    min_pair_samples: int = 30,
    redundancy_threshold: float = 0.85,
) -> dict[str, Any]:
    conn = sqlite3.connect(str(db_path), timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT f.id,f.symbol,f.effective_trade_date,f.features_json,
                   o.return_pct,o.directional_hit
            FROM v6_forecast_runs f
            JOIN v6_forecast_outcomes o ON o.forecast_run_id=f.id
            WHERE o.horizon_days=?
            ORDER BY f.effective_trade_date,f.symbol,f.id
            """,
            (int(horizon_days),),
        ).fetchall()
    finally:
        conn.close()

    observations: list[dict[str, Any]] = []
    for row in rows:
        raw = _object(row["features_json"])
        features = {
            name: value
            for name in FEATURES
            if (value := _finite(raw.get(name))) is not None
        }
        realized = _finite(row["return_pct"])
        if realized is None or not features:
            continue
        observations.append(
            {
                "symbol": str(row["symbol"] or ""),
                "date": str(row["effective_trade_date"] or ""),
                "features": features,
                "return_pct": realized,
            }
        )

    individual: dict[str, dict[str, Any]] = {}
    for name in FEATURES:
        xs: list[float] = []
        ys: list[float] = []
        for item in observations:
            value = item["features"].get(name)
            if value is None:
                continue
            xs.append(_oriented(name, value))
            ys.append(float(item["return_pct"]))
        individual[name] = {
            "samples": len(xs),
            "return_correlation": _pearson(xs, ys),
        }

    pairs: list[dict[str, Any]] = []
    redundant_pairs: list[dict[str, Any]] = []
    for index, left in enumerate(FEATURES):
        for right in FEATURES[index + 1 :]:
            xs: list[float] = []
            ys: list[float] = []
            for item in observations:
                l = item["features"].get(left)
                r = item["features"].get(right)
                if l is None or r is None:
                    continue
                xs.append(_oriented(left, l))
                ys.append(_oriented(right, r))
            corr = _pearson(xs, ys)
            block = {
                "left": left,
                "right": right,
                "samples": len(xs),
                "correlation": corr,
            }
            pairs.append(block)
            if (
                corr is not None
                and len(xs) >= min_pair_samples
                and abs(corr) >= redundancy_threshold
            ):
                left_edge = abs(individual[left]["return_correlation"] or 0.0)
                right_edge = abs(individual[right]["return_correlation"] or 0.0)
                block = dict(block)
                block["weaker_feature"] = left if left_edge <= right_edge else right
                redundant_pairs.append(block)

    eligible_rows = [
        item for item in observations if len(item["features"]) >= 3
    ]
    baseline_hits: list[int] = []
    for item in eligible_rows:
        score = _composite(item["features"])
        if score is None:
            continue
        baseline_hits.append(int((score >= 0) == (item["return_pct"] >= 0)))
    baseline_accuracy = mean(baseline_hits) if baseline_hits else None

    leave_one_out: dict[str, dict[str, Any]] = {}
    for name in FEATURES:
        paired_hits_with: list[int] = []
        paired_hits_without: list[int] = []
        paired_returns_with: list[float] = []
        paired_returns: list[float] = []
        for item in eligible_rows:
            if name not in item["features"]:
                continue
            score_with = _composite(item["features"])
            score_without = _composite(item["features"], omit=name)
            if score_with is None or score_without is None:
                continue
            realized_positive = item["return_pct"] >= 0
            paired_hits_with.append(int((score_with >= 0) == realized_positive))
            paired_hits_without.append(int((score_without >= 0) == realized_positive))
            paired_returns_with.append(score_without)
            paired_returns.append(float(item["return_pct"]))
        acc_with = mean(paired_hits_with) if paired_hits_with else None
        acc_without = mean(paired_hits_without) if paired_hits_without else None
        delta_pp = (
            None
            if acc_with is None or acc_without is None
            else (acc_with - acc_without) * 100.0
        )
        leave_one_out[name] = {
            "samples": len(paired_hits_with),
            "accuracy_with_feature": acc_with,
            "accuracy_without_feature": acc_without,
            "accuracy_contribution_pp": delta_pp,
            "without_feature_return_correlation": _pearson(
                paired_returns_with, paired_returns
            ),
        }

    drop_candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pair in redundant_pairs:
        weaker = str(pair["weaker_feature"])
        loo = leave_one_out.get(weaker) or {}
        contribution = _finite(loo.get("accuracy_contribution_pp"))
        if weaker in seen or contribution is None or contribution > 0.0:
            continue
        seen.add(weaker)
        drop_candidates.append(
            {
                "feature": weaker,
                "reason": "high_pairwise_redundancy_and_nonpositive_leave_one_out_contribution",
                "accuracy_contribution_pp": contribution,
            }
        )

    return {
        "version": "feature-redundancy-audit-v1",
        "horizon_days": int(horizon_days),
        "samples": len(observations),
        "baseline_simple_composite_accuracy": baseline_accuracy,
        "individual": individual,
        "pairwise": pairs,
        "redundant_pairs": redundant_pairs,
        "leave_one_out": leave_one_out,
        "drop_candidates": drop_candidates,
        "policy": (
            "research audit only; it never changes production weights automatically. "
            "Any removal still requires walk-forward validation and reviewed promotion."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit V6 deterministic feature redundancy and leave-one-out contribution"
    )
    parser.add_argument("--db", required=True)
    parser.add_argument("--output", default="v6_reports/feature_redundancy_audit.json")
    parser.add_argument("--horizon-days", type=int, default=5)
    parser.add_argument("--min-pair-samples", type=int, default=30)
    parser.add_argument("--redundancy-threshold", type=float, default=0.85)
    args = parser.parse_args()
    payload = run(
        args.db,
        horizon_days=args.horizon_days,
        min_pair_samples=max(3, args.min_pair_samples),
        redundancy_threshold=max(0.0, min(1.0, args.redundancy_threshold)),
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
