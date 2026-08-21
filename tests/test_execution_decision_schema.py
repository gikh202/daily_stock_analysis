# -*- coding: utf-8 -*-
"""V7.2 execution decision schema tests."""

from api.v1.schemas.execution_decision import ExecutionDecision


def test_full_approved_syncs_legacy_authorization():
    decision = ExecutionDecision(execution_status="FULL_APPROVED")
    decision.sync_legacy_authorization()

    assert decision.execution_authorized is True
    assert decision.is_executable() is True


def test_conditional_approved_requires_confirmation():
    decision = ExecutionDecision(
        execution_status="CONDITIONAL_APPROVED",
        conditional_entry_price=100,
        conditional_entry_reason="valuation_wait",
    )

    decision.sync_legacy_authorization()

    assert decision.execution_authorized is False
    assert decision.requires_confirmation() is True


def test_rejected_blocks_execution():
    decision = ExecutionDecision(execution_status="REJECTED")

    decision.sync_legacy_authorization()

    assert decision.execution_authorized is False
    assert decision.is_executable() is False
