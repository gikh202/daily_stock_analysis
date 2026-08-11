from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.run_v6_daily as base_runner
import scripts.run_v6_daily_stage9 as stage9_runner
from src.v6_daily.legacy_retirement import assert_legacy_retirement_ready
from src.v6_daily.legacy_write_guard import (
    assert_legacy_facts_unchanged,
    snapshot_legacy_facts,
)
from src.v6_daily.normalized_write_store import NormalizedOnlyV6WriteStore


STAGE10_ENTRYPOINT_VERSION = "v6-stage10-legacy-projection-shutdown-v1"


def _persist_stage10_metadata(
    result: Dict[str, Any],
    *,
    report_dir: str,
    retirement: Dict[str, Any],
    write_guard: Dict[str, Any],
) -> Dict[str, Any]:
    result["legacy_retirement"] = retirement
    result["legacy_write_guard"] = write_guard
    result["stage10_entrypoint"] = STAGE10_ENTRYPOINT_VERSION

    output = Path(report_dir)
    run_path = output / "v6_run.json"
    payload_path = output / "v6_daily_latest.json"
    for path in (run_path, payload_path):
        if not path.is_file():
            raise RuntimeError(f"Stage 10 expected output missing: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["legacy_retirement"] = retirement
        data["legacy_write_guard"] = write_guard
        if path == run_path:
            data["stage10_entrypoint"] = STAGE10_ENTRYPOINT_VERSION
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return result


def run(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Run Stage 9 normalized consumers with legacy production writes disabled."""
    v6_db_path = str(kwargs.get("v6_db_path") or "v6_data/v6_daily.db")
    report_dir = str(kwargs.get("report_dir") or "v6_reports")

    legacy_before = snapshot_legacy_facts(v6_db_path)

    # The original runner resolves this global at execution time. Stage 10 swaps
    # only the writer; Stage 9 continues to provide normalized-only consumers,
    # manifest persistence and read self-consistency guards.
    base_runner.CanonicalV6WriteStore = NormalizedOnlyV6WriteStore
    result = stage9_runner.run(*args, **kwargs)

    legacy_after = snapshot_legacy_facts(v6_db_path)
    write_guard = assert_legacy_facts_unchanged(legacy_before, legacy_after)
    retirement = assert_legacy_retirement_ready(
        v6_db_path,
        projection_enabled=False,
    )
    retirement["entrypoint_version"] = STAGE10_ENTRYPOINT_VERSION

    write_path = dict(result.get("write_path") or {})
    if write_path.get("legacy_projection_enabled") is not False:
        raise RuntimeError(f"Stage 10 writer still enables legacy projection: {write_path}")
    if int(write_path.get("legacy_projection_writes") or 0) != 0:
        raise RuntimeError(f"Stage 10 writer reported legacy projection writes: {write_path}")
    if write_path.get("automatic_legacy_bootstrap") is not False:
        raise RuntimeError(f"Stage 10 automatic legacy bootstrap is not disabled: {write_path}")

    return _persist_stage10_metadata(
        result,
        report_dir=report_dir,
        retirement=retirement,
        write_guard=write_guard,
    )


# base_runner.main resolves its module-global ``run`` at invocation time. This
# assignment affects only the explicit Stage 10 CLI entrypoint.
base_runner.run = run


if __name__ == "__main__":
    raise SystemExit(base_runner.main())
