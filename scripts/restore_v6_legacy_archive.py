from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.v6_daily.legacy_archive import (
    restore_verified_legacy_archive,
    verify_legacy_archive,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify and restore a legacy V6 archive into an isolated SQLite database"
    )
    parser.add_argument("--archive", required=True)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--restore-db", default=None)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--output",
        default=None,
        help="Optional JSON evidence output path.",
    )
    args = parser.parse_args()

    if args.verify_only:
        result = verify_legacy_archive(args.archive, manifest_path=args.manifest)
        if not result.get("verified"):
            raise SystemExit("legacy archive verification failed: " + repr(result.get("errors")))
    else:
        restore_db = args.restore_db or "v6_reports/legacy_restore_rehearsal.db"
        result = restore_verified_legacy_archive(
            args.archive,
            restore_db,
            manifest_path=args.manifest,
            overwrite=args.overwrite,
        )
        if not result.get("verified"):
            raise SystemExit("legacy archive restore rehearsal failed: " + repr(result.get("errors")))

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
