from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.v6_daily.legacy_archive import (
    export_verified_legacy_archive,
    inspect_legacy_facts,
    restore_verified_legacy_archive,
    verify_legacy_archive,
)
from src.v6_daily.legacy_retirement_gate_v6 import assert_legacy_retirement_gate_v6
from src.v6_daily.normalized_schema import ensure_normalized_schema
from src.v6_daily.production_import_guard import assert_production_import_graph_clean


STAGE12_REHEARSAL_EVIDENCE_VERSION = "v6-stage12-archive-restore-evidence-v1"


def _remove_sqlite(path: Path) -> None:
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        if candidate.exists():
            candidate.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a read-only legacy archive export plus isolated restore rehearsal"
    )
    parser.add_argument(
        "--v6-db",
        default=os.getenv("V6_DAILY_DB_PATH", "v6_data/v6_daily.db"),
    )
    parser.add_argument("--archive", default="v6_reports/stage12/legacy_archive.json")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--restore-db", default="v6_reports/stage12/legacy_restore.db")
    parser.add_argument("--schema-db", default="v6_reports/stage12/schema_rehearsal.db")
    parser.add_argument("--evidence-output", default="v6_reports/stage12/rehearsal_evidence.json")
    parser.add_argument("--source-commit", default=os.getenv("GITHUB_SHA"))
    parser.add_argument("--engine-version", default=None)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    source = Path(args.v6_db)
    archive = Path(args.archive)
    restore_db = Path(args.restore_db)
    schema_db = Path(args.schema_db)
    evidence_output = Path(args.evidence_output)

    if not source.is_file():
        raise SystemExit(f"legacy source database is missing: {source}")
    for target in (restore_db, schema_db):
        if target.exists() and not args.overwrite:
            raise SystemExit(f"rehearsal target already exists; pass --overwrite: {target}")
    if args.overwrite:
        _remove_sqlite(restore_db)
        _remove_sqlite(schema_db)

    before = inspect_legacy_facts(source)
    archive_summary = export_verified_legacy_archive(
        source,
        archive,
        manifest_path=args.manifest,
        source_commit=args.source_commit,
        engine_version=args.engine_version,
    )
    archive_verification = verify_legacy_archive(
        archive,
        manifest_path=args.manifest,
    )
    if not archive_verification.get("verified"):
        raise SystemExit(
            "archive verification failed: " + repr(archive_verification.get("errors"))
        )

    restore_summary = restore_verified_legacy_archive(
        archive,
        restore_db,
        manifest_path=args.manifest,
        overwrite=args.overwrite,
    )
    schema_registry = ensure_normalized_schema(schema_db)
    import_guard = assert_production_import_graph_clean(REPO_ROOT)
    after = inspect_legacy_facts(source)
    source_unchanged = before["tables"] == after["tables"]

    retirement_gate = assert_legacy_retirement_gate_v6(
        archive=archive_summary,
        restore=restore_summary,
        schema_registry=schema_registry,
        import_guard=import_guard,
        source_unchanged=source_unchanged,
    )

    evidence = {
        "schema_version": STAGE12_REHEARSAL_EVIDENCE_VERSION,
        "status": "pass",
        "source_database": str(source),
        "source_unchanged": source_unchanged,
        "archive": archive_summary,
        "archive_verification": archive_verification,
        "restore": restore_summary,
        "schema_registry_rehearsal": schema_registry,
        "production_import_guard": import_guard,
        "retirement_gate_v6": retirement_gate,
        "policies": {
            "source_opened_read_only_for_archive": True,
            "restore_isolated": True,
            "automatic_production_archive": False,
            "automatic_production_restore": False,
            "drop_legacy_tables": False,
        },
    }
    evidence_output.parent.mkdir(parents=True, exist_ok=True)
    evidence_output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
