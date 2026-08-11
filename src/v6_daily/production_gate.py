from __future__ import annotations

from typing import Any, Dict, Mapping


PRODUCTION_READ_GATE_SCHEMA_VERSION = "v6-production-read-write-gate-v2"
EXPECTED_READ_SOURCE = "normalized_v6_tables"
EXPECTED_READ_MODE = "normalized_primary_with_legacy_parity_guard"
EXPECTED_REQUESTED_SOURCE = "normalized"
EXPECTED_NORMALIZED_RUN_MODE = "LIVE"
EXPECTED_WRITE_SOURCE = "normalized_v6_tables"
EXPECTED_WRITE_MODE = "normalized_primary_legacy_projection"


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def evaluate_production_read_gate(run_payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Evaluate whether a V6 run is safe to persist/notify as production.

    Production now requires both sides of the storage cutover:

    1. normalized tables are the canonical write destination and legacy tables
       are only exact compatibility projections;
    2. normalized tables are also the outward production read source with exact
       business parity, healthy SQLite integrity and LIVE run semantics.

    Legacy reads/writes remain available only as migration compatibility and
    diagnostics. They cannot independently authorize a production cache save or
    final notification.
    """
    read_cutover = _mapping(run_payload.get("read_cutover"))
    normalized_storage = _mapping(run_payload.get("normalized_storage"))
    write_path = _mapping(run_payload.get("write_path"))
    run_stats = _mapping(run_payload.get("run"))

    reasons: list[str] = []

    requested_source = str(read_cutover.get("requested_source") or "").strip().lower()
    selected_source = str(read_cutover.get("selected_source") or "").strip()
    read_mode = str(read_cutover.get("mode") or "").strip()
    read_parity = str(read_cutover.get("parity") or "").strip().lower()
    read_quick = str(read_cutover.get("quick_check") or "").strip().lower()
    foreign_key_errors = read_cutover.get("foreign_key_errors")

    storage_parity = str(normalized_storage.get("parity") or "").strip().lower()
    storage_quick = str(normalized_storage.get("quick_check") or "").strip().lower()
    storage_run_mode = str(normalized_storage.get("run_mode") or "").strip().upper()
    run_quick = str(run_stats.get("quick_check") or "").strip().lower()

    write_source = str(write_path.get("canonical_source") or "").strip()
    write_mode = str(write_path.get("mode") or "").strip()
    write_parity = str(write_path.get("parity") or "").strip().lower()
    write_quick = str(write_path.get("quick_check") or "").strip().lower()
    write_fk_errors = _int(write_path.get("foreign_key_errors"))

    if write_source != EXPECTED_WRITE_SOURCE:
        reasons.append(
            f"canonical write source={write_source!r} is not {EXPECTED_WRITE_SOURCE!r}"
        )
    if write_mode != EXPECTED_WRITE_MODE:
        reasons.append(f"write mode={write_mode!r} is not {EXPECTED_WRITE_MODE!r}")
    if write_parity != "exact":
        reasons.append(f"write parity={write_parity!r} is not exact")
    if write_quick != "ok":
        reasons.append(f"write quick_check={write_quick!r} is not ok")
    if write_fk_errors != 0:
        reasons.append(f"write foreign_key_errors={write_path.get('foreign_key_errors')!r}")

    canonical_signals = write_path.get("canonical_signals")
    legacy_signal_projections = write_path.get("legacy_signal_projections")
    canonical_outcomes = write_path.get("canonical_outcomes")
    legacy_outcome_projections = write_path.get("legacy_outcome_projections")
    if canonical_signals != legacy_signal_projections:
        reasons.append(
            "write signal projection mismatch "
            f"canonical={canonical_signals!r} legacy={legacy_signal_projections!r}"
        )
    if canonical_outcomes != legacy_outcome_projections:
        reasons.append(
            "write outcome projection mismatch "
            f"canonical={canonical_outcomes!r} legacy={legacy_outcome_projections!r}"
        )
    for field in (
        "missing_legacy_signal_projections",
        "missing_canonical_signal_rows",
        "missing_legacy_outcome_projections",
        "missing_canonical_outcome_rows",
    ):
        if _int(write_path.get(field)) != 0:
            reasons.append(f"write parity field {field}={write_path.get(field)!r}")

    if requested_source != EXPECTED_REQUESTED_SOURCE:
        reasons.append(
            f"requested_source={requested_source!r} is not production normalized"
        )
    if selected_source != EXPECTED_READ_SOURCE:
        reasons.append(
            f"selected_source={selected_source!r} is not {EXPECTED_READ_SOURCE!r}"
        )
    if read_mode != EXPECTED_READ_MODE:
        reasons.append(f"read mode={read_mode!r} is not {EXPECTED_READ_MODE!r}")
    if read_parity != "exact":
        reasons.append(f"read parity={read_parity!r} is not exact")
    if read_cutover.get("fail_closed") is not True:
        reasons.append("read cutover is not fail_closed")
    if read_quick != "ok":
        reasons.append(f"normalized read quick_check={read_quick!r} is not ok")
    fk_count = _int(foreign_key_errors)
    if fk_count != 0:
        reasons.append(f"normalized read foreign_key_errors={foreign_key_errors!r}")

    if storage_parity != "exact":
        reasons.append(f"normalized storage parity={storage_parity!r} is not exact")
    if storage_quick != "ok":
        reasons.append(f"normalized storage quick_check={storage_quick!r} is not ok")
    if storage_run_mode != EXPECTED_NORMALIZED_RUN_MODE:
        reasons.append(
            f"normalized storage run_mode={storage_run_mode!r} is not {EXPECTED_NORMALIZED_RUN_MODE!r}"
        )
    if run_quick != "ok":
        reasons.append(f"run quick_check={run_quick!r} is not ok")

    source_signals = normalized_storage.get("source_signals")
    forecast_runs = normalized_storage.get("forecast_runs")
    decision_runs = normalized_storage.get("decision_runs")
    execution_plans = normalized_storage.get("execution_plans")
    source_outcomes = normalized_storage.get("source_outcomes")
    forecast_outcomes = normalized_storage.get("forecast_outcomes")
    if source_signals != forecast_runs:
        reasons.append(
            f"normalized forecast parity mismatch source_signals={source_signals!r} forecast_runs={forecast_runs!r}"
        )
    if source_signals != decision_runs:
        reasons.append(
            f"normalized decision parity mismatch source_signals={source_signals!r} decision_runs={decision_runs!r}"
        )
    if source_signals != execution_plans:
        reasons.append(
            f"normalized plan parity mismatch source_signals={source_signals!r} execution_plans={execution_plans!r}"
        )
    if source_outcomes != forecast_outcomes:
        reasons.append(
            f"normalized outcome parity mismatch source_outcomes={source_outcomes!r} forecast_outcomes={forecast_outcomes!r}"
        )

    production_ready = not reasons
    return {
        "schema_version": PRODUCTION_READ_GATE_SCHEMA_VERSION,
        "status": "ready" if production_ready else "blocked",
        "production_ready": production_ready,
        "cache_persist_allowed": production_ready,
        "notification_allowed": production_ready,
        "expected_write_source": EXPECTED_WRITE_SOURCE,
        "write_source": write_source,
        "write_mode": write_mode,
        "write_parity": write_parity,
        "expected_source": EXPECTED_READ_SOURCE,
        "selected_source": selected_source,
        "requested_source": requested_source,
        "read_parity": read_parity,
        "normalized_storage_parity": storage_parity,
        "write_quick_check": write_quick,
        "normalized_read_quick_check": read_quick,
        "normalized_storage_quick_check": storage_quick,
        "write_foreign_key_errors": write_fk_errors,
        "foreign_key_errors": fk_count,
        "reasons": reasons,
        "legacy_policy": "compatibility_projection_and_diagnostics_only",
    }


def assert_production_read_gate(run_payload: Mapping[str, Any]) -> Dict[str, Any]:
    gate = evaluate_production_read_gate(run_payload)
    if not gate["production_ready"]:
        raise RuntimeError(
            "V6 production read/write gate blocked: "
            + "; ".join(gate.get("reasons") or ["unknown gate failure"])
        )
    return gate
