from __future__ import annotations

import copy

import pytest

from src.v6_daily.production_gate import (
    assert_production_read_gate,
    evaluate_production_read_gate,
)


def _legacy_snapshot() -> dict:
    return {
        "database_present": True,
        "tables": {
            "v6_signals": {"present": True, "rows": 4, "sha256": "signals-hash"},
            "v6_outcomes": {"present": True, "rows": 12, "sha256": "outcomes-hash"},
        },
    }


def _ready_payload() -> dict:
    snapshot = _legacy_snapshot()
    return {
        "run": {"quick_check": "ok"},
        "write_path": {
            "canonical_source": "normalized_v6_tables",
            "mode": "normalized_only_no_legacy_projection",
            "identity_source": "normalized_sequence_only",
            "parity": "exact",
            "quick_check": "ok",
            "foreign_key_errors": 0,
            "canonical_signals": 4,
            "decision_runs": 4,
            "execution_plans": 4,
            "canonical_outcomes": 12,
            "legacy_projection_enabled": False,
            "legacy_projection_writes": 0,
            "legacy_signal_projection_writes": 0,
            "legacy_outcome_projection_writes": 0,
            "automatic_legacy_bootstrap": False,
        },
        "read_cutover": {
            "requested_source": "normalized",
            "selected_source": "normalized_v6_tables",
            "mode": "normalized_primary_self_consistency_guard",
            "parity": "exact",
            "fail_closed": True,
            "legacy_reference_used": False,
            "legacy_consumer_count": 0,
            "quick_check": "ok",
            "foreign_key_errors": 0,
        },
        "normalized_storage": {
            "run_mode": "LIVE",
            "parity": "exact",
            "quick_check": "ok",
            "foreign_key_errors": 0,
            "legacy_reference_used": False,
            "source_mode": "normalized_only",
            "source_signals": 4,
            "forecast_runs": 4,
            "decision_runs": 4,
            "execution_plans": 4,
            "source_outcomes": 12,
            "forecast_outcomes": 12,
        },
        "legacy_retirement": {
            "status": "retirement_ready",
            "projection_retirement_ready": True,
            "legacy_projection_required_by_active_consumers": False,
            "legacy_projection_enabled": False,
            "legacy_consumer_count": 0,
            "legacy_fk_dependencies": [],
            "missing_normalized_dependencies": [],
            "quick_check": "ok",
            "foreign_key_errors": 0,
        },
        "legacy_write_guard": {
            "status": "unchanged",
            "legacy_writes_detected": False,
            "legacy_projection_enabled": False,
            "legacy_projection_writes": 0,
            "before": copy.deepcopy(snapshot),
            "after": copy.deepcopy(snapshot),
            "changes": [],
        },
    }


def test_production_gate_requires_normalized_only_write_read_and_immutable_legacy() -> None:
    payload = _ready_payload()
    gate = assert_production_read_gate(payload)

    assert gate["status"] == "ready"
    assert gate["production_ready"] is True
    assert gate["cache_persist_allowed"] is True
    assert gate["notification_allowed"] is True
    assert gate["write_source"] == "normalized_v6_tables"
    assert gate["write_mode"] == "normalized_only_no_legacy_projection"
    assert gate["identity_source"] == "normalized_sequence_only"
    assert gate["write_parity"] == "exact"
    assert gate["legacy_projection_enabled"] is False
    assert gate["legacy_projection_writes"] == 0
    assert gate["selected_source"] == "normalized_v6_tables"
    assert gate["read_mode"] == "normalized_primary_self_consistency_guard"
    assert gate["read_parity"] == "exact"
    assert gate["legacy_consumer_count"] == 0
    assert gate["projection_retirement_ready"] is True
    assert gate["legacy_fact_tables_unchanged"] is True
    assert gate["legacy_policy"] == "historical_read_only_explicit_migration_source"
    assert gate["reasons"] == []


def test_production_gate_treats_manual_legacy_fallback_as_diagnostic_only() -> None:
    payload = _ready_payload()
    payload["read_cutover"].update(
        {
            "requested_source": "legacy",
            "selected_source": "legacy_v6_signals_outcomes",
            "mode": "manual_legacy_fallback",
            "legacy_reference_used": True,
            "fail_closed": False,
        }
    )

    gate = evaluate_production_read_gate(payload)
    assert gate["production_ready"] is False
    assert gate["cache_persist_allowed"] is False
    assert gate["notification_allowed"] is False
    assert any("requested_source" in reason for reason in gate["reasons"])
    assert any("selected_source" in reason for reason in gate["reasons"])
    assert any("legacy reference" in reason for reason in gate["reasons"])
    with pytest.raises(RuntimeError, match="production normalized-only gate blocked"):
        assert_production_read_gate(payload)


def test_production_gate_blocks_any_legacy_write_or_bootstrap_path() -> None:
    mutations = [
        ("canonical_source", "legacy_v6_signals"),
        ("mode", "normalized_primary_legacy_projection"),
        ("identity_source", "legacy_max_plus_one"),
        ("parity", "drift"),
        ("quick_check", "corrupt"),
        ("foreign_key_errors", 1),
        ("legacy_projection_enabled", True),
        ("legacy_projection_writes", 1),
        ("legacy_signal_projection_writes", 1),
        ("legacy_outcome_projection_writes", 1),
        ("automatic_legacy_bootstrap", True),
        ("decision_runs", 3),
        ("execution_plans", 3),
    ]

    for key, value in mutations:
        payload = copy.deepcopy(_ready_payload())
        payload["write_path"][key] = value
        gate = evaluate_production_read_gate(payload)
        assert gate["production_ready"] is False, (key, value)
        assert gate["reasons"], (key, value)


def test_production_gate_blocks_read_or_storage_drift() -> None:
    mutations = [
        ("read_cutover", "mode", "normalized_primary_with_legacy_parity_guard"),
        ("read_cutover", "parity", "mismatch"),
        ("read_cutover", "legacy_reference_used", True),
        ("read_cutover", "legacy_consumer_count", 1),
        ("read_cutover", "quick_check", "corrupt"),
        ("read_cutover", "foreign_key_errors", 1),
        ("normalized_storage", "parity", "mismatch"),
        ("normalized_storage", "legacy_reference_used", True),
        ("normalized_storage", "source_mode", "legacy_reconciled"),
        ("normalized_storage", "quick_check", "corrupt"),
        ("normalized_storage", "run_mode", "REPLAY"),
        ("normalized_storage", "decision_runs", 3),
        ("normalized_storage", "forecast_outcomes", 11),
        ("run", "quick_check", "corrupt"),
    ]

    for section, key, value in mutations:
        payload = copy.deepcopy(_ready_payload())
        payload[section][key] = value
        gate = evaluate_production_read_gate(payload)
        assert gate["production_ready"] is False, (section, key, value)
        assert gate["reasons"], (section, key, value)


def test_production_gate_blocks_any_remaining_active_legacy_consumer() -> None:
    mutations = [
        ("status", "blocked"),
        ("projection_retirement_ready", False),
        ("legacy_projection_required_by_active_consumers", True),
        ("legacy_projection_enabled", True),
        ("legacy_consumer_count", 1),
        ("legacy_fk_dependencies", [{"table": "lab", "target": "v6_signals"}]),
        ("missing_normalized_dependencies", ["v6_forecast_runs"]),
        ("quick_check", "corrupt"),
        ("foreign_key_errors", 1),
    ]

    for key, value in mutations:
        payload = copy.deepcopy(_ready_payload())
        payload["legacy_retirement"][key] = value
        gate = evaluate_production_read_gate(payload)
        assert gate["production_ready"] is False, (key, value)
        assert gate["reasons"], (key, value)


def test_production_gate_blocks_legacy_fingerprint_drift() -> None:
    mutations = [
        ("status", "changed"),
        ("legacy_writes_detected", True),
        ("legacy_projection_enabled", True),
        ("legacy_projection_writes", 1),
    ]
    for key, value in mutations:
        payload = copy.deepcopy(_ready_payload())
        payload["legacy_write_guard"][key] = value
        gate = evaluate_production_read_gate(payload)
        assert gate["production_ready"] is False, (key, value)
        assert gate["reasons"], (key, value)

    payload = copy.deepcopy(_ready_payload())
    payload["legacy_write_guard"]["after"]["tables"]["v6_signals"]["rows"] = 5
    gate = evaluate_production_read_gate(payload)
    assert gate["production_ready"] is False
    assert any("fingerprints differ" in reason for reason in gate["reasons"])
