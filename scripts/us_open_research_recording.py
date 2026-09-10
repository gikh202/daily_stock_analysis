from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from scripts.us_open_research_ledger import (
    NY,
    _finite,
    _json,
    _parse_dt,
    connect,
    signal_key,
)


SCHEMA_VERSION = "us-open-research-ledger-v5"
_EXECUTION_STATES = {
    "FULL_APPROVED",
    "CONDITIONAL_APPROVED",
    "UNRESOLVED",
    "HARD_REJECTED",
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def close_execution_contract(packet: Mapping[str, Any]) -> dict[str, Any]:
    assessment = _mapping(packet.get("assessment"))
    execution = _mapping(packet.get("execution"))
    contract = _mapping(packet.get("execution_contract"))

    raw_status = str(
        assessment.get("execution_status")
        or execution.get("execution_status")
        or contract.get("status")
        or ""
    ).strip().upper()
    reject_reason = str(
        assessment.get("reject_reason_code")
        or execution.get("reject_reason_code")
        or contract.get("reject_reason_code")
        or ""
    ).strip() or None

    if raw_status in _EXECUTION_STATES:
        execution_status = raw_status
    elif raw_status == "REJECTED":
        # REJECTED from pre-V9 packets did not distinguish uncertainty from an
        # invariant risk veto. Preserve safety when a machine reason exists;
        # otherwise migrate it to UNRESOLVED instead of fabricating a hard veto.
        execution_status = "HARD_REJECTED" if reject_reason else "UNRESOLVED"
    else:
        authorized = bool(
            assessment.get("execution_authorized")
            if "execution_authorized" in assessment
            else execution.get("execution_authorized")
        )
        worth_buying = assessment.get("worth_buying")
        if authorized:
            execution_status = "FULL_APPROVED"
        elif worth_buying is True:
            execution_status = "CONDITIONAL_APPROVED"
        elif reject_reason:
            execution_status = "HARD_REJECTED"
        else:
            execution_status = "UNRESOLVED"

    conditional_entry_price = _finite(
        assessment.get("conditional_entry_price")
        or execution.get("conditional_entry_price")
    )
    conditional_entry_reason = str(
        assessment.get("conditional_entry_reason")
        or execution.get("conditional_entry_reason")
        or ""
    ).strip() or None
    verdict = str(assessment.get("verdict") or "").strip().lower() or None
    worth_buying = assessment.get("worth_buying")

    return {
        "execution_status": execution_status,
        "execution_authorized": execution_status == "FULL_APPROVED",
        "hard_block": execution_status == "HARD_REJECTED",
        "reject_reason_code": reject_reason,
        "conditional_entry_price": conditional_entry_price,
        "conditional_entry_reason": conditional_entry_reason,
        "verdict": verdict,
        "worth_buying": worth_buying if isinstance(worth_buying, bool) else None,
        "legacy_status": (
            "REJECTED"
            if execution_status in {"UNRESOLVED", "HARD_REJECTED"}
            else execution_status
        ),
    }


def record_signal(
    path: str | Path,
    *,
    packet: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    decision: Mapping[str, Any],
    evaluated_at: datetime,
    policy_version: str,
    source_run_id: str | None,
) -> bool:
    identity = _mapping(packet.get("identity"))
    symbol = str(identity.get("symbol") or decision.get("symbol") or "").strip().upper()
    bar_time = _parse_dt(
        snapshot.get("last_bar_time") or decision.get("source_last_bar_time")
    )
    signal_price = _finite(
        snapshot.get("current_price") or decision.get("current_price")
    )
    if not symbol or bar_time is None or signal_price is None or signal_price <= 0:
        return False

    session_date = bar_time.date().isoformat()
    key = signal_key(
        session_date=session_date,
        symbol=symbol,
        policy_version=policy_version,
        source_run_id=source_run_id,
        signal_bar_time=bar_time.isoformat(),
    )
    close_plan = close_execution_contract(packet)
    open_action = str(
        decision.get("action") or decision.get("status") or ""
    ).strip().upper()
    transition = f"{close_plan['execution_status']}->{open_action or 'UNKNOWN'}"

    with connect(path) as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO us_open_signals(
                schema_version, signal_key, session_date, symbol,
                policy_version, source_run_id, source_trade_date,
                evaluated_at, signal_bar_time, signal_price,
                decision_status, packet_json, snapshot_json, decision_json,
                market_regime, close_plan_json, execution_transition
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                SCHEMA_VERSION,
                key,
                session_date,
                symbol,
                policy_version,
                str(source_run_id or "") or None,
                str(identity.get("effective_trade_date") or "") or None,
                evaluated_at.astimezone(NY).isoformat(),
                bar_time.isoformat(),
                signal_price,
                str(decision.get("action") or decision.get("status") or ""),
                _json(packet),
                _json(snapshot),
                _json(decision),
                str(decision.get("market_regime") or "").strip().lower() or None,
                _json(close_plan),
                transition,
            ),
        )
        return cursor.rowcount > 0
