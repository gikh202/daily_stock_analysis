from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.run_alpha_shadow import run as run_shadow
from src.alpha_engine.shadow_store import AlphaShadowStore
from src.alpha_engine.validation import write_validation_report


def _reset_backtest_database(path: Path) -> None:
    normalized_name = path.name.lower()
    if "backtest" not in normalized_name:
        raise ValueError(
            "refusing to reset a database whose filename does not contain 'backtest'"
        )
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if candidate.exists():
            candidate.unlink()


def _evidence_state(validation: Dict[str, Any]) -> str:
    coverage = validation.get("coverage") or {}
    gate = validation.get("research_gate") or {}
    outcomes = int(coverage.get("outcomes") or 0)
    evaluated_signals = int(coverage.get("evaluated_signals") or 0)
    if outcomes <= 0 or evaluated_signals <= 0:
        return "no_mature_outcomes"
    if gate.get("status") == "insufficient_data":
        return "below_sample_floor"
    return "measurable"


def run(
    *,
    stock_db_path: str,
    backtest_db_path: str,
    report_dir: str,
    limit: int,
    min_samples: int,
    primary_horizon: int,
) -> Dict[str, Any]:
    backtest_db = Path(backtest_db_path)
    backtest_db.parent.mkdir(parents=True, exist_ok=True)
    _reset_backtest_database(backtest_db)

    shadow_stats = run_shadow(
        stock_db_path=stock_db_path,
        alpha_db_path=str(backtest_db),
        report_dir=report_dir,
        limit=max(1, int(limit)),
    )

    store = AlphaShadowStore(str(backtest_db))
    validation = write_validation_report(
        store,
        report_dir,
        min_samples=max(3, int(min_samples)),
        primary_horizon=max(1, int(primary_horizon)),
        stem="backtest_summary",
        title="V5 Alpha Historical Replay Backtest",
    )
    evidence_state = _evidence_state(validation)
    payload = {
        "mode": "historical_replay",
        "execution_status": "success",
        "evidence_state": evidence_state,
        "performance_evidence_available": evidence_state == "measurable",
        "lookahead_policy": (
            "features come from stored analysis snapshots; outcomes use only trading "
            "bars strictly after each original analysis date"
        ),
        "interpretation": (
            "execution_status only confirms that the replay pipeline completed. "
            "Performance claims require measurable matured outcomes."
        ),
        "shadow_run": shadow_stats,
        "validation": validation,
    }
    output = Path(report_dir)
    (output / "backtest_run.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay historical production analysis snapshots through V5 Alpha"
    )
    parser.add_argument(
        "--stock-db",
        default=os.getenv("STOCK_DB_PATH", "data/stock_analysis.db"),
    )
    parser.add_argument(
        "--backtest-db",
        default=os.getenv("ALPHA_BACKTEST_DB_PATH", "alpha_data/alpha_backtest.db"),
    )
    parser.add_argument(
        "--report-dir",
        default=os.getenv("ALPHA_BACKTEST_REPORT_DIR", "alpha_reports/backtest"),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=int(os.getenv("ALPHA_BACKTEST_SCAN_LIMIT", "5000")),
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=int(os.getenv("ALPHA_VALIDATION_MIN_SAMPLES", "20")),
    )
    parser.add_argument(
        "--primary-horizon",
        type=int,
        default=int(os.getenv("ALPHA_VALIDATION_PRIMARY_HORIZON", "5")),
    )
    args = parser.parse_args()
    payload = run(
        stock_db_path=args.stock_db,
        backtest_db_path=args.backtest_db,
        report_dir=args.report_dir,
        limit=args.limit,
        min_samples=args.min_samples,
        primary_horizon=args.primary_horizon,
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
