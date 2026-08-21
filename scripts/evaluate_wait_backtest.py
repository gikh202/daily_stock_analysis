from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any, Mapping


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def enrich_wait_backtest(
    payload: Mapping[str, Any],
    *,
    transaction_cost_hurdle_pct: float = 0.10,
) -> dict[str, Any]:
    """Add realized-entry and promotion metrics to a WAIT A/B payload.

    The cost hurdle is a fixed research assumption, not a broker-specific fee quote.
    It represents the minimum entry-price improvement that must be large enough to
    survive modest fees/slippage before a WAIT policy can be considered for
    promotion.
    """
    result = dict(payload)
    rows = [dict(row) for row in (payload.get("rows") or []) if isinstance(row, Mapping)]
    realized_improvements: list[float] = []
    for row in rows:
        if not bool(row.get("better_entry_hit")):
            continue
        signal_price = _finite(row.get("signal_price"))
        entry_price = _finite(row.get("expected_better_price"))
        if signal_price is None or entry_price is None or signal_price <= 0:
            continue
        improvement = (signal_price - entry_price) / signal_price * 100.0
        if improvement >= 0:
            realized_improvements.append(improvement)

    avg_realized = mean(realized_improvements) if realized_improvements else None
    wait_count = int(result.get("wait_sample_count") or len(rows))
    hit_rate = _finite(result.get("better_entry_hit_rate"))
    timing_alpha = _finite((result.get("entry_timing_alpha") or {}).get("avg_vs_immediate_pct"))
    hurdle = max(0.0, float(transaction_cost_hurdle_pct))

    sample_count_ok = wait_count >= 20
    hit_rate_ok = hit_rate is not None and hit_rate > 0.50
    improvement_ok = avg_realized is not None and avg_realized > hurdle
    timing_alpha_ok = timing_alpha is not None and timing_alpha > 0.0

    result["avg_realized_entry_improvement_pct"] = avg_realized
    result["realized_entry_improvement_samples"] = len(realized_improvements)
    result["research_transaction_cost_hurdle_pct"] = hurdle
    result["promotion_check"] = {
        "minimum_samples": 20,
        "sample_count_ok": sample_count_ok,
        "hit_rate_gt_50pct": hit_rate_ok,
        "realized_improvement_gt_cost_hurdle": improvement_ok,
        "positive_timing_alpha": timing_alpha_ok,
        "eligible": bool(sample_count_ok and hit_rate_ok and improvement_ok and timing_alpha_ok),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Enrich WAIT A/B research metrics")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--transaction-cost-hurdle-pct", type=float, default=0.10)
    args = parser.parse_args()
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    enriched = enrich_wait_backtest(
        payload,
        transaction_cost_hurdle_pct=args.transaction_cost_hurdle_pct,
    )
    Path(args.output).write_text(
        json.dumps(enriched, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(enriched, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
