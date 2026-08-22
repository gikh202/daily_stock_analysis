from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.calibrate_us_open_timing import evaluate_policy, load_observations
from src.forecasting.timing_policy import load_timing_policy


def _load_registry(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    return dict(value) if isinstance(value, Mapping) else {}


def check_health(
    db_path: str | Path,
    *,
    policy_path: str | Path = "config/us_open_timing_policy.json",
    registry_path: str | Path = "config/us_open_policy_registry.json",
    recent_sessions: int = 20,
) -> dict[str, Any]:
    policy = load_timing_policy(policy_path)
    registry = _load_registry(registry_path)
    rows = load_observations(db_path)
    dates = sorted({row.session_date for row in rows})
    keep = set(dates[-max(1, recent_sessions):])
    recent = [row for row in rows if row.session_date in keep]
    metrics = evaluate_policy(recent, policy)
    waits = int(metrics.get("wait_count") or 0)
    triggers: list[str] = []
    if waits >= 10:
        if float(metrics.get("avg_timing_alpha_pct") or 0.0) < 0.0:
            triggers.append("recent_timing_alpha_negative")
        if float(metrics.get("missed_continuation_rate") or 0.0) > 0.35:
            triggers.append("missed_continuation_above_35pct")
        dd = abs(float(metrics.get("max_drawdown_pct") or 0.0))
        immediate_dd = abs(float(metrics.get("immediate_max_drawdown_pct") or 0.0))
        if dd > immediate_dd * 1.20 + 0.10:
            triggers.append("drawdown_degraded_over_20pct")
    previous = str(registry.get("previous") or "") or None
    return {
        "version": "us-open-policy-health-v1",
        "active_policy_version": policy.version,
        "recent_session_count": len(keep),
        "recent_wait_samples": waits,
        "status": "collecting" if waits < 10 else ("degraded" if triggers else "healthy"),
        "triggers": triggers,
        "previous_policy_version": previous,
        "rollback_recommended": bool(previous and triggers),
        "metrics": {key: value for key, value in metrics.items() if key != "wait_alphas"},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check live V7.3 timing policy health and rollback conditions")
    parser.add_argument("--db", required=True)
    parser.add_argument("--policy", default="config/us_open_timing_policy.json")
    parser.add_argument("--registry", default="config/us_open_policy_registry.json")
    parser.add_argument("--recent-sessions", type=int, default=20)
    parser.add_argument("--output", default="open_confirmation_reports/us_open_policy_health.json")
    args = parser.parse_args()
    payload = check_health(
        args.db,
        policy_path=args.policy,
        registry_path=args.registry,
        recent_sessions=args.recent_sessions,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
