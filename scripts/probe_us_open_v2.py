from __future__ import annotations

import argparse
import json
from datetime import datetime, time
from pathlib import Path
from statistics import mean, median
from zoneinfo import ZoneInfo

from scripts.backtest_us_open_confirmation import build_observations, load_candidates
from scripts.run_us_open_confirmation import classify_confirmation
from scripts.run_us_open_confirmation_v2 import POLICY_VERSION, classify_confirmation_v2

NY = ZoneInfo("America/New_York")


def _metrics(rows, decisions):
    buys = [row for row, decision in zip(rows, decisions) if decision.status == "BUY_NOW"]
    close_returns = [row.close_return_pct for row in buys]
    hour_returns = [row.return_60m_pct for row in buys if row.return_60m_pct is not None]
    modeled = []
    status_counts = {}
    for decision in decisions:
        status_counts[decision.status] = status_counts.get(decision.status, 0) + 1
    for row in buys:
        execution = row.packet.get("execution") or {}
        stop = execution.get("stop_loss")
        targets = execution.get("targets") or []
        try:
            stop = float(stop) if stop is not None else None
        except (TypeError, ValueError):
            stop = None
        try:
            target1 = float(targets[0]) if targets else None
        except (TypeError, ValueError):
            target1 = None
        if row.stop_hit and row.target1_hit:
            continue
        if row.stop_hit and stop is not None:
            modeled.append((stop / row.snapshot.current_price - 1.0) * 100.0)
        elif row.target1_hit and target1 is not None:
            modeled.append((target1 / row.snapshot.current_price - 1.0) * 100.0)
        else:
            modeled.append(row.close_return_pct)
    return {
        "observations": len(rows),
        "buy_count": len(buys),
        "buy_rate": len(buys) / len(rows) if rows else 0.0,
        "status_counts": status_counts,
        "win_rate_close": (
            sum(value > 0 for value in close_returns) / len(close_returns)
            if close_returns else None
        ),
        "avg_close_return_pct": mean(close_returns) if close_returns else None,
        "median_close_return_pct": median(close_returns) if close_returns else None,
        "avg_60m_return_pct": mean(hour_returns) if hour_returns else None,
        "avg_mfe_pct": mean(row.mfe_pct for row in buys) if buys else None,
        "avg_mae_pct": mean(row.mae_pct for row in buys) if buys else None,
        "stop_hit_rate": sum(row.stop_hit for row in buys) / len(buys) if buys else None,
        "target1_hit_rate": sum(row.target1_hit for row in buys) / len(buys) if buys else None,
        "avg_modeled_plan_exit_return_pct": mean(modeled) if modeled else None,
        "buys": [
            {
                "symbol": row.symbol,
                "plan_date": row.plan_date,
                "session_date": row.session_date,
                "signal_price": row.snapshot.current_price,
                "status": decision.status,
                "return_from_open_pct": row.snapshot.return_from_open_pct,
                "volume_ratio": row.snapshot.volume_ratio,
                "opening_range_position": row.opening_range_position,
                "close_return_pct": row.close_return_pct,
                "return_60m_pct": row.return_60m_pct,
                "mfe_pct": row.mfe_pct,
                "mae_pct": row.mae_pct,
            }
            for row, decision in zip(rows, decisions)
            if decision.status == "BUY_NOW"
        ],
    }


def evaluate_baseline(rows):
    decisions = [
        classify_confirmation(
            row.packet,
            row.snapshot,
            chase_tolerance_pct=0.5,
            weak_open_pct=-0.75,
            min_volume_ratio=0.55,
        )
        for row in rows
    ]
    result = _metrics(rows, decisions)
    result["policy"] = "legacy-v1-baseline"
    result["implementation"] = "scripts.run_us_open_confirmation.classify_confirmation"
    return result


def evaluate_production_v2(rows):
    decisions = []
    for row in rows:
        session_date = datetime.fromisoformat(row.session_date).date()
        evaluated_at = datetime.combine(session_date, time(9, 45), tzinfo=NY)
        decisions.append(
            classify_confirmation_v2(
                row.packet,
                row.snapshot,
                evaluated_at=evaluated_at,
            )
        )
    result = _metrics(rows, decisions)
    result["policy"] = POLICY_VERSION
    result["implementation"] = "scripts.run_us_open_confirmation_v2.classify_confirmation_v2"
    result["evaluation_clock_et"] = "09:45"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the exact production US-open V2 policy")
    parser.add_argument("--v6-db", required=True)
    parser.add_argument("--output", default="open_confirmation_v2_probe.json")
    args = parser.parse_args()

    observations = build_observations(load_candidates(args.v6_db))
    by_source = {}
    for observation in observations:
        by_source.setdefault(observation.source, []).append(observation)

    payload = {
        "version": "us-open-production-policy-probe-v1",
        "policy_version": POLICY_VERSION,
        "generated_at": datetime.now(NY).isoformat(),
        "sources": {},
    }
    for source, rows in sorted(by_source.items()):
        payload["sources"][source] = {
            "baseline": evaluate_baseline(rows),
            "production_v2": evaluate_production_v2(rows),
        }

    Path(args.output).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
