from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from scripts.run_us_open_confirmation import (
    ConfirmationDecision,
    LiveSnapshot,
    STATUS_LABELS,
    fetch_live_snapshot,
    load_final_packets,
    notify_report,
    write_outputs,
)
from scripts.run_us_open_confirmation import classify_confirmation as classify_v1


logger = logging.getLogger("us_open_confirmation_v2")
NY = ZoneInfo("America/New_York")
POLICY_VERSION = "us-open-confirmation-v2-runtime"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _parse_bar_time(value: str | None) -> datetime | None:
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


def _runtime_snapshot(snapshot: LiveSnapshot) -> LiveSnapshot:
    """Use only evidence that is comparable at the actual workflow runtime.

    Before the first 15 regular-session bars are complete, the stored volume
    ratio compares a partial current opening window with complete historical
    15-minute windows. Treat that ratio as unavailable instead of letting the
    scheduler's exact start minute create a false weak-volume signal. Price,
    opening range, stops and quote freshness still use the latest available bar.
    """
    if snapshot.bar_count >= 15 or snapshot.volume_ratio is None:
        return snapshot
    return replace(snapshot, volume_ratio=None)


def _opening_range_position(snapshot: LiveSnapshot) -> float:
    width = snapshot.opening_15m_high - snapshot.opening_15m_low
    if width <= 1e-12:
        return 0.5
    return (snapshot.current_price - snapshot.opening_15m_low) / width


def _entry_high(packet: Mapping[str, Any]) -> float | None:
    execution = _mapping(packet.get("execution"))
    entry = execution.get("entry_zone")
    if not isinstance(entry, (list, tuple)) or len(entry) != 2:
        return None
    try:
        value = float(entry[1])
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _with_status(
    decision: ConfirmationDecision,
    *,
    status: str,
    label: str,
    reason: str,
    starter_position_pct: float = 0.0,
) -> ConfirmationDecision:
    return replace(
        decision,
        status=status,
        label=label,
        reason=reason,
        starter_position_pct=starter_position_pct,
    )


def classify_confirmation_v2(
    packet: Mapping[str, Any],
    snapshot: LiveSnapshot | None,
    *,
    evaluated_at: datetime,
    normal_chase_tolerance_pct: float = 0.5,
    momentum_chase_tolerance_pct: float = 0.75,
    weak_open_pct: float = -0.5,
    min_volume_ratio: float = 0.70,
    min_opening_range_position: float = 0.25,
    momentum_min_opening_range_position: float = 0.50,
    momentum_min_volume_ratio: float = 0.70,
    max_quote_age_minutes: float = 8.0,
    max_plan_age_days: int = 4,
    starter_position_pct: float = 10.0,
    data_error: str | None = None,
) -> ConfirmationDecision:
    """V2 intraday policy evaluated at the workflow's actual runtime.

    The prior close packet remains the authority for whether a symbol is buyable
    and for its entry/stop/targets. V2 only decides whether the latest verified
    tape at the actual execution time confirms action now; it never invents a new
    trade plan and it no longer assumes a fixed 09:45/10:15 confirmation clock.
    """
    evaluated = evaluated_at.astimezone(NY)
    runtime_snapshot = _runtime_snapshot(snapshot) if snapshot is not None else None
    base = classify_v1(
        packet,
        runtime_snapshot,
        chase_tolerance_pct=normal_chase_tolerance_pct,
        weak_open_pct=weak_open_pct,
        min_volume_ratio=min_volume_ratio,
        starter_position_pct=starter_position_pct,
        data_error=data_error,
    )
    if runtime_snapshot is None:
        return base

    last_bar = _parse_bar_time(runtime_snapshot.last_bar_time)
    if last_bar is None:
        return _with_status(
            base,
            status="DATA_UNAVAILABLE",
            label=STATUS_LABELS["DATA_UNAVAILABLE"],
            reason="实时行情缺少可验证的最新时间戳，拒绝给出买入授权。",
        )

    quote_age_minutes = (evaluated - last_bar).total_seconds() / 60.0
    if quote_age_minutes < -2.0 or quote_age_minutes > max_quote_age_minutes:
        return _with_status(
            base,
            status="DATA_UNAVAILABLE",
            label=STATUS_LABELS["DATA_UNAVAILABLE"],
            reason=(
                f"最新行情距当前确认时刻 {quote_age_minutes:.1f} 分钟，超过允许的 "
                f"{max_quote_age_minutes:.1f} 分钟；拒绝用陈旧/异常行情下单。"
            ),
        )

    source_date_text = str(base.source_trade_date or "").strip()
    if source_date_text:
        try:
            source_date = date.fromisoformat(source_date_text[:10])
        except ValueError:
            source_date = None
        if source_date is None:
            return _with_status(
                base,
                status="NO_BUY",
                label=STATUS_LABELS["NO_BUY"],
                reason="上一收盘计划缺少有效交易日期，无法证明计划时效，今天不建立新仓。",
            )
        plan_age = (last_bar.date() - source_date).days
        if plan_age <= 0 or plan_age > max_plan_age_days:
            return _with_status(
                base,
                status="NO_BUY",
                label=STATUS_LABELS["NO_BUY"],
                reason=(
                    f"上一计划日期 {source_date.isoformat()} 与当前交易日 {last_bar.date().isoformat()} "
                    f"相差 {plan_age} 天，不满足 1–{max_plan_age_days} 天的前收盘计划时效要求。"
                ),
            )

    # Final-fusion WAIT/AVOID/data-incomplete and incomplete-plan states from V1
    # remain hard blockers. Only executable tape states continue below.
    if base.status not in {"BUY_NOW", "WAIT_PULLBACK"}:
        return base

    range_position = _opening_range_position(runtime_snapshot)
    if base.status == "BUY_NOW" and range_position < min_opening_range_position:
        return _with_status(
            base,
            status="WAIT_STABILIZE",
            label=STATUS_LABELS["WAIT_STABILIZE"],
            reason=(
                f"当前价格仅位于开盘确认区间的 {range_position * 100:.0f}% 位置，"
                f"低于 {min_opening_range_position * 100:.0f}% 确认线；先等价格重新转强。"
            ),
        )

    candidate = base
    if base.status == "BUY_NOW":
        candidate = _with_status(
            base,
            status="BUY_NOW",
            label=STATUS_LABELS["BUY_NOW"],
            starter_position_pct=base.starter_position_pct,
            reason=(
                f"截至 {evaluated.strftime('%H:%M')} ET，现价位于昨晚计划允许范围内，"
                "止损未失效且当前盘中价格未出现硬性走弱信号；允许执行第一笔仓位，"
                "但不得超过昨晚计划总仓位上限。"
            ),
        )
    elif base.status == "WAIT_PULLBACK":
        entry_high = _entry_high(packet)
        momentum_limit = (
            entry_high * (1.0 + max(0.0, momentum_chase_tolerance_pct) / 100.0)
            if entry_high is not None
            else None
        )
        momentum_ok = bool(
            momentum_limit is not None
            and runtime_snapshot.current_price <= momentum_limit
            and runtime_snapshot.return_from_open_pct > 0.0
            and range_position >= momentum_min_opening_range_position
            and runtime_snapshot.volume_ratio is not None
            and runtime_snapshot.volume_ratio >= momentum_min_volume_ratio
        )
        if not momentum_ok:
            return base
        starter = min(max(0.0, starter_position_pct), base.max_position_pct)
        candidate = _with_status(
            base,
            status="BUY_NOW",
            label=STATUS_LABELS["BUY_NOW"],
            starter_position_pct=starter,
            reason=(
                f"现价高于常规追价上限，但仍在 {momentum_chase_tolerance_pct:.2f}% 动量扩展内；"
                f"较开盘 {runtime_snapshot.return_from_open_pct:+.2f}%、位于开盘确认区间 "
                f"{range_position * 100:.0f}% 位置、量比 {runtime_snapshot.volume_ratio:.2f}x，"
                "满足严格动量例外，允许小仓首笔。"
            ),
        )

    return candidate


def run(
    *,
    v6_payload_path: str | Path,
    output_dir: str | Path,
    notify: bool = False,
    source_run_id: str | None = None,
    now: datetime | None = None,
    normal_chase_tolerance_pct: float = 0.5,
    momentum_chase_tolerance_pct: float = 0.75,
    weak_open_pct: float = -0.5,
    min_volume_ratio: float = 0.70,
    min_opening_range_position: float = 0.25,
    momentum_min_opening_range_position: float = 0.50,
    momentum_min_volume_ratio: float = 0.70,
    max_quote_age_minutes: float = 8.0,
    starter_position_pct: float = 10.0,
) -> dict[str, Any]:
    generated_at = (now or datetime.now(NY)).astimezone(NY)
    packets = load_final_packets(v6_payload_path)
    decisions: list[ConfirmationDecision] = []
    live_success = 0

    for packet in packets:
        identity = _mapping(packet.get("identity"))
        symbol = str(identity.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        try:
            snapshot = fetch_live_snapshot(symbol, now=generated_at)
            live_success += 1
            decision = classify_confirmation_v2(
                packet,
                snapshot,
                evaluated_at=generated_at,
                normal_chase_tolerance_pct=normal_chase_tolerance_pct,
                momentum_chase_tolerance_pct=momentum_chase_tolerance_pct,
                weak_open_pct=weak_open_pct,
                min_volume_ratio=min_volume_ratio,
                min_opening_range_position=min_opening_range_position,
                momentum_min_opening_range_position=momentum_min_opening_range_position,
                momentum_min_volume_ratio=momentum_min_volume_ratio,
                max_quote_age_minutes=max_quote_age_minutes,
                starter_position_pct=starter_position_pct,
            )
        except Exception as exc:
            logger.warning("%s live confirmation unavailable: %s", symbol, exc)
            decision = classify_confirmation_v2(
                packet,
                None,
                evaluated_at=generated_at,
                data_error=f"{type(exc).__name__}: {exc}",
                normal_chase_tolerance_pct=normal_chase_tolerance_pct,
                momentum_chase_tolerance_pct=momentum_chase_tolerance_pct,
                weak_open_pct=weak_open_pct,
                min_volume_ratio=min_volume_ratio,
                min_opening_range_position=min_opening_range_position,
                momentum_min_opening_range_position=momentum_min_opening_range_position,
                momentum_min_volume_ratio=momentum_min_volume_ratio,
                max_quote_age_minutes=max_quote_age_minutes,
                starter_position_pct=starter_position_pct,
            )
        decisions.append(decision)

    if not decisions:
        raise RuntimeError("no symbols available in prior final decision payload")
    if live_success == 0:
        raise RuntimeError("all live U.S. session quotes unavailable; refuse to send a false confirmation")

    report_path, json_path = write_outputs(
        decisions,
        output_dir=output_dir,
        generated_at=generated_at,
        source_run_id=source_run_id,
    )
    # Add policy metadata without changing the stable v1 decision payload shape.
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    payload["policy_version"] = POLICY_VERSION
    payload["policy"] = {
        "normal_chase_tolerance_pct": normal_chase_tolerance_pct,
        "momentum_chase_tolerance_pct": momentum_chase_tolerance_pct,
        "weak_open_pct": weak_open_pct,
        "min_volume_ratio": min_volume_ratio,
        "min_opening_range_position": min_opening_range_position,
        "momentum_min_opening_range_position": momentum_min_opening_range_position,
        "momentum_min_volume_ratio": momentum_min_volume_ratio,
        "max_quote_age_minutes": max_quote_age_minutes,
        "evaluation_clock": "actual_runtime_et",
        "early_partial_volume_ratio": "disabled_until_15_regular_session_bars",
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    sent = notify_report(report_path, decisions, generated_at.strftime("%Y-%m-%d")) if notify else False
    if notify and not sent:
        raise RuntimeError("open confirmation notification failed")

    return {
        "policy_version": POLICY_VERSION,
        "report": str(report_path),
        "json": str(json_path),
        "symbols": len(decisions),
        "live_success": live_success,
        "buy_now": sum(item.status == "BUY_NOW" for item in decisions),
        "waiting": sum(item.status.startswith("WAIT_") for item in decisions),
        "no_buy": sum(item.status in {"NO_BUY", "INVALIDATED"} for item in decisions),
        "notified": sent,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Guarded U.S. open runtime execution confirmation v2")
    parser.add_argument("--v6-payload", required=True)
    parser.add_argument("--output-dir", default="open_confirmation_reports")
    parser.add_argument("--source-run-id", default=os.getenv("OPEN_CONFIRMATION_SOURCE_RUN_ID"))
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--normal-chase-pct", type=float, default=float(os.getenv("OPEN_CONFIRMATION_CHASE_TOLERANCE_PCT", "0.5")))
    parser.add_argument("--momentum-chase-pct", type=float, default=float(os.getenv("OPEN_CONFIRMATION_MOMENTUM_CHASE_PCT", "0.75")))
    parser.add_argument("--weak-open-pct", type=float, default=float(os.getenv("OPEN_CONFIRMATION_WEAK_OPEN_PCT", "-0.5")))
    parser.add_argument("--min-volume-ratio", type=float, default=float(os.getenv("OPEN_CONFIRMATION_MIN_VOLUME_RATIO", "0.70")))
    parser.add_argument("--min-opening-range-position", type=float, default=float(os.getenv("OPEN_CONFIRMATION_MIN_OPENING_RANGE_POSITION", "0.25")))
    parser.add_argument("--momentum-min-range-position", type=float, default=float(os.getenv("OPEN_CONFIRMATION_MOMENTUM_MIN_RANGE_POSITION", "0.50")))
    parser.add_argument("--momentum-min-volume-ratio", type=float, default=float(os.getenv("OPEN_CONFIRMATION_MOMENTUM_MIN_VOLUME_RATIO", "0.70")))
    parser.add_argument("--max-quote-age-minutes", type=float, default=float(os.getenv("OPEN_CONFIRMATION_MAX_QUOTE_AGE_MINUTES", "8")))
    parser.add_argument("--starter-position-pct", type=float, default=float(os.getenv("OPEN_CONFIRMATION_STARTER_POSITION_PCT", "10")))
    args = parser.parse_args()

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    result = run(
        v6_payload_path=args.v6_payload,
        output_dir=args.output_dir,
        notify=args.notify,
        source_run_id=args.source_run_id,
        normal_chase_tolerance_pct=args.normal_chase_pct,
        momentum_chase_tolerance_pct=args.momentum_chase_pct,
        weak_open_pct=args.weak_open_pct,
        min_volume_ratio=args.min_volume_ratio,
        min_opening_range_position=args.min_opening_range_position,
        momentum_min_opening_range_position=args.momentum_min_range_position,
        momentum_min_volume_ratio=args.momentum_min_volume_ratio,
        max_quote_age_minutes=args.max_quote_age_minutes,
        starter_position_pct=args.starter_position_pct,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())