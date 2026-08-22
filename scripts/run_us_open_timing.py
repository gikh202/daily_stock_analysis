from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from statistics import pstdev
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_us_open_confirmation import ConfirmationDecision, LiveSnapshot, fetch_live_snapshot
from scripts.run_us_open_confirmation_v2 import classify_confirmation_v2
from src.forecasting import IntradayTimingModel

logger = logging.getLogger("us_open_timing")
NY = ZoneInfo("America/New_York")
POLICY_VERSION = "us-open-timing-v7.2"
ACTION_LABELS = {
    "BUY_NOW": "现在可以买（首仓）",
    "WAIT_BETTER_ENTRY": "等更好买点",
    "WAIT_CONFIRMATION": "等确认再买",
    "NO_BUY": "今天不买",
    "INVALIDATED": "计划失效，不买",
    "DATA_UNAVAILABLE": "行情不足，稍后再看",
}
EXECUTION_STATUS_LABELS = {
    "FULL_APPROVED": "完全批准",
    "CONDITIONAL_APPROVED": "条件批准",
    "REJECTED": "风险拒绝",
}


@dataclass(frozen=True)
class OpenTimingDecision:
    symbol: str
    action: str
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
    probability_up_1d: float | None
    probability_up_5d: float | None
    probability_up_20d: float | None
    expected_return_5d_pct: float | None
    expected_alpha_5d_pct: float | None
    forecast_confidence: float | None
    better_entry_score: float
    better_entry_probability: float
    expected_better_price: float | None
    expected_improvement_pct: float
    recheck_minutes: int
    terminal: bool
    source_trade_date: str | None
    source_last_bar_time: str | None
    execution_status: str = "REJECTED"
    conditional_entry_price: float | None = None
    conditional_entry_reason: str | None = None


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


def _execution_contract(packet: Mapping[str, Any]) -> dict[str, Any]:
    assessment = _mapping(packet.get("assessment"))
    raw_status = str(assessment.get("execution_status") or "").strip().upper()
    if raw_status not in EXECUTION_STATUS_LABELS:
        if bool(assessment.get("execution_authorized")):
            raw_status = "FULL_APPROVED"
        elif assessment.get("worth_buying") is True:
            raw_status = "CONDITIONAL_APPROVED"
        else:
            raw_status = "REJECTED"
    return {
        "status": raw_status,
        "conditional_entry_price": _finite(assessment.get("conditional_entry_price")),
        "conditional_entry_reason": str(
            assessment.get("conditional_entry_reason") or ""
        ).strip()
        or None,
    }


def _effective_timing_base(
    packet: Mapping[str, Any], base: ConfirmationDecision
) -> tuple[str, str]:
    """Preserve hard blockers while keeping conditionally approved WAIT non-terminal."""
    contract = _execution_contract(packet)
    if (
        contract["status"] == "CONDITIONAL_APPROVED"
        and base.status == "NO_BUY"
        and "缺少完整入场区间、止损、目标或仓位上限" in str(base.reason or "")
    ):
        return (
            "WAIT_STABILIZE",
            "上一收盘为条件批准，但尚未形成完整可执行的风险计划；继续等待确认，"
            "盘中不得临时补造入场区间、止损、目标或仓位。",
        )
    return base.status, base.reason


def _extract_forecast(packet: Mapping[str, Any]) -> dict[str, float | None]:
    intelligence = _mapping(packet.get("forecast_intelligence"))
    horizons = _mapping(intelligence.get("horizons")) or _mapping(
        packet.get("horizon_forecasts")
    )

    def bucket(name: str) -> Mapping[str, Any]:
        return _mapping(horizons.get(name))

    h1, h5, h20 = bucket("1d"), bucket("5d"), bucket("20d")
    return {
        "p1": _finite(h1.get("probability_up")),
        "p5": _finite(h5.get("probability_up")),
        "p20": _finite(h20.get("probability_up")),
        "return5": _finite(h5.get("expected_return_pct")),
        "alpha5": _finite(h5.get("expected_alpha_vs_spy_pct")),
        "confidence": _finite(h5.get("forecast_confidence")),
    }


def load_runtime_packets(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    final = _mapping(payload.get("final_decisions"))
    packets = [
        dict(item)
        for item in _sequence(final.get("packets"))
        if isinstance(item, Mapping)
    ]
    if not packets:
        raise RuntimeError(f"no final decision packets in {path}")
    board_by_code = {
        str(item.get("code") or "").strip().upper(): item
        for item in _sequence(payload.get("board"))
        if isinstance(item, Mapping) and str(item.get("code") or "").strip()
    }
    for packet in packets:
        symbol = str(_mapping(packet.get("identity")).get("symbol") or "").strip().upper()
        board = board_by_code.get(symbol, {})
        packet["forecast_intelligence"] = _mapping(
            _mapping(board.get("context_features")).get("forecast_intelligence")
        )
        packet["horizon_forecasts"] = _mapping(board.get("horizon_forecasts"))
    return packets


def _extended_intraday(
    symbol: str, now: datetime, base: LiveSnapshot
) -> dict[str, float | int | None]:
    try:
        import yfinance as yf

        frame = yf.Ticker(symbol).history(
            period="1d",
            interval="1m",
            auto_adjust=False,
            prepost=False,
            actions=False,
        )
        if frame is None or frame.empty:
            raise RuntimeError("empty timing frame")
        if getattr(frame.index, "tz", None) is None:
            frame.index = frame.index.tz_localize("UTC").tz_convert(NY)
        else:
            frame.index = frame.index.tz_convert(NY)
        session = frame[(frame.index.date == now.date()) & (frame.index <= now)]
        if session.empty:
            raise RuntimeError("empty regular session through evaluation time")
        volumes = session["Volume"].fillna(0)
        typical = (session["High"] + session["Low"] + session["Close"]) / 3.0
        total = float(volumes.sum())
        vwap = float((typical * volumes).sum() / total) if total > 0 else None
        closes = [float(value) for value in session["Close"].dropna().tolist()]
        last5 = (
            (closes[-1] / closes[-6] - 1.0) * 100.0
            if len(closes) >= 6 and closes[-6] > 0
            else None
        )
        minute_returns = [
            (closes[index] / closes[index - 1] - 1.0) * 100.0
            for index in range(1, len(closes))
            if closes[index - 1] > 0
        ]
        intraday_vol = (
            pstdev(minute_returns) * math.sqrt(30.0)
            if len(minute_returns) >= 3
            else (base.session_high / max(base.session_low, 1e-9) - 1.0) * 100.0 * 0.35
        )
        minutes = max(0, int((now.hour * 60 + now.minute) - (9 * 60 + 30)))
        return {
            "session_vwap": vwap,
            "last_5m_return_pct": last5,
            "intraday_volatility_pct": intraday_vol,
            "minutes_since_open": minutes,
        }
    except Exception as exc:
        logger.info("%s extended timing fields unavailable: %s", symbol, exc)
        minutes = max(0, int((now.hour * 60 + now.minute) - (9 * 60 + 30)))
        range_vol = (
            (base.session_high / max(base.session_low, 1e-9) - 1.0) * 100.0 * 0.35
        )
        return {
            "session_vwap": None,
            "last_5m_return_pct": base.return_from_open_pct,
            "intraday_volatility_pct": range_vol,
            "minutes_since_open": minutes,
        }


def _to_open_decision(
    packet: Mapping[str, Any],
    base: ConfirmationDecision,
    snapshot: LiveSnapshot | None,
    *,
    evaluated_at: datetime,
) -> OpenTimingDecision:
    forecast = _extract_forecast(packet)
    contract = _execution_contract(packet)
    common = dict(
        symbol=base.symbol,
        current_price=base.current_price,
        entry_low=base.entry_low,
        entry_high=base.entry_high,
        stop_loss=base.stop_loss,
        targets=base.targets,
        max_position_pct=base.max_position_pct,
        return_from_open_pct=base.return_from_open_pct,
        volume_ratio=base.volume_ratio,
        probability_up_1d=forecast["p1"],
        probability_up_5d=forecast["p5"],
        probability_up_20d=forecast["p20"],
        expected_return_5d_pct=forecast["return5"],
        expected_alpha_5d_pct=forecast["alpha5"],
        forecast_confidence=forecast["confidence"],
        source_trade_date=base.source_trade_date,
        source_last_bar_time=base.source_last_bar_time,
        execution_status=contract["status"],
        conditional_entry_price=contract["conditional_entry_price"],
        conditional_entry_reason=contract["conditional_entry_reason"],
    )
    if snapshot is None:
        return OpenTimingDecision(
            action="DATA_UNAVAILABLE",
            label=ACTION_LABELS["DATA_UNAVAILABLE"],
            reason=base.reason,
            starter_position_pct=0.0,
            better_entry_score=0.0,
            better_entry_probability=0.0,
            expected_better_price=None,
            expected_improvement_pct=0.0,
            recheck_minutes=15,
            terminal=False,
            **common,
        )

    effective_status, effective_reason = _effective_timing_base(packet, base)
    ext = _extended_intraday(base.symbol, evaluated_at, snapshot)
    timing = IntradayTimingModel().assess(
        base_status=effective_status,
        current_price=snapshot.current_price,
        entry_low=base.entry_low,
        entry_high=base.entry_high,
        stop_loss=base.stop_loss,
        session_low=snapshot.session_low,
        session_high=snapshot.session_high,
        session_vwap=_finite(ext["session_vwap"]),
        last_5m_return_pct=_finite(ext["last_5m_return_pct"]),
        intraday_volatility_pct=_finite(ext["intraday_volatility_pct"]),
        minutes_since_open=int(ext["minutes_since_open"] or 0),
        probability_up_1d=forecast["p1"],
        probability_up_5d=forecast["p5"],
    )
    reason = (
        effective_reason
        if timing.action in {"NO_BUY", "INVALIDATED", "DATA_UNAVAILABLE"}
        else f"{effective_reason}；择时判断：{timing.rationale}"
    )
    return OpenTimingDecision(
        action=timing.action,
        label=ACTION_LABELS.get(timing.action, timing.action),
        reason=reason,
        starter_position_pct=(
            base.starter_position_pct if timing.action == "BUY_NOW" else 0.0
        ),
        better_entry_score=timing.better_entry_probability,
        better_entry_probability=timing.better_entry_probability,
        expected_better_price=timing.expected_better_price,
        expected_improvement_pct=timing.expected_improvement_pct,
        recheck_minutes=timing.recheck_minutes,
        terminal=timing.terminal,
        **common,
    )



def _enforce_execution_contract(decision: OpenTimingDecision) -> OpenTimingDecision:
    if decision.action != "BUY_NOW" or decision.execution_status == "FULL_APPROVED":
        return decision
    payload = asdict(decision)
    if decision.execution_status == "REJECTED":
        payload.update(
            action="NO_BUY",
            label=ACTION_LABELS["NO_BUY"],
            reason=decision.reason + "；收盘风险层已拒绝新仓，盘中不得绕过。",
            starter_position_pct=0.0,
            terminal=True,
        )
    else:
        payload.update(
            action="WAIT_CONFIRMATION",
            label=ACTION_LABELS["WAIT_CONFIRMATION"],
            reason=decision.reason + "；收盘仅为条件批准，尚未获得完全执行授权。",
            starter_position_pct=0.0,
            terminal=False,
        )
    return OpenTimingDecision(**payload)

def _money(value: float | None) -> str:
    return "N/A" if value is None else f"${value:.2f}"


def _pct(value: float | None, *, probability: bool = False) -> str:
    if value is None:
        return "N/A"
    return f"{value:.0%}" if probability else f"{value:+.2f}%"


def _execution_label(status: str) -> str:
    return EXECUTION_STATUS_LABELS.get(status, status or "未知")


def render_markdown(
    decisions: Sequence[OpenTimingDecision],
    *,
    generated_at: datetime,
    source_run_id: str | None,
) -> str:
    now = generated_at.astimezone(NY)
    buy = sum(item.action == "BUY_NOW" for item in decisions)
    better = sum(item.action == "WAIT_BETTER_ENTRY" for item in decisions)
    confirm = sum(item.action == "WAIT_CONFIRMATION" for item in decisions)
    blocked = sum(item.action in {"NO_BUY", "INVALIDATED"} for item in decisions)
    unavailable = sum(item.action == "DATA_UNAVAILABLE" for item in decisions)
    lines = [
        f"# 美股盘中择时决策 · {now.strftime('%Y-%m-%d %H:%M ET')}",
        "",
        "> 收盘层先决定风险资格，盘中层再决定执行时点。条件批准不是风险否决：它表示逻辑仍可跟踪，但尚未满足完整执行条件。",
        "",
        "## 一眼结论",
        "",
        f"- **现在可以买**：{buy} 只",
        f"- **更适合等更好买点**：{better} 只",
        f"- **等待价格确认**：{confirm} 只",
        f"- **今天不买/计划失效**：{blocked} 只",
        f"- **行情不足**：{unavailable} 只",
    ]
    if source_run_id:
        lines.append(f"- **上一收盘决策来源**：run `{source_run_id}`")
    lines += [
        "",
        "| 标的 | 收盘授权 | 当前动作 | 当前价 | 1D上涨概率 | 5D上涨概率 | 更好买点评分* | 预计更优价 |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for item in decisions:
        lines.append(
            f"| {item.symbol} | **{_execution_label(item.execution_status)}** | **{item.label}** | "
            f"{_money(item.current_price)} | {_pct(item.probability_up_1d, probability=True)} | "
            f"{_pct(item.probability_up_5d, probability=True)} | "
            f"{_pct(item.better_entry_score, probability=True)} | {_money(item.expected_better_price)} |"
        )
    for index, item in enumerate(decisions, 1):
        lines += [
            "",
            f"## {index}. {item.symbol} · {item.label}",
            "",
            f"- **收盘执行状态**：{_execution_label(item.execution_status)} (`{item.execution_status}`)",
            f"- **当前判断**：{item.reason}",
            f"- **当前价**：{_money(item.current_price)}；较开盘 {_pct(item.return_from_open_pct)}",
            f"- **预测**：1D {_pct(item.probability_up_1d, probability=True)} / 5D {_pct(item.probability_up_5d, probability=True)} / 20D {_pct(item.probability_up_20d, probability=True)}；5D 期望收益 {_pct(item.expected_return_5d_pct)}；5D 相对 SPY Alpha {_pct(item.expected_alpha_5d_pct)}",
            f"- **择时**：更好买点启发式评分（未校准） {_pct(item.better_entry_score, probability=True)}；预计可改善 {item.expected_improvement_pct:.2f}%；参考更优价 {_money(item.expected_better_price)}",
        ]
        if item.conditional_entry_price is not None:
            lines.append(
                f"- **收盘条件价**：{_money(item.conditional_entry_price)}；原因 `{item.conditional_entry_reason or 'conditional_entry'}`"
            )
        elif item.execution_status == "CONDITIONAL_APPROVED":
            lines.append(
                "- **条件批准说明**：当前尚无完整风险边界，继续等待可执行计划；盘中不会临时补造止损、目标或仓位。"
            )
        if item.forecast_confidence is not None:
            lines.append(
                f"- **预测可信度**：{item.forecast_confidence:.0%}（与证据覆盖率分开；低样本会自动收缩）"
            )
        if item.entry_low is not None and item.entry_high is not None:
            lines.append(
                f"- **风控入场区间**：${item.entry_low:.2f}–${item.entry_high:.2f}"
            )
        if item.action == "BUY_NOW":
            lines.append(
                f"- **现在执行**：首仓不超过 {item.starter_position_pct:.1f}%；计划总仓位上限 {item.max_position_pct:.1f}%"
            )
        elif not item.terminal:
            lines.append(
                f"- **下一次评估**：约 {item.recheck_minutes} 分钟后或价格/状态明显变化时。"
            )
        if item.stop_loss is not None:
            lines.append(f"- **止损/失效线**：${item.stop_loss:.2f}")
        if item.targets:
            lines.append(
                "- **目标位**：" + " / ".join(f"${value:.2f}" for value in item.targets)
            )
    lines += [
        "",
        "## 决策纪律",
        "",
        "- `FULL_APPROVED`：收盘风险层已批准新仓，盘中只优化执行时点。",
        "- `CONDITIONAL_APPROVED`：投资逻辑仍可跟踪，但需价格/确认/完整风险计划满足后才能买，不等于今天永久不买。",
        "- `REJECTED`：收盘风险层明确拒绝新仓，盘中模型不能绕过。",
        "- `等更好买点` 不是看空，而是当前价格的等待期望值高于立即追入。",
        "- `等待确认` 表示当前仍缺执行条件，首仓保持 0%。",
        "- 止损、计划失效和收盘风险否决是硬边界，盘中模型不能绕过。",
        "- 收盘层上涨概率和期望收益来自历史校准，不是收益保证。",
        "- `更好买点评分` 当前是盘中启发式 score，不是校准概率；Research Ledger 会按多时点 outcome 验证，样本不足前不得表述为胜率。",
        "",
    ]
    return "\n".join(lines)


def _decision_payload(decision: OpenTimingDecision) -> dict[str, Any]:
    payload = asdict(decision)
    payload["expected_wait_minutes"] = (
        decision.recheck_minutes if decision.action == "WAIT_BETTER_ENTRY" else 0
    )
    payload["better_entry_reason"] = (
        "intraday_volatility_pullback"
        if decision.action == "WAIT_BETTER_ENTRY"
        else None
    )
    return payload


def _semantic_price_state(
    current_price: float | None,
    entry_low: float | None,
    entry_high: float | None,
    stop_loss: float | None,
) -> str:
    price = _finite(current_price)
    low = _finite(entry_low)
    high = _finite(entry_high)
    stop = _finite(stop_loss)
    if price is None:
        return "unknown"
    if stop is not None and price <= stop:
        return "at_or_below_stop"
    if low is not None and high is not None:
        if price < low:
            return "below_entry"
        if price <= high:
            return "inside_entry"
        premium = (price / high - 1.0) * 100.0 if high > 0 else 0.0
        if premium < 0.5:
            return "above_entry_lt_0_5pct"
        if premium < 1.0:
            return "above_entry_0_5_1pct"
        if premium < 2.0:
            return "above_entry_1_2pct"
        return "above_entry_ge_2pct"
    return "priced"


def _signature(decisions: Sequence[OpenTimingDecision]) -> str:
    compact = [
        {
            "symbol": item.symbol,
            "execution_status": item.execution_status,
            "action": item.action,
            "better_bucket": min(9, max(0, int(item.better_entry_score * 10.0))),
            "price_state": _semantic_price_state(
                item.current_price, item.entry_low, item.entry_high, item.stop_loss
            ),
        }
        for item in decisions
    ]
    return hashlib.sha256(
        json.dumps(compact, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]


def _read_previous(path: str | Path | None) -> dict[str, Any] | None:
    if not path or not Path(path).is_file():
        return None
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _should_notify(
    previous: Mapping[str, Any] | None,
    *,
    signature: str,
    generated_at: datetime,
    force: bool,
) -> bool:
    del generated_at
    if force or not previous:
        return True
    return str(previous.get("state_signature") or "") != signature


def _notify(
    report_path: Path, decisions: Sequence[OpenTimingDecision], session_date: str
) -> bool:
    from src.notification import NotificationService

    service = NotificationService()
    if not service.is_available():
        return False
    return bool(
        service.send(
            report_path.read_text(encoding="utf-8"),
            email_stock_codes=[item.symbol for item in decisions if item.symbol],
            email_send_to_all=True,
            route_type="report",
            severity="info",
            dedup_key=f"us-open-timing-{session_date}",
        )
    )


def run(
    *,
    v6_payload_path: str | Path,
    output_dir: str | Path,
    notify: bool = False,
    source_run_id: str | None = None,
    now: datetime | None = None,
    previous_state_path: str | Path | None = None,
    force_notify: bool = False,
    allow_all_unavailable: bool = False,
    **policy: float,
) -> dict[str, Any]:
    generated_at = (now or datetime.now(NY)).astimezone(NY)
    packets = load_runtime_packets(v6_payload_path)
    decisions: list[OpenTimingDecision] = []
    live_success = 0
    allowed = {
        "normal_chase_tolerance_pct",
        "momentum_chase_tolerance_pct",
        "weak_open_pct",
        "min_volume_ratio",
        "min_opening_range_position",
        "momentum_min_opening_range_position",
        "momentum_min_volume_ratio",
        "max_quote_age_minutes",
        "starter_position_pct",
    }
    v2_policy = {key: value for key, value in policy.items() if key in allowed}
    for packet in packets:
        symbol = str(_mapping(packet.get("identity")).get("symbol") or "").strip().upper()
        if not symbol:
            continue
        snapshot = None
        error = None
        try:
            snapshot = fetch_live_snapshot(symbol, now=generated_at)
            live_success += 1
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("%s live timing unavailable: %s", symbol, error)
        base = classify_confirmation_v2(
            packet,
            snapshot,
            evaluated_at=generated_at,
            data_error=error,
            **v2_policy,
        )
        decision = _to_open_decision(
            packet, base, snapshot, evaluated_at=generated_at
        )
        decisions.append(_enforce_execution_contract(decision))
    if not decisions:
        raise RuntimeError("no symbols available in prior final decision payload")
    if live_success == 0 and not allow_all_unavailable:
        raise RuntimeError(
            "all live U.S. session quotes unavailable; refuse to send a false timing decision"
        )

    follow = any(not item.terminal for item in decisions)
    signature = _signature(decisions)
    previous = _read_previous(previous_state_path)
    send_now = notify and _should_notify(
        previous, signature=signature, generated_at=generated_at, force=force_notify
    )
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "us_open_confirmation_latest.md"
    json_path = output / "us_open_confirmation_latest.json"
    report_path.write_text(
        render_markdown(
            decisions, generated_at=generated_at, source_run_id=source_run_id
        ),
        encoding="utf-8",
    )
    summary = {
        "symbols": len(decisions),
        "buy_now": sum(item.action == "BUY_NOW" for item in decisions),
        "wait_better_entry": sum(
            item.action == "WAIT_BETTER_ENTRY" for item in decisions
        ),
        "wait_confirmation": sum(
            item.action == "WAIT_CONFIRMATION" for item in decisions
        ),
        "no_buy": sum(item.action in {"NO_BUY", "INVALIDATED"} for item in decisions),
        "data_unavailable": sum(
            item.action == "DATA_UNAVAILABLE" for item in decisions
        ),
        "execution_status_counts": {
            status: sum(item.execution_status == status for item in decisions)
            for status in EXECUTION_STATUS_LABELS
        },
    }
    payload = {
        "version": "us-open-timing-v7.2",
        "policy_version": POLICY_VERSION,
        "better_entry_metric": {
            "field": "better_entry_score",
            "legacy_alias": "better_entry_probability",
            "semantics": "heuristic_score",
            "calibrated": False,
        },
        "generated_at": generated_at.isoformat(),
        "source_run_id": source_run_id,
        "state_signature": signature,
        "follow_up_needed": follow,
        "summary": summary,
        "decisions": [_decision_payload(item) for item in decisions],
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
        _notify(report_path, decisions, generated_at.strftime("%Y-%m-%d"))
        if send_now
        else False
    )
    if send_now and not sent:
        raise RuntimeError("open timing notification failed")
    return {
        "policy_version": POLICY_VERSION,
        "report": str(report_path),
        "json": str(json_path),
        "symbols": len(decisions),
        "live_success": live_success,
        "buy_now": summary["buy_now"],
        "wait_better_entry": summary["wait_better_entry"],
        "wait_confirmation": summary["wait_confirmation"],
        "no_buy": summary["no_buy"],
        "data_unavailable": summary["data_unavailable"],
        "follow_up_needed": follow,
        "state_signature": signature,
        "notified": sent,
        "notification_suppressed": bool(notify and not send_now),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="V7.2 U.S. open intraday timing decision with three-state close authorization"
    )
    parser.add_argument("--v6-payload", required=True)
    parser.add_argument("--output-dir", default="open_confirmation_reports")
    parser.add_argument(
        "--source-run-id", default=os.getenv("OPEN_CONFIRMATION_SOURCE_RUN_ID")
    )
    parser.add_argument(
        "--previous-state", default=os.getenv("OPEN_CONFIRMATION_PREVIOUS_STATE")
    )
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--force-notify", action="store_true")
    parser.add_argument("--allow-all-unavailable", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    print(
        json.dumps(
            run(
                v6_payload_path=args.v6_payload,
                output_dir=args.output_dir,
                source_run_id=args.source_run_id,
                previous_state_path=args.previous_state,
                notify=args.notify,
                force_notify=args.force_notify,
                allow_all_unavailable=args.allow_all_unavailable,
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
