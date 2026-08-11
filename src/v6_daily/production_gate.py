from __future__ import annotations

from typing import Any, Dict, Mapping


PRODUCTION_READ_GATE_SCHEMA_VERSION = "v6-production-normalized-only-gate-v4"
EXPECTED_READ_SOURCE = "normalized_v6_tables"
EXPECTED_READ_MODE = "normalized_primary_self_consistency_guard"
EXPECTED_REQUESTED_SOURCE = "normalized"
EXPECTED_NORMALIZED_RUN_MODE = "LIVE"
EXPECTED_WRITE_SOURCE = "normalized_v6_tables"
EXPECTED_WRITE_MODE = "normalized_only_no_legacy_projection"
EXPECTED_IDENTITY_SOURCE = "normalized_sequence_only"


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def evaluate_production_read_gate(run_payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Evaluate Stage 10 normalized-only production readiness.

    Production is authorized only when reads, writes, Accuracy Lab and LIVE
    manifest persistence are normalized-only, active legacy consumers are zero,
    legacy projection is disabled, and a before/after fingerprint proves the
    historical legacy fact tables were not modified during the run.
    """
    read_cutover = _mapping(run_payload.get("read_cutover"))
    normalized_storage = _mapping(run_payload.get("normalized_storage"))
    write_path = _mapping(run_payload.get("write_path"))
    retirement = _mapping(run_payload.get("legacy_retirement"))
    legacy_write_guard = _mapping(run_payload.get("legacy_write_guard"))
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
    identity_source = str(write_path.get("identity_source") or "").strip()

    if write_source != EXPECTED_WRITE_SOURCE:
        reasons.append(
            f"canonical write source={write_source!r} is not {EXPECTED_WRITE_SOURCE!r}"
        )
    if write_mode != EXPECTED_WRITE_MODE:
        reasons.append(f"write mode={write_mode!r} is not {EXPECTED_WRITE_MODE!r}")
    if identity_source != EXPECTED_IDENTITY_SOURCE:
        reasons.append(
            f"write identity source={identity_source!r} is not {EXPECTED_IDENTITY_SOURCE!r}"
        )
    if write_parity != "exact":
        reasons.append(f"normalized write parity={write_parity!r} is not exact")
    if write_quick != "ok":
        reasons.append(f"write quick_check={write_quick!r} is not ok")
    if write_fk_errors != 0:
        reasons.append(f"write foreign_key_errors={write_path.get('foreign_key_errors')!r}")
    if write_path.get("legacy_projection_enabled") is not False:
        reasons.append("legacy projection is still enabled on the production writer")
    if _int(write_path.get("legacy_projection_writes")) != 0:
        reasons.append(
            f"legacy projection writes={write_path.get('legacy_projection_writes')!r} is not zero"
        )
    if _int(write_path.get("legacy_signal_projection_writes")) != 0:
        reasons.append(
            "legacy signal projection writes="
            f"{write_path.get('legacy_signal_projection_writes')!r} is not zero"
        )
    if _int(write_path.get("legacy_outcome_projection_writes")) != 0:
        reasons.append(
            "legacy outcome projection writes="
            f"{write_path.get('legacy_outcome_projection_writes')!r} is not zero"
        )
    if write_path.get("automatic_legacy_bootstrap") is not False:
        reasons.append("automatic legacy bootstrap is not disabled")

    canonical_signals = write_path.get("canonical_signals")
    decision_runs = write_path.get("decision_runs")
    execution_plans = write_path.get("execution_plans")
    if canonical_signals != decision_runs:
        reasons.append(
            "normalized write decision mismatch "
            f"forecast={canonical_signals!r} decision={decision_runs!r}"
        )
    if canonical_signals != execution_plans:
        reasons.append(
            "normalized write plan mismatch "
            f"forecast={canonical_signals!r} plan={execution_plans!r}"
        )

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
    if read_cutover.get("legacy_reference_used") is not False:
        reasons.append("production read cutover still depends on a legacy reference")
    if _int(read_cutover.get("legacy_consumer_count")) != 0:
        reasons.append(
            f"read cutover legacy_consumer_count={read_cutover.get('legacy_consumer_count')!r}"
        )
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
    if normalized_storage.get("legacy_reference_used") is not False:
        reasons.append("LIVE manifest persistence still uses a legacy reference")
    if str(normalized_storage.get("source_mode") or "").strip() != "normalized_only":
        reasons.append(
            f"normalized storage source_mode={normalized_storage.get('source_mode')!r}"
        )
    if run_quick != "ok":
        reasons.append(f"run quick_check={run_quick!r} is not ok")

    source_signals = normalized_storage.get("source_signals")
    forecast_runs = normalized_storage.get("forecast_runs")
    manifest_decision_runs = normalized_storage.get("decision_runs")
    manifest_execution_plans = normalized_storage.get("execution_plans")
    source_outcomes = normalized_storage.get("source_outcomes")
    forecast_outcomes = normalized_storage.get("forecast_outcomes")
    if source_signals != forecast_runs:
        reasons.append(
            f"normalized forecast parity mismatch source_signals={source_signals!r} forecast_runs={forecast_runs!r}"
        )
    if source_signals != manifest_decision_runs:
        reasons.append(
            f"normalized decision parity mismatch source_signals={source_signals!r} decision_runs={manifest_decision_runs!r}"
        )
    if source_signals != manifest_execution_plans:
        reasons.append(
            f"normalized plan parity mismatch source_signals={source_signals!r} execution_plans={manifest_execution_plans!r}"
        )
    if source_outcomes != forecast_outcomes:
        reasons.append(
            f"normalized outcome parity mismatch source_outcomes={source_outcomes!r} forecast_outcomes={forecast_outcomes!r}"
        )

    retirement_status = str(retirement.get("status") or "").strip()
    retirement_quick = str(retirement.get("quick_check") or "").strip().lower()
    retirement_fk_errors = _int(retirement.get("foreign_key_errors"))
    legacy_consumer_count = _int(retirement.get("legacy_consumer_count"))
    if retirement_status != "retirement_ready":
        reasons.append(f"legacy retirement status={retirement_status!r} is not ready")
    if retirement.get("projection_retirement_ready") is not True:
        reasons.append("legacy projection retirement readiness is false")
    if retirement.get("legacy_projection_required_by_active_consumers") is not False:
        reasons.append("an active consumer still requires legacy projection")
    if retirement.get("legacy_projection_enabled") is not False:
        reasons.append("legacy retirement metadata still marks projection enabled")
    if legacy_consumer_count != 0:
        reasons.append(f"legacy_consumer_count={legacy_consumer_count!r} is not zero")
    if retirement.get("legacy_fk_dependencies") not in ([], ()):
        reasons.append(
            f"legacy FK dependencies remain: {retirement.get('legacy_fk_dependencies')!r}"
        )
    if retirement.get("missing_normalized_dependencies") not in ([], ()):
        reasons.append(
            "normalized retirement dependencies missing: "
            f"{retirement.get('missing_normalized_dependencies')!r}"
        )
    if retirement_quick != "ok":
        reasons.append(f"legacy retirement quick_check={retirement_quick!r} is not ok")
    if retirement_fk_errors != 0:
        reasons.append(
            f"legacy retirement foreign_key_errors={retirement.get('foreign_key_errors')!r}"
        )

    guard_status = str(legacy_write_guard.get("status") or "").strip().lower()
    if guard_status != "unchanged":
        reasons.append(f"legacy write guard status={guard_status!r} is not unchanged")
    if legacy_write_guard.get("legacy_writes_detected") is not False:
        reasons.append("legacy write guard detected a fact-table mutation")
    if legacy_write_guard.get("legacy_projection_enabled") is not False:
        reasons.append("legacy write guard marks projection enabled")
    if _int(legacy_write_guard.get("legacy_projection_writes")) != 0:
        reasons.append(
            "legacy write guard projection writes="
            f"{legacy_write_guard.get('legacy_projection_writes')!r} is not zero"
        )
    if legacy_write_guard.get("before") != legacy_write_guard.get("after"):
        reasons.append("legacy fact-table before/after fingerprints differ")

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
        "identity_source": identity_source,
        "legacy_projection_enabled": write_path.get("legacy_projection_enabled") is True,
        "legacy_projection_writes": _int(write_path.get("legacy_projection_writes")),
        "expected_source": EXPECTED_READ_SOURCE,
        "selected_source": selected_source,
        "requested_source": requested_source,
        "read_mode": read_mode,
        "read_parity": read_parity,
        "normalized_storage_parity": storage_parity,
        "write_quick_check": write_quick,
        "normalized_read_quick_check": read_quick,
        "normalized_storage_quick_check": storage_quick,
        "write_foreign_key_errors": write_fk_errors,
        "foreign_key_errors": fk_count,
        "legacy_consumer_count": legacy_consumer_count,
        "projection_retirement_ready": retirement.get("projection_retirement_ready") is True,
        "legacy_fact_tables_unchanged": guard_status == "unchanged",
        "reasons": reasons,
        "legacy_policy": "historical_read_only_explicit_migration_source",
    }


def assert_production_read_gate(run_payload: Mapping[str, Any]) -> Dict[str, Any]:
    gate = evaluate_production_read_gate(run_payload)
    if not gate["production_ready"]:
        raise RuntimeError(
            "V6 production normalized-only gate blocked: "
            + "; ".join(gate.get("reasons") or ["unknown gate failure"])
        )
    return gate
