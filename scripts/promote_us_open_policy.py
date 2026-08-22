from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.forecasting.timing_policy import TUNABLE_FIELDS, TimingPolicy, load_timing_policy, write_timing_policy

DEFAULT_ACTIVE = Path("config/us_open_timing_policy.json")
DEFAULT_REGISTRY = Path("config/us_open_policy_registry.json")


def _load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"expected JSON object: {path}")
    return dict(value)


def _assert_only_tunables_changed(active: TimingPolicy, challenger: TimingPolicy) -> None:
    if challenger.version == active.version:
        raise ValueError("challenger version must differ from active version")
    if challenger.score_model_version != active.score_model_version:
        raise ValueError("score model version cannot change through timing calibration")
    allowed = set(TUNABLE_FIELDS) | {"version"}
    changed = {
        key
        for key, value in active.to_dict().items()
        if challenger.to_dict().get(key) != value
    }
    forbidden = sorted(changed - allowed)
    if forbidden:
        raise ValueError(f"challenger changes non-tunable fields: {forbidden}")


def build_promotion(
    active: TimingPolicy,
    challenger: TimingPolicy,
    report: Mapping[str, Any],
    registry: Mapping[str, Any],
) -> tuple[TimingPolicy, dict[str, Any]]:
    _assert_only_tunables_changed(active, challenger)
    promotion = report.get("promotion") if isinstance(report.get("promotion"), Mapping) else {}
    if not bool(promotion.get("eligible")):
        raise ValueError("calibration report is not eligible for production promotion")
    if str(report.get("active_policy_version") or "") != active.version:
        raise ValueError("calibration active policy version does not match repository active policy")
    if str(report.get("challenger_policy_version") or "") != challenger.version:
        raise ValueError("calibration challenger version does not match challenger file")
    if str(report.get("score_model_version") or "") != active.score_model_version:
        raise ValueError("calibration score model version mismatch")

    updated = dict(registry)
    policies = dict(updated.get("policies") or {})
    old_meta = dict(policies.get(active.version) or {})
    old_meta["status"] = "PREVIOUS"
    policies[active.version] = old_meta
    policies[challenger.version] = {
        "status": "ACTIVE",
        "source": "v7.3-calibration-promotion",
        "calibration_report_version": str(report.get("version") or "unknown"),
    }
    updated.update(
        active=challenger.version,
        previous=active.version,
        challenger=None,
        promotion_mode="pull_request",
        score_model_version=challenger.score_model_version,
        policies=policies,
    )
    return challenger.validate(), updated


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or apply a reviewed V7.3 timing policy promotion")
    parser.add_argument("--active", default=str(DEFAULT_ACTIVE))
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--challenger", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--policy-output", default="open_confirmation_reports/proposed_us_open_timing_policy.json")
    parser.add_argument("--registry-output", default="open_confirmation_reports/proposed_us_open_policy_registry.json")
    parser.add_argument("--apply", action="store_true", help="write repository active policy/registry; intended only inside a reviewed promotion PR")
    args = parser.parse_args()

    active = load_timing_policy(args.active)
    challenger = load_timing_policy(args.challenger)
    report = _load_json(args.report)
    registry = _load_json(args.registry)
    promoted, updated_registry = build_promotion(active, challenger, report, registry)

    policy_output = Path(args.active) if args.apply else Path(args.policy_output)
    registry_output = Path(args.registry) if args.apply else Path(args.registry_output)
    write_timing_policy(promoted, policy_output)
    registry_output.parent.mkdir(parents=True, exist_ok=True)
    registry_output.write_text(
        json.dumps(updated_registry, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"active": promoted.version, "previous": active.version, "applied": bool(args.apply)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
