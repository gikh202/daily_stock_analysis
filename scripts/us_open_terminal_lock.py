from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from scripts.run_us_open_timing import (
    OpenTimingDecision,
    POLICY_VERSION,
    _decision_payload,
    _notify,
    _read_previous,
    _should_notify,
    _signature,
    render_markdown,
    run as run_timing,
)

logger = logging.getLogger("us_open_terminal_lock")
NY = ZoneInfo("America/New_York")
LOCK_REASON_SUFFIX = "该标的已在本交易日进入终态，后续自动轮次保持锁定，不再重新计算。"


def _parse_previous_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=NY)
    return parsed.astimezone(NY)


def terminal_locks(
    previous: Mapping[str, Any] | None,
    *,
    source_run_id: str | None,
    evaluated_at: datetime,
    force_recompute: bool = False,
) -> dict[str, dict[str, Any]]:
    """Return reusable same-session terminal decisions keyed by symbol.

    A terminal decision is reusable only when it belongs to the same New York
    session date and the same close-forecast source run. Manual force-resend is
    intentionally a force-recompute escape hatch and therefore disables locks.
    """
    if force_recompute or not previous or source_run_id is None:
        return {}

    previous_source = str(previous.get("source_run_id") or "").strip()
    if not previous_source or previous_source != str(source_run_id).strip():
        return {}

    previous_time = _parse_previous_time(previous.get("generated_at"))
    if previous_time is None or previous_time.date() != evaluated_at.astimezone(NY).date():
        return {}

    result: dict[str, dict[str, Any]] = {}
    for raw in previous.get("decisions") or []:
        if not isinstance(raw, Mapping) or raw.get("terminal") is not True:
            continue
        symbol = str(raw.get("symbol") or "").strip().upper()
        if symbol:
            result[symbol] = dict(raw)
    return result


def _symbol_from_packet(packet: Mapping[str, Any]) -> str:
    identity = packet.get("identity") if isinstance(packet.get("identity"), Mapping) else {}
    return str(identity.get("symbol") or "").strip().upper()


def _filtered_payload(
    source_path: str | Path,
    *,
    locked_symbols: set[str],
) -> tuple[dict[str, Any], list[str]]:
    payload = json.loads(Path(source_path).read_text(encoding="utf-8"))
    final = payload.get("final_decisions") if isinstance(payload.get("final_decisions"), dict) else {}
    packets = [
        dict(item)
        for item in (final.get("packets") or [])
        if isinstance(item, Mapping)
    ]
    ordered_symbols = [symbol for symbol in (_symbol_from_packet(item) for item in packets) if symbol]
    final["packets"] = [
        item for item in packets if _symbol_from_packet(item) not in locked_symbols
    ]
    payload["final_decisions"] = final

    board = payload.get("board")
    if isinstance(board, list):
        payload["board"] = [
            item
            for item in board
            if not isinstance(item, Mapping)
            or str(item.get("code") or "").strip().upper() not in locked_symbols
        ]
    return payload, ordered_symbols


def _decision_from_payload(raw: Mapping[str, Any], *, locked: bool) -> OpenTimingDecision:
    allowed = {item.name for item in fields(OpenTimingDecision)}
    values = {key: value for key, value in raw.items() if key in allowed}
    if "targets" in values and isinstance(values["targets"], list):
        values["targets"] = tuple(values["targets"])
    if locked:
        reason = str(values.get("reason") or "").strip()
        if LOCK_REASON_SUFFIX not in reason:
            values["reason"] = f"{reason}；{LOCK_REASON_SUFFIX}" if reason else LOCK_REASON_SUFFIX
    return OpenTimingDecision(**values)


def _summary(decisions: list[OpenTimingDecision], locked_symbols: set[str]) -> dict[str, Any]:
    return {
        "symbols": len(decisions),
        "buy_now": sum(item.action == "BUY_NOW" for item in decisions),
        "wait_better_entry": sum(item.action == "WAIT_BETTER_ENTRY" for item in decisions),
        "wait_confirmation": sum(item.action == "WAIT_CONFIRMATION" for item in decisions),
        "no_buy": sum(item.action in {"NO_BUY", "INVALIDATED"} for item in decisions),
        "data_unavailable": sum(item.action == "DATA_UNAVAILABLE" for item in decisions),
        "terminal_locked": len(locked_symbols),
        "execution_status_counts": {
            status: sum(item.execution_status == status for item in decisions)
            for status in ("FULL_APPROVED", "CONDITIONAL_APPROVED", "REJECTED")
        },
    }


def run_with_terminal_lock(
    *,
    v6_payload_path: str | Path,
    output_dir: str | Path,
    source_run_id: str | None,
    notify: bool,
    previous_state_path: str | Path | None,
    force_notify: bool,
    allow_all_unavailable: bool,
    **policy: float,
) -> dict[str, Any]:
    """Run timing while freezing prior per-symbol terminal decisions.

    When no valid locks exist this delegates directly to the existing timing
    runner. With locks, only non-terminal symbols are sent through live quote
    retrieval/timing classification; the locked decisions are merged back before
    signature calculation, reporting, persistence and notification.
    """
    evaluated_at = datetime.now(NY)
    previous = _read_previous(previous_state_path)
    locks = terminal_locks(
        previous,
        source_run_id=source_run_id,
        evaluated_at=evaluated_at,
        force_recompute=force_notify,
    )
    if not locks:
        return run_timing(
            v6_payload_path=v6_payload_path,
            output_dir=output_dir,
            source_run_id=source_run_id,
            notify=notify,
            previous_state_path=previous_state_path,
            force_notify=force_notify,
            allow_all_unavailable=allow_all_unavailable,
            **policy,
        )

    locked_symbols = set(locks)
    filtered, original_order = _filtered_payload(
        v6_payload_path,
        locked_symbols=locked_symbols,
    )
    unlocked_packets = (filtered.get("final_decisions") or {}).get("packets") or []
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    fresh_result: dict[str, Any] | None = None
    fresh_decisions: dict[str, dict[str, Any]] = {}
    if unlocked_packets:
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".json",
                prefix="us_open_unlocked_",
                dir=output,
                delete=False,
            ) as handle:
                json.dump(filtered, handle, ensure_ascii=False)
                tmp_path = Path(handle.name)
            fresh_result = run_timing(
                v6_payload_path=tmp_path,
                output_dir=output,
                source_run_id=source_run_id,
                notify=False,
                previous_state_path=None,
                force_notify=False,
                allow_all_unavailable=allow_all_unavailable,
                **policy,
            )
            fresh_payload = json.loads(
                (output / "us_open_confirmation_latest.json").read_text(encoding="utf-8")
            )
            for raw in fresh_payload.get("decisions") or []:
                if not isinstance(raw, Mapping):
                    continue
                symbol = str(raw.get("symbol") or "").strip().upper()
                if symbol:
                    fresh_decisions[symbol] = dict(raw)
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

    merged: list[OpenTimingDecision] = []
    for symbol in original_order:
        if symbol in locks:
            merged.append(_decision_from_payload(locks[symbol], locked=True))
        elif symbol in fresh_decisions:
            merged.append(_decision_from_payload(fresh_decisions[symbol], locked=False))
    if not merged:
        raise RuntimeError("terminal-lock merge produced no US-open decisions")

    follow = any(not item.terminal for item in merged)
    signature = _signature(merged)
    send_now = notify and _should_notify(
        previous,
        signature=signature,
        generated_at=evaluated_at,
        force=force_notify,
    )
    report_path = output / "us_open_confirmation_latest.md"
    json_path = output / "us_open_confirmation_latest.json"
    report_text = render_markdown(
        merged,
        generated_at=evaluated_at,
        source_run_id=source_run_id,
    )
    report_text += "\n\n## 本轮终态锁\n\n"
    report_text += (
        "- 已锁定：" + ", ".join(sorted(locked_symbols)) + "。这些标的本轮未重新拉取实时行情或重新分类。\n"
    )
    report_path.write_text(report_text, encoding="utf-8")

    summary = _summary(merged, locked_symbols)
    decision_payloads: list[dict[str, Any]] = []
    previous_generated_at = str((previous or {}).get("generated_at") or "") or None
    for item in merged:
        row = _decision_payload(item)
        if item.symbol in locked_symbols:
            row["terminal_locked"] = True
            row["terminal_locked_from"] = previous_generated_at
        decision_payloads.append(row)

    payload = {
        "version": POLICY_VERSION,
        "policy_version": POLICY_VERSION,
        "better_entry_metric": {
            "field": "better_entry_score",
            "legacy_alias": "better_entry_probability",
            "semantics": "heuristic_score",
            "calibrated": False,
        },
        "generated_at": evaluated_at.isoformat(),
        "source_run_id": source_run_id,
        "state_signature": signature,
        "follow_up_needed": follow,
        "summary": summary,
        "terminal_lock": {
            "applied": True,
            "symbols": sorted(locked_symbols),
            "source_generated_at": previous_generated_at,
        },
        "decisions": decision_payloads,
        "notification": {
            "requested": bool(notify),
            "suppressed_unchanged": bool(notify and not send_now),
        },
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    sent = (
        _notify(report_path, merged, evaluated_at.strftime("%Y-%m-%d"))
        if send_now
        else False
    )
    if send_now and not sent:
        raise RuntimeError("open timing notification failed")

    logger.info(
        "per-symbol terminal lock applied: locked=%s recalculated=%s",
        sorted(locked_symbols),
        sorted(fresh_decisions),
    )
    return {
        "policy_version": POLICY_VERSION,
        "report": str(report_path),
        "json": str(json_path),
        "symbols": len(merged),
        "live_success": int((fresh_result or {}).get("live_success") or 0),
        "buy_now": summary["buy_now"],
        "wait_better_entry": summary["wait_better_entry"],
        "wait_confirmation": summary["wait_confirmation"],
        "no_buy": summary["no_buy"],
        "data_unavailable": summary["data_unavailable"],
        "terminal_locked": len(locked_symbols),
        "follow_up_needed": follow,
        "state_signature": signature,
        "notified": sent,
        "notification_suppressed": bool(notify and not send_now),
    }
