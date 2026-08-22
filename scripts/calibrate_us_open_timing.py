from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.forecasting.timing_policy import TimingPolicy, load_timing_policy, write_timing_policy

MIN_SETTLED_SAMPLES = 60
MIN_OOS_SAMPLES = 20
MIN_SESSION_DATES = 10
MIN_SYMBOLS = 3
MIN_CHALLENGER_WAITS = 10
MIN_AVG_TIMING_ALPHA_PCT = 0.10
MAX_MISSED_CONTINUATION_RATE = 0.25
BOOTSTRAP_SAMPLES = 1000


@dataclass(frozen=True)
class Observation:
    row_id: int
    session_date: str
    symbol: str
    signal_price: float
    close_return_pct: float
    mfe_pct: float
    better_entry_score: float
    expected_improvement_pct: float
    expected_better_price: float
    better_entry_hit: bool

    @property
    def close_price(self) -> float:
        return self.signal_price * (1.0 + self.close_return_pct / 100.0)

    @property
    def wait_return_pct(self) -> float:
        if not self.better_entry_hit:
            return 0.0
        return (self.close_price / self.expected_better_price - 1.0) * 100.0

    @property
    def wait_alpha_pct(self) -> float:
        return self.wait_return_pct - self.close_return_pct

    @property
    def missed_continuation(self) -> bool:
        return (not self.better_entry_hit) and self.mfe_pct >= 0.50


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _decision_fields(text: Any) -> tuple[float | None, float | None, float | None]:
    try:
        payload = json.loads(str(text or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, None, None
    if not isinstance(payload, Mapping):
        return None, None, None
    score = _finite(payload.get("better_entry_score"))
    if score is None:
        score = _finite(payload.get("better_entry_probability"))
    return score, _finite(payload.get("expected_improvement_pct")), _finite(payload.get("expected_better_price"))


def load_observations(db_path: str | Path) -> list[Observation]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(us_open_signals)")}
        required = {
            "id", "session_date", "symbol", "signal_price", "decision_status",
            "decision_json", "settled_at", "close_return_pct", "mfe_pct", "better_entry_hit",
        }
        missing = sorted(required - columns)
        if missing:
            raise RuntimeError(f"research ledger missing columns: {missing}")
        rows = conn.execute(
            """
            SELECT id, session_date, symbol, signal_price, decision_json,
                   close_return_pct, mfe_pct, better_entry_hit
            FROM us_open_signals
            WHERE settled_at IS NOT NULL
              AND decision_status IN ('BUY_NOW','WAIT_BETTER_ENTRY')
            ORDER BY session_date, symbol, id
            """
        ).fetchall()
    finally:
        conn.close()

    result: list[Observation] = []
    for row in rows:
        price = _finite(row["signal_price"])
        close_ret = _finite(row["close_return_pct"])
        mfe = _finite(row["mfe_pct"])
        score, improvement, better_price = _decision_fields(row["decision_json"])
        hit = row["better_entry_hit"]
        if None in {price, close_ret, mfe, score, improvement, better_price} or hit is None:
            continue
        assert price is not None and better_price is not None
        if price <= 0 or not (0 < better_price < price):
            continue
        result.append(
            Observation(
                row_id=int(row["id"]),
                session_date=str(row["session_date"]),
                symbol=str(row["symbol"]).upper(),
                signal_price=price,
                close_return_pct=float(close_ret),
                mfe_pct=float(mfe),
                better_entry_score=float(score),
                expected_improvement_pct=float(improvement),
                expected_better_price=better_price,
                better_entry_hit=bool(hit),
            )
        )
    return result


def _drawdown(rows: Sequence[tuple[str, float]]) -> float | None:
    if not rows:
        return None
    grouped: dict[str, list[float]] = {}
    for day, value in rows:
        grouped.setdefault(day, []).append(value)
    equity = peak = 1.0
    worst = 0.0
    for day in sorted(grouped):
        equity *= max(0.0, 1.0 + mean(grouped[day]) / 100.0)
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
    return worst * 100.0


def evaluate_policy(rows: Sequence[Observation], policy: TimingPolicy) -> dict[str, Any]:
    returns: list[tuple[str, float]] = []
    immediate: list[tuple[str, float]] = []
    wait_alphas: list[float] = []
    waits: list[Observation] = []
    wait_hits = 0
    missed = 0
    for row in rows:
        choose_wait = (
            row.better_entry_score >= policy.wait_threshold
            and row.expected_improvement_pct >= policy.min_expected_improvement_pct
        )
        immediate.append((row.session_date, row.close_return_pct))
        if choose_wait:
            waits.append(row)
            value = row.wait_return_pct
            wait_alphas.append(value - row.close_return_pct)
            wait_hits += int(row.better_entry_hit)
            missed += int(row.missed_continuation)
        else:
            value = row.close_return_pct
        returns.append((row.session_date, value))
    values = [v for _, v in returns]
    immediate_values = [v for _, v in immediate]
    return {
        "policy_version": policy.version,
        "sample_count": len(rows),
        "session_dates": len({row.session_date for row in rows}),
        "symbols": len({row.symbol for row in rows}),
        "wait_count": len(waits),
        "wait_hit_rate": wait_hits / len(waits) if waits else None,
        "avg_return_pct": mean(values) if values else None,
        "immediate_avg_return_pct": mean(immediate_values) if immediate_values else None,
        "avg_return_delta_vs_immediate_pct": (
            mean(values) - mean(immediate_values) if values and immediate_values else None
        ),
        "avg_timing_alpha_pct": mean(wait_alphas) if wait_alphas else None,
        "median_timing_alpha_pct": median(wait_alphas) if wait_alphas else None,
        "missed_continuation_rate": missed / len(waits) if waits else None,
        "max_drawdown_pct": _drawdown(returns),
        "immediate_max_drawdown_pct": _drawdown(immediate),
        "wait_alphas": wait_alphas,
    }


def _candidate_grid(active: TimingPolicy) -> Iterable[TimingPolicy]:
    thresholds = sorted({
        round(max(0.50, min(0.80, active.wait_threshold + delta)), 4)
        for delta in (-0.08, -0.04, 0.0, 0.04, 0.08)
    })
    improvements = sorted({
        round(max(0.05, min(0.60, active.min_expected_improvement_pct + delta)), 4)
        for delta in (-0.05, 0.0, 0.05, 0.10)
    })
    for threshold in thresholds:
        for improvement in improvements:
            yield active.with_tunables(
                wait_threshold=threshold,
                min_expected_improvement_pct=improvement,
            )


def _objective(metrics: Mapping[str, Any]) -> float:
    if int(metrics.get("wait_count") or 0) < 3:
        return -1e9
    avg_return = float(metrics.get("avg_return_pct") or 0.0)
    alpha = float(metrics.get("avg_timing_alpha_pct") or 0.0)
    missed = float(metrics.get("missed_continuation_rate") or 0.0)
    return avg_return + 0.35 * alpha - 0.15 * missed


def _split(rows: Sequence[Observation]) -> tuple[list[Observation], list[Observation]]:
    dates = sorted({row.session_date for row in rows})
    if len(dates) < 2:
        return list(rows), []
    split_at = max(1, min(len(dates) - 1, int(len(dates) * 0.70)))
    train_dates = set(dates[:split_at])
    return (
        [row for row in rows if row.session_date in train_dates],
        [row for row in rows if row.session_date not in train_dates],
    )


def _bootstrap_lower(values: Sequence[float]) -> float | None:
    if len(values) < 10:
        return None
    rng = random.Random(7301)
    n = len(values)
    samples = [mean(values[rng.randrange(n)] for _ in range(n)) for _ in range(BOOTSTRAP_SAMPLES)]
    samples.sort()
    return samples[max(0, int(len(samples) * 0.025) - 1)]


def _walk_forward(rows: Sequence[Observation], champion: TimingPolicy, challenger: TimingPolicy) -> dict[str, Any]:
    dates = sorted({row.session_date for row in rows})
    if len(dates) < 6:
        return {"folds": 0, "challenger_wins": 0, "majority_won": False, "details": []}
    chunk = max(2, len(dates) // 4)
    details: list[dict[str, Any]] = []
    start = chunk
    while start < len(dates):
        test_dates = set(dates[start : min(len(dates), start + chunk)])
        test = [row for row in rows if row.session_date in test_dates]
        if not test:
            break
        c = evaluate_policy(test, champion)
        h = evaluate_policy(test, challenger)
        details.append({
            "start": min(test_dates), "end": max(test_dates), "samples": len(test),
            "champion_avg_return_pct": c["avg_return_pct"],
            "challenger_avg_return_pct": h["avg_return_pct"],
            "challenger_won": float(h["avg_return_pct"] or 0.0) > float(c["avg_return_pct"] or 0.0),
        })
        start += chunk
    wins = sum(bool(item["challenger_won"]) for item in details)
    return {
        "folds": len(details),
        "challenger_wins": wins,
        "majority_won": bool(details and wins > len(details) / 2),
        "details": details,
    }


def promotion_gate(
    rows: Sequence[Observation],
    champion: Mapping[str, Any],
    challenger: Mapping[str, Any],
    walk_forward: Mapping[str, Any],
) -> dict[str, Any]:
    wait_alphas = [float(value) for value in challenger.get("wait_alphas") or []]
    bootstrap_lower = _bootstrap_lower(wait_alphas)
    champion_dd = float(champion.get("max_drawdown_pct") or 0.0)
    challenger_dd = float(challenger.get("max_drawdown_pct") or 0.0)
    dd_ok = abs(challenger_dd) <= abs(champion_dd) * 1.10 + 0.10
    checks = {
        "settled_samples_ok": len(rows) >= MIN_SETTLED_SAMPLES,
        "oos_samples_ok": int(challenger.get("sample_count") or 0) >= MIN_OOS_SAMPLES,
        "session_dates_ok": int(challenger.get("session_dates") or 0) >= MIN_SESSION_DATES,
        "symbols_ok": int(challenger.get("symbols") or 0) >= MIN_SYMBOLS,
        "wait_samples_ok": int(challenger.get("wait_count") or 0) >= MIN_CHALLENGER_WAITS,
        "avg_timing_alpha_ok": float(challenger.get("avg_timing_alpha_pct") or -999) >= MIN_AVG_TIMING_ALPHA_PCT,
        "median_timing_alpha_ok": float(challenger.get("median_timing_alpha_pct") or -999) > 0.0,
        "bootstrap_95_lower_nonnegative": bootstrap_lower is not None and bootstrap_lower >= 0.0,
        "missed_continuation_ok": float(challenger.get("missed_continuation_rate") or 0.0) <= MAX_MISSED_CONTINUATION_RATE,
        "drawdown_ok": dd_ok,
        "oos_return_beats_champion": float(challenger.get("avg_return_pct") or -999) > float(champion.get("avg_return_pct") or -999),
        "walk_forward_majority_won": bool(walk_forward.get("majority_won")),
    }
    return {
        "eligible": all(checks.values()),
        "checks": checks,
        "bootstrap_95_lower_timing_alpha_pct": bootstrap_lower,
        "thresholds": {
            "minimum_settled_samples": MIN_SETTLED_SAMPLES,
            "minimum_oos_samples": MIN_OOS_SAMPLES,
            "minimum_session_dates": MIN_SESSION_DATES,
            "minimum_symbols": MIN_SYMBOLS,
            "minimum_challenger_waits": MIN_CHALLENGER_WAITS,
            "minimum_avg_timing_alpha_pct": MIN_AVG_TIMING_ALPHA_PCT,
            "maximum_missed_continuation_rate": MAX_MISSED_CONTINUATION_RATE,
        },
    }


def _public_metrics(metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in metrics.items() if key != "wait_alphas"}


def calibrate(
    db_path: str | Path,
    *,
    active_policy: TimingPolicy | None = None,
    generated_at: datetime | None = None,
) -> tuple[dict[str, Any], TimingPolicy | None]:
    active = active_policy or load_timing_policy()
    rows = load_observations(db_path)
    train, oos = _split(rows)
    now = generated_at or datetime.now(timezone.utc)
    report: dict[str, Any] = {
        "version": "us-open-policy-calibration-v1",
        "generated_at": now.isoformat(),
        "score_model_version": active.score_model_version,
        "active_policy_version": active.version,
        "observation_count": len(rows),
        "train_samples": len(train),
        "oos_samples": len(oos),
        "promotion_mode": "pull_request",
    }
    if len(rows) < 10 or len(train) < 6 or not oos:
        report.update(status="insufficient_samples", challenger_policy_version=None, promotion={"eligible": False, "reason": "insufficient_samples"})
        return report, None

    scored: list[tuple[float, TimingPolicy, dict[str, Any]]] = []
    for candidate in _candidate_grid(active):
        metrics = evaluate_policy(train, candidate)
        scored.append((_objective(metrics), candidate, metrics))
    scored.sort(key=lambda item: item[0], reverse=True)
    _, best, train_metrics = scored[0]
    challenger = best.with_tunables(
        wait_threshold=best.wait_threshold,
        min_expected_improvement_pct=best.min_expected_improvement_pct,
        version=now.strftime("v7.3-challenger-%Y%m%d%H%M"),
    )
    champion_oos = evaluate_policy(oos, active)
    challenger_oos = evaluate_policy(oos, challenger)
    walk = _walk_forward(rows, active, challenger)
    gate = promotion_gate(rows, champion_oos, challenger_oos, walk)
    report.update(
        status="challenger_evaluated",
        challenger_policy_version=challenger.version,
        active_policy={
            "version": active.version,
            "wait_threshold": active.wait_threshold,
            "min_expected_improvement_pct": active.min_expected_improvement_pct,
        },
        challenger_policy={
            "version": challenger.version,
            "wait_threshold": challenger.wait_threshold,
            "min_expected_improvement_pct": challenger.min_expected_improvement_pct,
        },
        train=_public_metrics(train_metrics),
        champion_oos=_public_metrics(champion_oos),
        challenger_oos=_public_metrics(challenger_oos),
        walk_forward=walk,
        promotion=gate,
    )
    return report, challenger


def main() -> int:
    parser = argparse.ArgumentParser(description="Calibrate a guarded V7.3 US-open timing Challenger")
    parser.add_argument("--db", required=True)
    parser.add_argument("--active-policy", default="config/us_open_timing_policy.json")
    parser.add_argument("--report-output", default="open_confirmation_reports/us_open_policy_calibration.json")
    parser.add_argument("--challenger-output", default="open_confirmation_reports/us_open_timing_policy_challenger.json")
    args = parser.parse_args()
    report, challenger = calibrate(args.db, active_policy=load_timing_policy(args.active_policy))
    report_path = Path(args.report_output)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    if challenger is not None:
        write_timing_policy(challenger, args.challenger_output)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
