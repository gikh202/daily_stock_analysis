from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from statistics import mean, median
from typing import Any


def _avg(values: list[float]) -> float | None:
    return mean(values) if values else None


def run(db_path: str | Path, *, cost_hurdle_pct: float = 0.10) -> dict[str, Any]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(us_open_signals)")}
        required = {
            "market_regime",
            "close_return_pct",
            "ideal_entry_hit",
            "ideal_entry_policy_return_pct",
            "ideal_entry_alpha_vs_immediate_pct",
            "minutes_to_ideal_entry",
        }
        missing = sorted(required - columns)
        if missing:
            raise RuntimeError(f"entry-optimizer ledger columns missing: {missing}")
        rows = conn.execute(
            """
            SELECT session_date, symbol, market_regime, close_return_pct,
                   ideal_entry_hit, ideal_entry_policy_return_pct,
                   ideal_entry_alpha_vs_immediate_pct, minutes_to_ideal_entry
            FROM us_open_signals
            WHERE settled_at IS NOT NULL
              AND ideal_entry_hit IS NOT NULL
            ORDER BY session_date, symbol, id
            """
        ).fetchall()
    finally:
        conn.close()

    def metrics(items: list[sqlite3.Row]) -> dict[str, Any]:
        hits = [int(row["ideal_entry_hit"]) for row in items]
        immediate = [
            float(row["close_return_pct"])
            for row in items
            if row["close_return_pct"] is not None
        ]
        policy = [
            float(row["ideal_entry_policy_return_pct"])
            for row in items
            if row["ideal_entry_policy_return_pct"] is not None
        ]
        alpha = [
            float(row["ideal_entry_alpha_vs_immediate_pct"])
            for row in items
            if row["ideal_entry_alpha_vs_immediate_pct"] is not None
        ]
        minutes = [
            float(row["minutes_to_ideal_entry"])
            for row in items
            if row["minutes_to_ideal_entry"] is not None
        ]
        net_alpha = [value - cost_hurdle_pct for value in alpha]
        return {
            "samples": len(items),
            "hit_rate": (sum(hits) / len(hits)) if hits else None,
            "immediate_avg_return_pct": _avg(immediate),
            "optimized_avg_return_pct": _avg(policy),
            "avg_alpha_vs_immediate_pct": _avg(alpha),
            "median_alpha_vs_immediate_pct": median(alpha) if alpha else None,
            "avg_net_alpha_after_cost_hurdle_pct": _avg(net_alpha),
            "avg_minutes_to_ideal_entry": _avg(minutes),
        }

    all_rows = list(rows)
    regimes = sorted({str(row["market_regime"] or "unknown") for row in all_rows})
    by_regime = {
        regime: metrics(
            [row for row in all_rows if str(row["market_regime"] or "unknown") == regime]
        )
        for regime in regimes
    }
    overall = metrics(all_rows)
    eligible = bool(
        overall["samples"] >= 20
        and (overall["hit_rate"] or 0.0) > 0.50
        and (overall["avg_net_alpha_after_cost_hurdle_pct"] or 0.0) > 0.0
    )
    return {
        "version": "entry-optimizer-backtest-v1",
        "definition": (
            "enter at ideal_entry_price when touched inside the promised wait window; "
            "otherwise remain in cash; compare with immediate signal-price entry"
        ),
        "transaction_cost_hurdle_pct": cost_hurdle_pct,
        "overall": overall,
        "by_regime": by_regime,
        "promotion_check": {
            "minimum_samples": 20,
            "hit_rate_gt_50pct": bool((overall["hit_rate"] or 0.0) > 0.50),
            "positive_net_alpha": bool(
                (overall["avg_net_alpha_after_cost_hurdle_pct"] or 0.0) > 0.0
            ),
            "eligible": eligible,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest optimized entry timing from Research Ledger")
    parser.add_argument("--db", required=True)
    parser.add_argument("--output", default="open_confirmation_reports/entry_optimizer_backtest.json")
    parser.add_argument("--cost-hurdle-pct", type=float, default=0.10)
    args = parser.parse_args()
    payload = run(args.db, cost_hurdle_pct=args.cost_hurdle_pct)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
