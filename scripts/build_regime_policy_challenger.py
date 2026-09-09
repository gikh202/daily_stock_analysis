from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def _load(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"expected JSON object: {path}")
    return dict(value)


def build(
    current: Mapping[str, Any],
    calibration: Mapping[str, Any],
) -> tuple[dict[str, Any], int]:
    updated = dict(current)
    overrides = dict(updated.get("overrides") or {})
    changed = 0
    for regime, raw in (calibration.get("regime_calibration") or {}).items():
        if not isinstance(raw, Mapping) or not bool(raw.get("eligible")):
            continue
        proposed = raw.get("proposed") if isinstance(raw.get("proposed"), Mapping) else {}
        if "wait_threshold" not in proposed or "min_expected_improvement_pct" not in proposed:
            continue
        value = {
            "enabled": True,
            "wait_threshold": float(proposed["wait_threshold"]),
            "min_expected_improvement_pct": float(
                proposed["min_expected_improvement_pct"]
            ),
            "source": "weekly_regime_calibration",
            "samples": int(raw.get("samples") or 0),
            "oos_samples": int(raw.get("oos_samples") or 0),
        }
        if overrides.get(regime) != value:
            overrides[str(regime)] = value
            changed += 1

    updated["overrides"] = overrides
    if changed:
        updated["version"] = datetime.now(timezone.utc).strftime(
            "regime-policy-%Y%m%d%H%M"
        )
    return updated, changed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build regime-specific timing policy challenger"
    )
    parser.add_argument(
        "--current",
        default="config/us_open_regime_policy.json",
    )
    parser.add_argument("--report", required=True)
    parser.add_argument(
        "--output",
        default="open_confirmation_reports/proposed_us_open_regime_policy.json",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    current = _load(args.current)
    report = _load(args.report)
    updated, changed = build(current, report)
    output = Path(args.current) if args.apply else Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(updated, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"changed": changed, "version": updated.get("version")},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
