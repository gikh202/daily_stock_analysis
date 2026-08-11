from __future__ import annotations

from typing import Any, Dict, Mapping

from .legacy_archive import (
    LEGACY_ARCHIVE_MANIFEST_VERSION,
    LEGACY_ARCHIVE_SCHEMA_VERSION,
    LEGACY_RESTORE_SCHEMA_VERSION,
)
from .normalized_schema import (
    ACCURACY_LAB_MIGRATION_ID,
    NORMALIZED_ACCURACY_LAB_SCHEMA_VERSION,
    NORMALIZED_SCHEMA_REGISTRY_VERSION,
)
from .production_import_guard import PRODUCTION_IMPORT_GUARD_SCHEMA_VERSION


LEGACY_RETIREMENT_GATE_V6_SCHEMA_VERSION = "v6-legacy-retirement-gate-v6"


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def evaluate_legacy_retirement_gate_v6(
    *,
    archive: Mapping[str, Any],
    restore: Mapping[str, Any],
    schema_registry: Mapping[str, Any],
    import_guard: Mapping[str, Any],
    source_unchanged: bool,
) -> Dict[str, Any]:
    """Decide whether Stage 12 evidence is strong enough to enter Stage 13.

    Passing this gate does not drop tables. It only certifies that a verified,
    restorable legacy archive exists and that the normalized runtime/schema
    boundary is ready for a separately reviewed Stage 13 physical-drop change.
    """
    archive_data = _mapping(archive)
    restore_data = _mapping(restore)
    registry = _mapping(schema_registry)
    imports = _mapping(import_guard)
    reasons: list[str] = []

    if archive_data.get("schema_version") != LEGACY_ARCHIVE_SCHEMA_VERSION:
        reasons.append("verified archive schema version mismatch")
    if archive_data.get("manifest_version") != LEGACY_ARCHIVE_MANIFEST_VERSION:
        reasons.append("archive manifest version mismatch")
    if str(archive_data.get("status") or "") != "verified_archive_exported":
        reasons.append(f"archive status={archive_data.get('status')!r} is not verified")
    if archive_data.get("source_mutated") is not False:
        reasons.append("archive export may have mutated the source database")
    if archive_data.get("source_unchanged") is not True:
        reasons.append("archive export did not prove source facts unchanged")
    if not str(archive_data.get("archive_file_sha256") or ""):
        reasons.append("archive file SHA256 is missing")
    if not str(archive_data.get("content_sha256") or ""):
        reasons.append("archive canonical content SHA256 is missing")
    if not str(archive_data.get("schema_snapshot_sha256") or ""):
        reasons.append("archive schema snapshot SHA256 is missing")
    if _int(archive_data.get("source_foreign_key_errors")) != 0:
        reasons.append("archive source foreign_key_check is not clean")
    if str(archive_data.get("source_quick_check") or "").strip().lower() != "ok":
        reasons.append("archive source quick_check is not ok")

    if restore_data.get("schema_version") != LEGACY_RESTORE_SCHEMA_VERSION:
        reasons.append("restore rehearsal schema version mismatch")
    if str(restore_data.get("status") or "").lower() != "pass":
        reasons.append(f"restore rehearsal status={restore_data.get('status')!r} is not pass")
    if restore_data.get("verified") is not True:
        reasons.append("restore rehearsal is not verified")
    if restore_data.get("archive_verified") is not True:
        reasons.append("restore did not consume a verified archive")
    if restore_data.get("isolated_restore") is not True:
        reasons.append("restore rehearsal was not isolated")
    if restore_data.get("source_database_mutated") is not False:
        reasons.append("restore rehearsal may have mutated the source database")
    if str(restore_data.get("quick_check") or "").strip().lower() != "ok":
        reasons.append("restored database quick_check is not ok")
    if _int(restore_data.get("foreign_key_errors")) != 0:
        reasons.append("restored database foreign_key_check is not clean")
    if restore_data.get("archive_content_sha256") != archive_data.get("content_sha256"):
        reasons.append("restore/archive canonical content SHA256 mismatch")
    if restore_data.get("schema_snapshot_sha256") != archive_data.get("schema_snapshot_sha256"):
        reasons.append("restore/archive schema snapshot SHA256 mismatch")
    if restore_data.get("restored_schema_snapshot_sha256") != archive_data.get("schema_snapshot_sha256"):
        reasons.append("restored schema snapshot does not match archived schema")
    if _int(restore_data.get("legacy_signal_rows")) != _int(archive_data.get("legacy_signal_rows")):
        reasons.append("restored legacy signal row count differs from archive")
    if _int(restore_data.get("legacy_outcome_rows")) != _int(archive_data.get("legacy_outcome_rows")):
        reasons.append("restored legacy outcome row count differs from archive")

    if registry.get("registry_version") != NORMALIZED_SCHEMA_REGISTRY_VERSION:
        reasons.append("normalized schema registry version mismatch")
    if str(registry.get("status") or "") != "current":
        reasons.append(f"normalized schema registry status={registry.get('status')!r} is not current")
    if registry.get("pending_migrations") not in ([], ()):
        reasons.append(f"normalized schema migrations still pending: {registry.get('pending_migrations')!r}")
    if registry.get("accuracy_lab_schema_version") != NORMALIZED_ACCURACY_LAB_SCHEMA_VERSION:
        reasons.append("Accuracy Lab schema is not owned by the unified registry")
    migration_ids = {
        str(item.get("migration_id") or "")
        for item in (registry.get("migrations") or [])
        if isinstance(item, Mapping)
    }
    if ACCURACY_LAB_MIGRATION_ID not in migration_ids:
        reasons.append("Accuracy Lab migration is absent from the unified registry")
    if str(registry.get("quick_check") or "").strip().lower() != "ok":
        reasons.append("schema rehearsal quick_check is not ok")
    if _int(registry.get("foreign_key_errors")) != 0:
        reasons.append("schema rehearsal foreign_key_check is not clean")

    if imports.get("schema_version") != PRODUCTION_IMPORT_GUARD_SCHEMA_VERSION:
        reasons.append("production import guard schema mismatch")
    if str(imports.get("status") or "") != "clean":
        reasons.append("production import graph is not clean")
    if _int(imports.get("forbidden_import_count")) != 0:
        reasons.append("production import graph still reaches retired compatibility runtime")

    if source_unchanged is not True:
        reasons.append("Stage 12 source fingerprint changed during rehearsal")

    ready = not reasons
    return {
        "schema_version": LEGACY_RETIREMENT_GATE_V6_SCHEMA_VERSION,
        "status": "ready_for_stage13" if ready else "blocked",
        "stage13_eligible": ready,
        "physical_drop_allowed_now": False,
        "requires_separate_stage13_change": True,
        "archive_verified": archive_data.get("status") == "verified_archive_exported",
        "restore_verified": restore_data.get("verified") is True,
        "source_unchanged": source_unchanged is True,
        "normalized_schema_registry_status": registry.get("status"),
        "accuracy_lab_migration_registered": ACCURACY_LAB_MIGRATION_ID in migration_ids,
        "production_import_graph_status": imports.get("status"),
        "forbidden_import_count": _int(imports.get("forbidden_import_count")),
        "reasons": reasons,
    }


def assert_legacy_retirement_gate_v6(
    *,
    archive: Mapping[str, Any],
    restore: Mapping[str, Any],
    schema_registry: Mapping[str, Any],
    import_guard: Mapping[str, Any],
    source_unchanged: bool,
) -> Dict[str, Any]:
    gate = evaluate_legacy_retirement_gate_v6(
        archive=archive,
        restore=restore,
        schema_registry=schema_registry,
        import_guard=import_guard,
        source_unchanged=source_unchanged,
    )
    if not gate["stage13_eligible"]:
        raise RuntimeError(
            "V6 Stage 12 legacy retirement gate blocked: "
            + "; ".join(gate.get("reasons") or ["unknown gate failure"])
        )
    return gate
