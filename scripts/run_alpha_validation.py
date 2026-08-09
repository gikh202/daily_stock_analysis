from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.alpha_engine.shadow_store import AlphaShadowStore
from src.alpha_engine.validation import write_validation_report


def run(
    *,
    alpha_db_path: str,
    report_dir: str,
    min_samples: int,
    primary_horizon: int,
) -> dict:
    store = AlphaShadowStore(alpha_db_path)
    if store.quick_check().strip().lower() != "ok":
        raise RuntimeError("alpha shadow database quick_check failed")
    return write_validation_report(
        store,
        report_dir,
        min_samples=max(3, int(min_samples)),
        primary_horizon=max(1, int(primary_horizon)),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate V5 Alpha validation metrics")
    parser.add_argument(
        "--alpha-db",
        default=os.getenv("ALPHA_SHADOW_DB_PATH", "alpha_data/alpha_shadow.db"),
    )
    parser.add_argument(
        "--report-dir",
        default=os.getenv("ALPHA_REPORT_DIR", "alpha_reports"),
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
    summary = run(
        alpha_db_path=args.alpha_db,
        report_dir=args.report_dir,
        min_samples=args.min_samples,
        primary_horizon=args.primary_horizon,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
