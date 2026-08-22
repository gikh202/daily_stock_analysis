from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Mapping

DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "us_open_timing_policy.json"
TUNABLE_FIELDS = ("wait_threshold", "min_expected_improvement_pct")


@dataclass(frozen=True)
class TimingPolicy:
    version: str = "v7.3-policy-001"
    score_model_version: str = "heuristic_score_v1"
    wait_threshold: float = 0.62
    min_expected_improvement_pct: float = 0.20
    base_score: float = 0.42
    range_position_weight: float = 0.24
    vwap_premium_divisor: float = 2.0
    vwap_adjust_min: float = -0.12
    vwap_adjust_max: float = 0.16
    momentum_divisor: float = 2.0
    momentum_adjust_min: float = -0.12
    momentum_adjust_max: float = 0.16
    p1_anchor: float = 0.58
    p1_weight: float = 0.10
    p5_anchor: float = 0.56
    p5_weight: float = 0.06
    above_entry_base_bonus: float = 0.08
    above_entry_bonus_divisor: float = 4.0
    above_entry_bonus_max: float = 0.22
    near_entry_discount: float = 0.08
    near_entry_ratio: float = 1.002
    time_anchor_minutes: float = 60.0
    time_adjust_divisor: float = 600.0
    time_adjust_min: float = -0.05
    time_adjust_max: float = 0.10
    score_min: float = 0.05
    score_max: float = 0.95
    improvement_vol_base: float = 0.22
    improvement_vol_score_weight: float = 0.38
    improvement_min_pct: float = 0.05
    improvement_max_pct: float = 2.50
    above_entry_improvement_weight: float = 0.45
    above_entry_improvement_max_pct: float = 1.00
    stop_buffer_ratio: float = 1.002
    falling_hard_momentum_pct: float = -0.45
    continuation_momentum_pct: float = 0.30
    continuation_range_position: float = 0.65
    continuation_p1: float = 0.58
    continuation_vwap_premium_min_pct: float = -0.10
    pullback_score_floor: float = 0.60
    early_recheck_minutes: int = 15
    late_recheck_minutes: int = 30
    early_recheck_cutoff_minutes: int = 60
    confirmation_recheck_cutoff_minutes: int = 90
    default_recheck_minutes: int = 20

    def validate(self) -> "TimingPolicy":
        if not self.version.strip():
            raise ValueError("timing policy version is required")
        if self.score_model_version != "heuristic_score_v1":
            raise ValueError("unsupported score_model_version")
        if not 0.50 <= self.wait_threshold <= 0.80:
            raise ValueError("wait_threshold must stay within [0.50, 0.80]")
        if not 0.05 <= self.min_expected_improvement_pct <= 0.60:
            raise ValueError("min_expected_improvement_pct must stay within [0.05, 0.60]")
        if not 0.0 < self.score_min < self.score_max < 1.0:
            raise ValueError("score bounds must satisfy 0 < min < max < 1")
        if not 0.0 < self.improvement_min_pct < self.improvement_max_pct <= 5.0:
            raise ValueError("invalid improvement bounds")
        if self.early_recheck_minutes <= 0 or self.late_recheck_minutes <= 0:
            raise ValueError("recheck intervals must be positive")
        if self.stop_buffer_ratio < 1.0:
            raise ValueError("stop_buffer_ratio cannot weaken the hard stop")
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def with_tunables(
        self,
        *,
        wait_threshold: float,
        min_expected_improvement_pct: float,
        version: str | None = None,
    ) -> "TimingPolicy":
        return replace(
            self,
            wait_threshold=float(wait_threshold),
            min_expected_improvement_pct=float(min_expected_improvement_pct),
            version=version or self.version,
        ).validate()


def _coerce_policy(payload: Mapping[str, Any]) -> TimingPolicy:
    defaults = TimingPolicy().to_dict()
    values = {key: payload.get(key, default) for key, default in defaults.items()}
    return TimingPolicy(**values).validate()


def load_timing_policy(path: str | Path | None = None) -> TimingPolicy:
    policy_path = Path(path) if path is not None else DEFAULT_POLICY_PATH
    if not policy_path.is_file():
        return TimingPolicy().validate()
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"timing policy must be a JSON object: {policy_path}")
    return _coerce_policy(payload)


def write_timing_policy(policy: TimingPolicy, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(policy.validate().to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target
