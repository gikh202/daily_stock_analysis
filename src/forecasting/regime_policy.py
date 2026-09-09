from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .timing_policy import TimingPolicy, load_timing_policy

DEFAULT_REGIME_POLICY_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "us_open_regime_policy.json"
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def load_regime_timing_policy(
    regime: str | None,
    *,
    base_policy: TimingPolicy | None = None,
    regime_policy_path: str | Path | None = None,
) -> TimingPolicy:
    base = (base_policy or load_timing_policy()).validate()
    key = str(regime or "").strip().lower()
    if not key:
        return base

    path = Path(regime_policy_path) if regime_policy_path else DEFAULT_REGIME_POLICY_PATH
    if not path.is_file():
        return base
    payload = json.loads(path.read_text(encoding="utf-8"))
    override = _mapping(_mapping(payload).get("overrides")).get(key)
    override = _mapping(override)
    if not override or override.get("enabled") is False:
        return base

    wait = override.get("wait_threshold", base.wait_threshold)
    improvement = override.get(
        "min_expected_improvement_pct",
        base.min_expected_improvement_pct,
    )
    return base.with_tunables(
        wait_threshold=float(wait),
        min_expected_improvement_pct=float(improvement),
        version=f"{base.version}+{key}",
    )
