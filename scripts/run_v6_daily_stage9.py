from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.run_v6_daily as base_runner
from src.v6_daily.engine import V6DailyEngine
from src.v6_daily.legacy_retirement import assert_legacy_retirement_ready
from src.v6_daily.normalized_accuracy_lab import run_normalized_accuracy_lab
from src.v6_daily.normalized_cutover import cutover_daily_payload as normalized_cutover_daily_payload
from src.v6_daily.normalized_manifest_store import NormalizedV6ManifestStore
from src.v6_daily.normalized_read_store import NormalizedV6ReadStore


STAGE9_ENTRYPOINT_VERSION = "v6-stage9-normalized-consumers-v1"
_ORIGINAL_RUN = base_runner.run


def _normalized_accuracy_lab_adapter(
    v6_db_path: str,
    stock_db_path: str,
    *,
    report_dir: str,
    min_samples: int,
    promotion_min_samples: int,
    cost_bps: float,
    max_holding_bars: int,
) -> Dict[str, Any]:
    return run_normalized_accuracy_lab(
        v6_db_path,
        stock_db_path,
        report_dir=report_dir,
        active_engine_version=V6DailyEngine().version,
        min_samples=min_samples,
        promotion_min_samples=promotion_min_samples,
        cost_bps=cost_bps,
        max_holding_bars=max_holding_bars,
    )


def _attach_retirement_metadata(
    result: Dict[str, Any],
    *,
    v6_db_path: str,
    report_dir: str,
) -> Dict[str, Any]:
    retirement = assert_legacy_retirement_ready(v6_db_path)
    retirement["entrypoint_version"] = STAGE9_ENTRYPOINT_VERSION
    result["legacy_retirement"] = retirement

    output = Path(report_dir)
    run_path = output / "v6_run.json"
    payload_path = output / "v6_daily_latest.json"
    for path in (run_path, payload_path):
        if not path.is_file():
            raise RuntimeError(f"Stage 9 expected output missing: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["legacy_retirement"] = retirement
        if path == run_path:
            data["stage9_entrypoint"] = STAGE9_ENTRYPOINT_VERSION
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return result


def run(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Execute the existing runner with all active read consumers normalized.

    Stage 8's canonical writer intentionally continues projecting legacy rows
    during this observation window. Everything that *reads* daily/research facts
    is replaced here with normalized implementations so Stage 10 can disable the
    projection without changing business algorithms.
    """
    # Global names are resolved by the original function at runtime, so these
    # substitutions keep the battle-tested orchestration while cutting its active
    # read dependencies off the legacy tables.
    base_runner.run_accuracy_lab = _normalized_accuracy_lab_adapter
    base_runner.VersionedV6DailyStore = NormalizedV6ReadStore
    base_runner.NormalizedV6Persistence = NormalizedV6ManifestStore
    base_runner.cutover_daily_payload = normalized_cutover_daily_payload

    result = _ORIGINAL_RUN(*args, **kwargs)
    v6_db_path = str(kwargs.get("v6_db_path") or "v6_data/v6_daily.db")
    report_dir = str(kwargs.get("report_dir") or "v6_reports")
    return _attach_retirement_metadata(
        result,
        v6_db_path=v6_db_path,
        report_dir=report_dir,
    )


# base_runner.main resolves its module-global ``run`` when invoked. Replace it
# only inside this explicit Stage 9 entrypoint; importing scripts/run_v6_daily.py
# elsewhere remains the Stage 8 compatibility path for rollback/testing.
base_runner.run = run


if __name__ == "__main__":
    raise SystemExit(base_runner.main())
