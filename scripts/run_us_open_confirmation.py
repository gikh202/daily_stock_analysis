from __future__ import annotations

import argparse
import json
import logging
import math
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo


logger = logging.getLogger("us_open_confirmation")
NY = ZoneInfo("America/New_York")

BUYABLE_PRIOR_VERDICTS = {"buy_by_plan", "conditional_buy", "watch"}
BLOCKED_PRIOR_VERDICTS = {"avoid", "wait", "data_incomplete"}
STATUS_LABELS = {
    "BUY_NOW": "可以买（首仓）",
    "WAIT_ENTRY": "等进入计划区间",
    "WAIT_PULLBACK": "不追，等回踩",
    "WAIT_STABILIZE": "先不买，等盘中止跌",
    "NO_BUY": "今天不买",
    "INVALIDATED": "不买，计划失效",
    "DATA_UNAVAILABLE": "行情不足，暂不下单",
}


@dataclass(frozen=True)
class LiveSnapshot:
    symbol: str
    current_price: float
    session_open: float
    session_high: float
    session_low: float
    opening_15m_high: float
    opening_15m_low: float
    return_from_open_pct: float
    opening_15m_volume: float
    recent_opening_volume_median: float | None
    volume_ratio: float | None
    bar_count: int
    last_bar_time: str


@dataclass(frozen=True)
class ConfirmationDecision:
    symbol: str
    status: str
    label: str
    reason: str
    current_price: float | None
    entry_low: float | None
    entry_high: float | None
    stop_loss: float | None
    targets: tuple[float, ...]
    starter_position_pct: float
    max_position_pct: float
    return_from_open_pct: float | None
    volume_ratio: float | None
    prior_verdict: str
    prior_worth_buying: bool | None
    prior_execution_authorized: bool
    prior_confirmations: tuple[str, ...]
    source_trade_date: str | None
    source_last_bar_time: str | None


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, (list, tuple)) else ()


def _float_pair(value: Any) -> tuple[float, float] | None:
    values = _sequence(value)
    if len(values) != 2:
        return None
    low = _finite(values[0])
    high = _finite(values[1])
    if low is None or high is None or low <= 0 or high <= 0 or low > high:
        return None
    return low, high


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_final_packets(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    final_decisions = _mapping(payload.get("final_decisions"))
    packets = [
        dict(item)
        for item in _sequence(final_decisions.get("packets"))
        if isinstance(item, Mapping)
    ]
    if not packets:
        raise RuntimeError(f"no final decision packets in {path}")
    return packets


def _opening_window(frame: Any, session_date: Any) -> Any:
    session = frame[frame.index.date == session_date]
    if session.empty:
        return session
    # 固定使用开盘前 15 分钟窗口；补偿重试即使晚于 09:45，也不会把后续行情
    # 混进“开盘 15 分钟确认”指标。当前价仍使用最新可用 regular-session bar。
    return session.between_time("09:30", "09:44")


def fetch_live_snapshot(symbol: str, now: datetime | None = None) -> LiveSnapshot:
    try:
        import yfinance as yf
    except Exception as exc:
        raise RuntimeError(f"yfinance unavailable: {exc}") from exc

    now_ny = (now or datetime.now(NY)).astimezone(NY)
    ticker = yf.Ticker(symbol)
    frame = ticker.history(
        period="5d",
        interval="1m",
        auto_adjust=False,
        prepost=False,
        actions=False,
    )
    if frame is None or frame.empty:
        raise RuntimeError(f"{symbol}: no 1m bars")

    index = frame.index
    if getattr(index, "tz", None) is None:
        frame.index = index.tz_localize("UTC").tz_convert(NY)
    else:
        frame.index = index.tz_convert(NY)

    today = now_ny.date()
    session = frame[frame.index.date == today]
    if session.empty:
        raise RuntimeError(f"{symbol}: no regular-session bars for {today}")

    opening = _opening_window(frame, today)
    if len(opening) < 5:
        raise RuntimeError(f"{symbol}: opening window incomplete ({len(opening)} bars)")

    last = session.iloc[-1]
    first = session.iloc[0]
    current_price = _finite(last.get("Close"))
    session_open = _finite(first.get("Open"))
    if current_price is None or session_open is None or current_price <= 0 or session_open <= 0:
        raise RuntimeError(f"{symbol}: invalid current/open price")

    prior_volumes: list[float] = []
    prior_dates = sorted({item for item in frame.index.date if item < today}, reverse=True)
    for prior_date in prior_dates[:4]:
        prior_opening = _opening_window(frame, prior_date)
        if len(prior_opening) < 10:
            continue
        volume = _finite(prior_opening["Volume"].fillna(0).sum())
        if volume is not None and volume > 0:
            prior_volumes.append(volume)

    current_opening_volume = float(opening["Volume"].fillna(0).sum())
    prior_median = median(prior_volumes) if prior_volumes else None
    volume_ratio = (
        current_opening_volume / prior_median
        if prior_median is not None and prior_median > 0
        else None
    )

    return LiveSnapshot(
        symbol=symbol,
        current_price=current_price,
        session_open=session_open,
        session_high=float(session["High"].max()),
        session_low=float(session["Low"].min()),
        opening_15m_high=float(opening["High"].max()),
        opening_15m_low=float(opening["Low"].min()),
        return_from_open_pct=(current_price / session_open - 1.0) * 100.0,
        opening_15m_volume=current_opening_volume,
        recent_opening_volume_median=prior_median,
        volume_ratio=volume_ratio,
        bar_count=int(len(session)),
        last_bar_time=session.index[-1].isoformat(),
    )


def classify_confirmation(
    packet: Mapping[str, Any],
    snapshot: LiveSnapshot | None,
    *,
    chase_tolerance_pct: float = 0.5,
    weak_open_pct: float = -0.75,
    min_volume_ratio: float = 0.55,
    starter_position_pct: float = 10.0,
    data_error: str | None = None,
) -> ConfirmationDecision:
    identity = _mapping(packet.get("identity"))
    assessment = _mapping(packet.get("assessment"))
    execution = _mapping(packet.get("execution"))

    symbol = str(identity.get("symbol") or "").strip().upper()
    prior_verdict = str(assessment.get("verdict") or "").strip().lower()
    worth_buying_raw = assessment.get("worth_buying")
    prior_worth_buying = worth_buying_raw if isinstance(worth_buying_raw, bool) else None
    prior_execution_authorized = bool(assessment.get("execution_authorized"))

    entry = _float_pair(execution.get("entry_zone"))
    stop = _finite(execution.get("stop_loss"))
    targets = tuple(
        value
        for value in (_finite(item) for item in _sequence(execution.get("targets")))
        if value is not None and value > 0
    )
    plan_max_fraction = max(0.0, _finite(execution.get("max_position_pct")) or 0.0)
    plan_max_pct = plan_max_fraction * 100.0
    confirmations = tuple(
        str(item).strip()
        for item in _sequence(execution.get("confirmations"))
        if str(item).strip()
    )
    has_active_plan = bool(
        execution.get("has_active_plan")
        if isinstance(execution.get("has_active_plan"), bool)
        else entry and stop is not None and targets and plan_max_fraction > 0
    )
    source_trade_date = str(identity.get("effective_trade_date") or "").strip() or None

    base = dict(
        symbol=symbol,
        entry_low=entry[0] if entry else None,
        entry_high=entry[1] if entry else None,
        stop_loss=stop,
        targets=targets,
        starter_position_pct=0.0,
        max_position_pct=plan_max_pct,
        prior_verdict=prior_verdict,
        prior_worth_buying=prior_worth_buying,
        prior_execution_authorized=prior_execution_authorized,
        prior_confirmations=confirmations,
        source_trade_date=source_trade_date,
    )

    if snapshot is None:
        return ConfirmationDecision(
            status="DATA_UNAVAILABLE",
            label=STATUS_LABELS["DATA_UNAVAILABLE"],
            reason=data_error or "盘中实时行情不足，拒绝在无数据情况下给买入授权。",
            current_price=None,
            return_from_open_pct=None,
            volume_ratio=None,
            source_last_bar_time=None,
            **base,
        )

    live = dict(
        current_price=snapshot.current_price,
        return_from_open_pct=snapshot.return_from_open_pct,
        volume_ratio=snapshot.volume_ratio,
        source_last_bar_time=snapshot.last_bar_time,
    )

    if prior_verdict == "avoid" or prior_worth_buying is False:
        return ConfirmationDecision(
            status="NO_BUY",
            label=STATUS_LABELS["NO_BUY"],
            reason="昨晚最终决策未授权新仓；盘中确认器不会绕过收盘风险结论。",
            **live,
            **base,
        )

    if prior_verdict in {"wait", "data_incomplete"}:
        return ConfirmationDecision(
            status="NO_BUY",
            label=STATUS_LABELS["NO_BUY"],
            reason="昨晚计划仍处于等待/数据不足状态，没有可执行的新仓计划。",
            **live,
            **base,
        )

    if prior_verdict not in BUYABLE_PRIOR_VERDICTS or prior_worth_buying is not True:
        return ConfirmationDecision(
            status="NO_BUY",
            label=STATUS_LABELS["NO_BUY"],
            reason="昨晚最终决策没有明确认定当前标的值得买入，今天不主动建立新仓。",
            **live,
            **base,
        )

    if not has_active_plan or entry is None or stop is None or not targets or plan_max_pct <= 0:
        return ConfirmationDecision(
            status="NO_BUY",
            label=STATUS_LABELS["NO_BUY"],
            reason="虽然逻辑偏多，但缺少完整入场区间、止损、目标或仓位上限，禁止临盘补造计划。",
            **live,
            **base,
        )

    price = snapshot.current_price
    entry_low, entry_high = entry
    if price <= stop:
        return ConfirmationDecision(
            status="INVALIDATED",
            label=STATUS_LABELS["INVALIDATED"],
            reason=f"现价已触及/跌破昨晚止损 ${stop:.2f}，原买入计划失效。",
            **live,
            **base,
        )

    if price < entry_low:
        return ConfirmationDecision(
            status="WAIT_ENTRY",
            label=STATUS_LABELS["WAIT_ENTRY"],
            reason=(
                f"现价低于计划入场下沿 ${entry_low:.2f}；不在下跌过程中抢跑，"
                "等待价格重新进入计划区间并企稳。"
            ),
            **live,
            **base,
        )

    chase_limit = entry_high * (1.0 + max(0.0, chase_tolerance_pct) / 100.0)
    if price > chase_limit:
        return ConfirmationDecision(
            status="WAIT_PULLBACK",
            label=STATUS_LABELS["WAIT_PULLBACK"],
            reason=(
                f"现价已高于计划上沿 ${entry_high:.2f} 超过允许追价幅度 "
                f"{chase_tolerance_pct:.2f}%，今天不追高。"
            ),
            **live,
            **base,
        )

    weak_price = snapshot.return_from_open_pct <= weak_open_pct
    weak_volume = (
        snapshot.volume_ratio is not None
        and snapshot.volume_ratio < min_volume_ratio
        and snapshot.return_from_open_pct <= 0
    )
    if weak_price or weak_volume:
        weakness = []
        if weak_price:
            weakness.append(f"较开盘 {snapshot.return_from_open_pct:+.2f}%")
        if weak_volume:
            weakness.append(f"开盘15分钟量比 {snapshot.volume_ratio:.2f}x")
        return ConfirmationDecision(
            status="WAIT_STABILIZE",
            label=STATUS_LABELS["WAIT_STABILIZE"],
            reason="；".join(weakness) + "，盘中确认偏弱，先等止跌/重新转强，不急着接。",
            **live,
            **base,
        )

    starter_pct = min(max(0.0, starter_position_pct), plan_max_pct)
    return ConfirmationDecision(
        status="BUY_NOW",
        label=STATUS_LABELS["BUY_NOW"],
        reason=(
            "现价位于昨晚计划允许范围内，止损未失效，开盘15分钟未出现明显走弱；"
            "允许执行第一笔仓位，但不得超过计划总仓位上限。"
        ),
        starter_position_pct=starter_pct,
        **live,
        **{key: value for key, value in base.items() if key != "starter_position_pct"},
    )


def _money(value: float | None) -> str:
    return "N/A" if value is None else f"${value:.2f}"


def _pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value:+.2f}%"


def _ratio(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2f}x"


def render_markdown(
    decisions: Sequence[ConfirmationDecision],
    *,
    generated_at: datetime,
    source_run_id: str | None = None,
) -> str:
    now_ny = generated_at.astimezone(NY)
    buy_count = sum(item.status == "BUY_NOW" for item in decisions)
    wait_count = sum(item.status.startswith("WAIT_") for item in decisions)
    no_buy_count = sum(item.status in {"NO_BUY", "INVALIDATED"} for item in decisions)
    unavailable_count = sum(item.status == "DATA_UNAVAILABLE" for item in decisions)

    lines = [
        f"# 美股开盘执行确认 · {now_ny.strftime('%Y-%m-%d %H:%M ET')}",
        "",
        "> 这封邮件只回答“现在买不买”。它复用上一交易日收盘计划，不重新生成一篇重复日报。",
        "",
        "## 一眼结论",
        "",
        f"- **可以买**：{buy_count} 只",
        f"- **等待条件**：{wait_count} 只",
        f"- **今天不买/计划失效**：{no_buy_count} 只",
        f"- **行情不足**：{unavailable_count} 只",
    ]
    if source_run_id:
        lines.append(f"- **收盘计划来源**：V6 run `{source_run_id}`")

    lines.extend(
        [
            "",
            "| 标的 | 现在怎么做 | 当前价 | 昨晚入场区间 | 开盘后涨跌 | 首仓上限 |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for item in decisions:
        entry = (
            f"${item.entry_low:.2f}–${item.entry_high:.2f}"
            if item.entry_low is not None and item.entry_high is not None
            else "N/A"
        )
        starter = f"{item.starter_position_pct:.1f}%" if item.starter_position_pct > 0 else "0%"
        lines.append(
            f"| {item.symbol} | **{item.label}** | {_money(item.current_price)} | "
            f"{entry} | {_pct(item.return_from_open_pct)} | {starter} |"
        )

    for index, item in enumerate(decisions, 1):
        lines.extend(
            [
                "",
                f"## {index}. {item.symbol} · {item.label}",
                "",
                f"- **结论**：{item.reason}",
                f"- **当前价**：{_money(item.current_price)}；较开盘：{_pct(item.return_from_open_pct)}；"
                f"开盘15分钟量比：{_ratio(item.volume_ratio)}",
            ]
        )
        if item.entry_low is not None and item.entry_high is not None:
            lines.append(
                f"- **昨晚计划入场**：${item.entry_low:.2f}–${item.entry_high:.2f}"
            )
        if item.status == "BUY_NOW":
            lines.append(
                f"- **现在执行**：允许建立第一笔仓位，**首仓不超过 {item.starter_position_pct:.1f}%**；"
                f"计划总仓位上限 {item.max_position_pct:.1f}%"
            )
        elif item.status == "WAIT_ENTRY" and item.entry_low is not None:
            lines.append(f"- **触发条件**：重新站回 ${item.entry_low:.2f} 以上并保持不明显走弱后再评估。")
        elif item.status == "WAIT_PULLBACK" and item.entry_high is not None:
            lines.append(f"- **触发条件**：回踩至 ${item.entry_high:.2f} 附近/以下再评估，不追高。")
        elif item.status == "WAIT_STABILIZE":
            lines.append("- **触发条件**：盘中跌势收敛并重新转强后再评估；未转强则今天不买。")
        elif item.status in {"NO_BUY", "INVALIDATED"}:
            lines.append("- **现在执行**：不建立新仓。")

        if item.stop_loss is not None:
            lines.append(f"- **止损/失效线**：${item.stop_loss:.2f}")
        if item.targets:
            lines.append("- **目标位**：" + " / ".join(f"${value:.2f}" for value in item.targets))
        if item.prior_confirmations:
            lines.append(
                "- **昨晚计划确认项**："
                + "；".join(item.prior_confirmations[:3])
                + "（作为辅助核对，不允许覆盖上述确定性风控）"
            )

    lines.extend(
        [
            "",
            "## 执行纪律",
            "",
            "- `可以买（首仓）` 才代表本轮允许新开第一笔仓位。",
            "- `等进入计划区间 / 不追，等回踩 / 先不买，等盘中止跌` 都代表**现在不下单**。",
            "- 一旦触及止损/失效线，原计划作废，不因为“昨晚看多”继续硬买。",
            "- 这是规则化交易确认，不保证收益；仓位上限和止损优先于方向判断。",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(
    decisions: Sequence[ConfirmationDecision],
    *,
    output_dir: str | Path,
    generated_at: datetime,
    source_run_id: str | None,
) -> tuple[Path, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    markdown = render_markdown(decisions, generated_at=generated_at, source_run_id=source_run_id)
    md_path = output / "us_open_confirmation_latest.md"
    json_path = output / "us_open_confirmation_latest.json"
    md_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "version": "us-open-confirmation-v1",
                "generated_at": generated_at.astimezone(NY).isoformat(),
                "source_run_id": source_run_id,
                "summary": {
                    "symbols": len(decisions),
                    "buy_now": sum(item.status == "BUY_NOW" for item in decisions),
                    "waiting": sum(item.status.startswith("WAIT_") for item in decisions),
                    "no_buy": sum(item.status in {"NO_BUY", "INVALIDATED"} for item in decisions),
                    "data_unavailable": sum(item.status == "DATA_UNAVAILABLE" for item in decisions),
                },
                "decisions": [asdict(item) for item in decisions],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return md_path, json_path


def notify_report(report_path: Path, decisions: Sequence[ConfirmationDecision], session_date: str) -> bool:
    from src.notification import NotificationService

    service = NotificationService()
    if not service.is_available():
        logger.error("no notification channel configured")
        return False
    codes = [item.symbol for item in decisions if item.symbol]
    return bool(
        service.send(
            report_path.read_text(encoding="utf-8"),
            email_stock_codes=codes,
            email_send_to_all=True,
            route_type="report",
            severity="info",
            dedup_key=f"us-open-confirmation-{session_date}",
        )
    )


def run(
    *,
    v6_payload_path: str | Path,
    output_dir: str | Path,
    notify: bool = False,
    source_run_id: str | None = None,
    now: datetime | None = None,
    chase_tolerance_pct: float = 0.5,
    weak_open_pct: float = -0.75,
    min_volume_ratio: float = 0.55,
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
            decision = classify_confirmation(
                packet,
                snapshot,
                chase_tolerance_pct=chase_tolerance_pct,
                weak_open_pct=weak_open_pct,
                min_volume_ratio=min_volume_ratio,
                starter_position_pct=starter_position_pct,
            )
        except Exception as exc:
            logger.warning("%s live confirmation unavailable: %s", symbol, exc)
            decision = classify_confirmation(
                packet,
                None,
                data_error=f"{type(exc).__name__}: {exc}",
                chase_tolerance_pct=chase_tolerance_pct,
                weak_open_pct=weak_open_pct,
                min_volume_ratio=min_volume_ratio,
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
    sent = notify_report(report_path, decisions, generated_at.strftime("%Y-%m-%d")) if notify else False
    if notify and not sent:
        raise RuntimeError("open confirmation notification failed")

    return {
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
    parser = argparse.ArgumentParser(description="Actionable U.S. open +15m execution confirmation")
    parser.add_argument("--v6-payload", required=True)
    parser.add_argument("--output-dir", default="open_confirmation_reports")
    parser.add_argument("--source-run-id", default=os.getenv("OPEN_CONFIRMATION_SOURCE_RUN_ID"))
    parser.add_argument("--notify", action="store_true")
    parser.add_argument(
        "--chase-tolerance-pct",
        type=float,
        default=float(os.getenv("OPEN_CONFIRMATION_CHASE_TOLERANCE_PCT", "0.5")),
    )
    parser.add_argument(
        "--weak-open-pct",
        type=float,
        default=float(os.getenv("OPEN_CONFIRMATION_WEAK_OPEN_PCT", "-0.75")),
    )
    parser.add_argument(
        "--min-volume-ratio",
        type=float,
        default=float(os.getenv("OPEN_CONFIRMATION_MIN_VOLUME_RATIO", "0.55")),
    )
    parser.add_argument(
        "--starter-position-pct",
        type=float,
        default=float(os.getenv("OPEN_CONFIRMATION_STARTER_POSITION_PCT", "10")),
    )
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
        chase_tolerance_pct=args.chase_tolerance_pct,
        weak_open_pct=args.weak_open_pct,
        min_volume_ratio=args.min_volume_ratio,
        starter_position_pct=args.starter_position_pct,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
