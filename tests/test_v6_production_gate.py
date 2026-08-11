from __future__ import annotations

import copy

import pytest

from src.v6_daily.production_gate import (
    assert_production_read_gate,
    evaluate_production_read_gate,
)


def _ready_payload() -> dict:
    return {
        "run": {"quick_check": "ok"},
        "write_path": {
            "canonical_source": "normalized_v6_tables",
            "mode": "normalized_primary_legacy_projection",
            "parity": "exact",
            "quick_check": "ok",
            "foreign_key_errors": 0,
            "canonical_signals": 4,
            "legacy_signal_projections": 4,
            "canonical_outcomes": 12,
            "legacy_outcome_projections": 12,
            "missing_legacy_signal_projections": 0,
            "missing_canonical_signal_rows": 0,
            "missing_legacy_outcome_projections": 0,
            "missing_canonical_outcome_rows": 0,
        },
        "read_cutover": {
            "requested_source": "normalized",
            "selected_source": "normalized_v6_tables",
            "mode": "normalized_primary_with_legacy_parity_guard",
            "parity": "exact",
            "fail_closed": True,
            "quick_check": "ok",
            "foreign_key_errors": 0,
        },
        "normalized_storage": {
            "run_mode": "LIVE",
            "parity": "exact",
            "quick_check": "ok",
            "source_signals": 4,
            "forecast_runs": 4,
            "decision_runs": 4,
            "execution_plans": 4,
            "source_outcomes": 12,
            "forecast_outcomes": 12,
        },
    }


def test_production_gate_requires_exact_normalized_write_and_read() -> None:
    payload = _ready_payload()
    gate = assert_production_read_gate(payload)

    assert gate["status"] == "ready"
    assert gate["production_ready"] is True
    assert gate["cache_persist_allowed"] is True
    assert gate["notification_allowed"] is True
    assert gate["write_source"] == "normalized_v6_tables"
    assert gate["write_mode"] == "normalized_primary_legacy_projection"
    assert gate["write_parity"] == "exact"
    assert gate["selected_source"] == "normalized_v6_tables"
    assert gate["read_parity"] == "exact"
    assert gate["reasons"] == []


def test_production_gate_treats_manual_legacy_fallback_as_diagnostic_only() -> None:
    payload = _ready_payload()
    payload["read_cutover"].update(
        {
            "requested_source": "legacy",
            "selected_source": "legacy_v6_signals_outcomes",
            "mode": "manual_legacy_fallback",
            "fail_closed": False,
        }
    )

    gate = evaluate_production_read_gate(payload)
    assert gate["production_ready"] is False
    assert gate["cache_persist_allowed"] is False
    assert gate["notification_allowed"] is False
    assert gate["legacy_policy"] == "compatibility_projection_and_diagnostics_only"
    assert any("requested_source" in reason for reason in gate["reasons"])
    assert any("selected_source" in reason for reason in gate["reasons"])
    with pytest.raises(RuntimeError, match="production read/write gate blocked"):
        assert_production_read_gate(payload)


def test_production_gate_blocks_write_integrity_or_projection_drift() -> None:
    mutations = [
        ("write_path", "canonical_source", "legacy_v6_signals"),
        ("write_path", "mode", "legacy_primary_dual_write"),
        ("write_path", "parity", "drift"),
        ("write_path", "quick_check", "corrupt"),
        ("write_path", "foreign_key_errors", 1),
        ("write_path", "legacy_signal_projections", 3),
        ("write_path", "legacy_outcome_projections", 11),
        ("write_path", "missing_legacy_signal_projections", 1),
        ("write_path", "missing_canonical_outcome_rows", 1),
    ]

    for section, key, value in mutations:
        payload = copy.deepcopy(_ready_payload())
        payload[section][key] = value
        gate = evaluate_production_read_gate(payload)
        assert gate["production_ready"] is False, (section, key, value)
        assert gate["reasons"], (section, key, value)


def test_production_gate_blocks_read_or_storage_drift() -> None:
    mutations = [
        ("read_cutover", "parity", "mismatch"),
        ("read_cutover", "quick_check", "corrupt"),
        ("read_cutover", "foreign_key_errors", 1),
        ("normalized_storage", "parity", "mismatch"),
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
