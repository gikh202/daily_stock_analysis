from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.v6_daily.legacy_physical_retirement import retire_legacy_tables


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Retire V6 legacy fact tables after exact normalized coverage, verified archive, "
            "and isolated restore. Dry-run is the default; --apply is required for migration/DROP."
        )
    )
    parser.add_argument("--v6-db", required=True)
    parser.add_argument("--archive-dir", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--source-commit")
    parser.add_argument("--engine-version")
    parser.add_argument("--report-date")
    parser.add_argument(
        "--migrate-missing-coverage",
        action="store_true",
        help=(
            "Explicitly request the one-time legacy-to-normalized coverage bridge. "
            "For the retirement CLI, --apply also authorizes this bridge so old production "
            "caches can upgrade atomically through the guarded retirement sequence."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Apply the guarded retirement. If legacy identities are not fully normalized, "
            "this explicitly authorizes a normalized-only coverage bridge before archive/restore/DROP."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = retire_legacy_tables(
        args.v6_db,
        archive_dir=args.archive_dir,
        receipt_path=args.receipt,
        repo_root=args.repo_root,
        source_commit=args.source_commit,
        engine_version=args.engine_version,
        report_date=args.report_date,
        migrate_missing_coverage=bool(args.migrate_missing_coverage or args.apply),
        apply=bool(args.apply),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if args.apply and result.get("legacy_tables_absent") is not True:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
