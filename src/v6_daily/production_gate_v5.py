from __future__ import annotations

from typing import Any, Dict, Mapping

from .normalized_schema import (
    NORMALIZED_CORE_SCHEMA_VERSION,
    NORMALIZED_SCHEMA_REGISTRY_VERSION,
)
from .production_gate import assert_stage10_production_gate
from .production_import_guard import PRODUCTION_IMPORT_GUARD_SCHEMA_VERSION


PRODUCTION_GATE_V5_SCHEMA_VERSION = "v6-production-archival-gate-v5"
EXPECTED_STAGE11_ENTRYPOINT = "v6-stage11-legacy-schema-archival-v1"


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def evaluate_stage11_production_gate(run_payload: Mapping[str, Any]) -> Dict[str, Any]:
    reasons: list[str] = []
    try:
        base = assert_stage10_production_gate(run_payload)
    except RuntimeError as exc:
        base = {"production_ready": False, "reasons": [str(exc)]}
        reasons.append(str(exc))

    stage11_entrypoint = str(run_payload.get("stage11_entrypoint") or "").strip()
    schema_registry = _mapping(run_payload.get("schema_registry"))
    import_guard = _mapping(run_payload.get("production_import_guard"))
    archival = _mapping(run_payload.get("legacy_archival"))
    write_path = _mapping(run_payload.get("write_path"))
    write_schema_registry = _mapping(write_path.get("schema_registry"))

    if stage11_entrypoint != EXPECTED_STAGE11_ENTRYPOINT:
        reasons.append(
            f"stage11_entrypoint={stage11_entrypoint!r} is not {EXPECTED_STAGE11_ENTRYPOINT!r}"
        )

    if schema_registry.get("registry_version") != NORMALIZED_SCHEMA_REGISTRY_VERSION:
        reasons.append(
            "schema registry version mismatch: "
            f"{schema_registry.get('registry_version')!r}"
        )
    if schema_registry.get("core_schema_version") != NORMALIZED_CORE_SCHEMA_VERSION:
        reasons.append(
            "normalized core schema version mismatch: "
            f"{schema_registry.get('core_schema_version')!r}"
        )
    if str(schema_registry.get("status") or "").strip().lower() != "current":
        reasons.append(f"schema registry status={schema_registry.get('status')!r} is not current")
    if schema_registry.get("pending_migrations") not in ([], ()):
        reasons.append(
            f"pending normalized schema migrations: {schema_registry.get('pending_migrations')!r}"
        )
    if str(schema_registry.get("quick_check") or "").strip().lower() != "ok":
        reasons.append("schema registry quick_check is not ok")
    if _int(schema_registry.get("foreign_key_errors")) != 0:
        reasons.append(
            f"schema registry foreign_key_errors={schema_registry.get('foreign_key_errors')!r}"
        )

    if write_schema_registry:
        if write_schema_registry.get("core_schema_version") != NORMALIZED_CORE_SCHEMA_VERSION:
            reasons.append("writer schema registry disagrees with Stage 11 core schema version")
        if str(write_schema_registry.get("status") or "").lower() != "current":
            reasons.append("writer schema registry is not current")

    if import_guard.get("schema_version") != PRODUCTION_IMPORT_GUARD_SCHEMA_VERSION:
        reasons.append(
            "production import guard schema mismatch: "
            f"{import_guard.get('schema_version')!r}"
        )
    if str(import_guard.get("status") or "").strip().lower() != "clean":
        reasons.append(f"production import graph status={import_guard.get('status')!r} is not clean")
    if _int(import_guard.get("forbidden_import_count")) != 0:
        reasons.append(
            "production import graph reaches forbidden modules: "
            f"{import_guard.get('violations')!r}"
        )
    if str(import_guard.get("entry_module") or "") != "scripts.run_v6_daily_stage11":
        reasons.append(
            f"unexpected production import-guard entry module: {import_guard.get('entry_module')!r}"
        )

    if str(archival.get("migration_policy") or "") != "explicit_cli_only":
        reasons.append(
            f"legacy migration policy={archival.get('migration_policy')!r} is not explicit_cli_only"
        )
    if str(archival.get("archive_policy") or "") != "explicit_cli_only":
        reasons.append(
            f"legacy archive policy={archival.get('archive_policy')!r} is not explicit_cli_only"
        )
    if archival.get("automatic_migration") is not False:
        reasons.append("automatic legacy migration is not disabled")
    if archival.get("automatic_archive") is not False:
        reasons.append("automatic legacy archive is not disabled")
    if archival.get("drop_legacy_tables") is not False:
        reasons.append("Stage 11 must not drop legacy tables")

    production_ready = bool(base.get("production_ready")) and not reasons
    return {
        "schema_version": PRODUCTION_GATE_V5_SCHEMA_VERSION,
        "status": "ready" if production_ready else "blocked",
        "production_ready": production_ready,
        "cache_persist_allowed": production_ready,
        "notification_allowed": production_ready,
        "base_gate_schema_version": base.get("schema_version"),
        "base_gate_ready": bool(base.get("production_ready")),
        "stage11_entrypoint": stage11_entrypoint,
        "schema_registry_status": schema_registry.get("status"),
        "core_schema_version": schema_registry.get("core_schema_version"),
        "production_import_graph_status": import_guard.get("status"),
        "forbidden_import_count": _int(import_guard.get("forbidden_import_count")),
        "legacy_migration_policy": archival.get("migration_policy"),
        "legacy_archive_policy": archival.get("archive_policy"),
        "reasons": reasons,
    }


def assert_stage11_production_gate(run_payload: Mapping[str, Any]) -> Dict[str, Any]:
    gate = evaluate_stage11_production_gate(run_payload)
    if not gate["production_ready"]:
        raise RuntimeError(
            "V6 Stage 11 production gate blocked: "
            + "; ".join(gate.get("reasons") or ["unknown gate failure"])
        )
    return gate
