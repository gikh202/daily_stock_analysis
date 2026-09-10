from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import sys
from dataclasses import asdict, dataclass, replace
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
from src.forecasting.entry_optimizer import EntryOptimization, EntryOptimizer
from src.forecasting.regime_policy import load_regime_timing_policy

logger = logging.getLogger("us_open_timing")
NY = ZoneInfo("America/New_York")
POLICY_VERSION = "us-open-timing-v9.0"
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
    "UNRESOLVED": "未决，可重新确认",
    "HARD_REJECTED": "硬风险拒绝",
    "REJECTED": "旧版未决",
}
DIRECTION_SIGNAL_LABELS = {
    "BULLISH": "看涨",
    "BEARISH": "看跌",
    "NEUTRAL": "中性",
    "NO_SIGNAL": "无有效信号",
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
    execution_status: str = "UNRESOLVED"
    conditional_entry_price: float | None = None
    conditional_entry_reason: str | None = None
    ideal_entry_price: float | None = None
    acceptable_entry_low: float | None = None
    acceptable_entry_high: float | None = None
    no_chase_above: float | None = None
    entry_candidate_source: str | None = None
    entry_touch_score: float | None = None
    entry_ev_score: float | None = None
    entry_candidates: tuple[dict[str, Any], ...] = ()
    market_regime: str | None = None
    direction_signal: str = "NO_SIGNAL"
    forecast_tradeable_5d: bool = False
    calibration_samples_5d: int = 0
    calibration_status_5d: str = "prior_only"
    historical_hit_rate_5d: float | None = None
    majority_baseline_5d: float | None = None
    calibration_scope_5d: str | None = None
    price_source: str | None = None
    bar_close_price: float | None = None
    quote_price: float | None = None
    price_validation: str | None = None


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
    root_contract = _mapping(packet.get("execution_contract"))
    raw_status = str(
        assessment.get("execution_status") or root_contract.get("status") or ""
    ).strip().upper()
    if raw_status == "REJECTED":
        raw_status = "UNRESOLVED"
    if raw_status not in {"FULL_APPROVED", "CONDITIONAL_APPROVED", "UNRESOLVED", "HARD_REJECTED"}:
        verdict = str(assessment.get("verdict") or "").strip().lower()
        if bool(assessment.get("execution_authorized")):
            raw_status = "FULL_APPROVED"
        elif assessment.get("worth_buying") is True:
            raw_status = "CONDITIONAL_APPROVED"
        elif verdict == "avoid":
            raw_status = "HARD_REJECTED"
        else:
            raw_status = "UNRESOLVED"
    return {
        "status": raw_status,
        "hard_block": raw_status == "HARD_REJECTED",
        "reject_reason_code": str(
            assessment.get("reject_reason_code")
            or root_contract.get("reject_reason_code")
            or ""
        ).strip()
        or None,
        "conditional_entry_price": _finite(assessment.get("conditional_entry_price")),
        "conditional_entry_reason": str(
            assessment.get("conditional_entry_reason") or ""
        ).strip()
        or None,
    }


def _effective_timing_base(
    packet: Mapping[str, Any], base: ConfirmationDecision
) -> tuple[str, str]:
    """Keep hard risk vetoes monotonic while allowing unresolved states to re-evaluate."""
    contract = _execution_contract(packet)
    if contract["hard_block"]:
        reason_code = contract.get("reject_reason_code") or "UNSPECIFIED_HARD_RISK"
        return (
            "NO_BUY",
            f"上一收盘为 HARD_REJECTED（{reason_code}）；盘中择时模型无权解除硬风险否决。",
        )
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


def _extract_forecast(packet: Mapping[str, Any]) -> dict[str, Any]:
    intelligence = _mapping(packet.get("forecast_intelligence"))
    horizons = _mapping(intelligence.get("horizons")) or _mapping(
        packet.get("horizon_forecasts")
    )

    def bucket(name: str) -> Mapping[str, Any]:
        return _mapping(horizons.get(name))

    def meta(block: Mapping[str, Any]) -> dict[str, Any]:
        diagnostics = _mapping(block.get("diagnostics"))
        status = str(block.get("calibration_status") or "prior_only").strip().lower()
        try:
            samples = max(0, int(block.get("calibration_samples") or 0))
        except (TypeError, ValueError):
            samples = 0
        hit = _finite(block.get("historical_direction_hit_rate"))
        if hit is None:
            hit = _finite(diagnostics.get("historical_direction_hit_rate"))
        baseline = _finite(block.get("historical_majority_baseline_accuracy"))
        if baseline is None:
            baseline = _finite(
                diagnostics.get("historical_majority_baseline_accuracy")
            )
        scope = str(
            diagnostics.get("calibration_scope")
            or block.get("calibration_scope")
            or ""
        ).strip() or None
        tradeable = bool(
            status == "mature"
            and samples >= 50
            and hit is not None
            and hit >= 0.52
            and (baseline is None or hit >= baseline + 0.02)
        )
        return {
            "status": status,
            "samples": samples,
            "hit": hit,
            "baseline": baseline,
            "scope": scope,
            "tradeable": tradeable,
        }

    h1, h5, h20 = bucket("1d"), bucket("5d"), bucket("20d")
    m1, m5, m20 = meta(h1), meta(h5), meta(h20)
    p5 = _finite(h5.get("probability_up"))
    return5 = _finite(h5.get("expected_return_pct"))
    direction_signal = "NO_SIGNAL"
    if m5["tradeable"] and p5 is not None and return5 is not None:
        if p5 >= 0.58 and return5 > 0:
            direction_signal = "BULLISH"
        elif p5 <= 0.42 and return5 < 0:
            direction_signal = "BEARISH"
        else:
            direction_signal = "NEUTRAL"

    return {
        "p1": _finite(h1.get("probability_up")),
        "p5": p5,
        "p20": _finite(h20.get("probability_up")),
        "return5": return5,
        "alpha5": _finite(h5.get("expected_alpha_vs_spy_pct")),
        "confidence": _finite(h5.get("forecast_confidence")),
        "meta1": m1,
        "meta5": m5,
        "meta20": m20,
        "direction_signal": direction_signal,
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
        packet["_market_regime"] = (
            str(board.get("market_regime") or "").strip().lower()
            or str(_mapping(board.get("context_features")).get("market_regime") or "").strip().lower()
            or None
        )
    return packets


def _extended_intraday(
    symbol: str, now: datetime, base: LiveSnapshot
) -> dict[str, float | int | None]:
    try:
        import yfinance as yf

        frame = yf.Ticker(symbol).history(
            period="5d",
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
        opening = session.between_time("09:30", "09:44")
        opening_range_low = (
            float(opening["Low"].min()) if not opening.empty else None
        )
        close_series = session["Close"].dropna()
        ema20 = (
            float(close_series.ewm(span=20, adjust=False).mean().iloc[-1])
            if not close_series.empty
            else None
        )
        previous_close = None
        prior_dates = sorted(
            {item for item in frame.index.date if item < now.date()},
            reverse=True,
        )
        if prior_dates:
            prior = frame[frame.index.date == prior_dates[0]].between_time("09:30", "16:00")
            if not prior.empty:
                previous_close = _finite(prior.iloc[-1].get("Close"))
        minutes = max(0, int((now.hour * 60 + now.minute) - (9 * 60 + 30)))
        return {
            "session_vwap": vwap,
            "last_5m_return_pct": last5,
            "intraday_volatility_pct": intraday_vol,
            "minutes_since_open": minutes,
            "ema20": ema20,
            "opening_range_low": opening_range_low,
            "previous_close": previous_close,
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
            "ema20": None,
            "opening_range_low": base.opening_15m_low,
            "previous_close": None,
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
        direction_signal=forecast["direction_signal"],
        forecast_tradeable_5d=bool(forecast["meta5"]["tradeable"]),
        calibration_samples_5d=int(forecast["meta5"]["samples"]),
        calibration_status_5d=str(forecast["meta5"]["status"]),
        historical_hit_rate_5d=_finite(forecast["meta5"]["hit"]),
        majority_baseline_5d=_finite(forecast["meta5"]["baseline"]),
        calibration_scope_5d=forecast["meta5"]["scope"],
        price_source=(snapshot.price_source if snapshot is not None else None),
        bar_close_price=(snapshot.bar_close_price if snapshot is not None else None),
        quote_price=(snapshot.quote_price if snapshot is not None else None),
        price_validation=(snapshot.price_validation if snapshot is not None else None),
    )
    if snapshot is None:
        action = "NO_BUY" if contract["hard_block"] else "DATA_UNAVAILABLE"
        return OpenTimingDecision(
            action=action,
            label=ACTION_LABELS[action],
            reason=base.reason,
            starter_position_pct=0.0,
            better_entry_score=0.0,
            better_entry_probability=0.0,
            expected_better_price=None,
            expected_improvement_pct=0.0,
            recheck_minutes=0 if contract["hard_block"] else 15,
            terminal=bool(contract["hard_block"]),
            **common,
        )

    effective_status, effective_reason = _effective_timing_base(packet, base)
    ext = _extended_intraday(base.symbol, evaluated_at, snapshot)
    timing_model = IntradayTimingModel(
        policy=load_regime_timing_policy(packet.get("_market_regime"))
    )
    timing = timing_model.assess(
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
        probability_up_1d=(
            forecast["p1"] if forecast["meta5"]["tradeable"] else 0.50
        ),
        probability_up_5d=(
            forecast["p5"] if forecast["meta5"]["tradeable"] else 0.50
        ),
    )
    action = timing.action
    reason = (
        effective_reason
        if timing.action in {"NO_BUY", "INVALIDATED", "DATA_UNAVAILABLE"}
        else f"{effective_reason}；择时判断：{timing.rationale}"
    )
    if contract["hard_block"]:
        action = "NO_BUY"
        reason = effective_reason
    elif action in {"BUY_NOW", "WAIT_BETTER_ENTRY"} and not forecast["meta5"]["tradeable"]:
        action = "WAIT_CONFIRMATION"
        reason += (
            "；5D方向模型未通过生产可靠度门（至少50个成熟样本、"
            "方向命中率≥52%，且需高于多数类基线至少2个百分点），"
            "本轮禁止把研究倾向升级为可执行买入。"
        )
    optimization = EntryOptimization(None, None, None, None, None, None, None, ())
    if (
        not contract["hard_block"]
        and forecast["meta5"]["tradeable"]
        and action in {"BUY_NOW", "WAIT_BETTER_ENTRY", "WAIT_CONFIRMATION"}
    ):
        optimization = EntryOptimizer().optimize(
            current_price=snapshot.current_price,
            stop_loss=base.stop_loss,
            targets=base.targets,
            entry_low=base.entry_low,
            entry_high=base.entry_high,
            session_low=snapshot.session_low,
            session_high=snapshot.session_high,
            session_vwap=_finite(ext["session_vwap"]),
            ema20=_finite(ext["ema20"]),
            opening_range_low=_finite(ext["opening_range_low"]),
            previous_close=_finite(ext["previous_close"]),
            intraday_volatility_pct=_finite(ext["intraday_volatility_pct"]),
            last_5m_return_pct=_finite(ext["last_5m_return_pct"]),
            probability_up_1d=forecast["p1"],
            probability_up_5d=forecast["p5"],
            expected_return_5d_pct=forecast["return5"],
            market_regime=str(packet.get("_market_regime") or "") or None,
            allow_current=action != "WAIT_BETTER_ENTRY",
        )
    optimized_price = (
        optimization.ideal_entry_price or timing.expected_better_price
        if forecast["meta5"]["tradeable"] and not contract["hard_block"]
        else None
    )
    optimized_improvement = (
        timing.expected_improvement_pct
        if forecast["meta5"]["tradeable"] and not contract["hard_block"]
        else 0.0
    )
    if optimized_price is not None and optimized_price < snapshot.current_price:
        optimized_improvement = max(
            optimized_improvement,
            (snapshot.current_price / optimized_price - 1.0) * 100.0,
        )
    return OpenTimingDecision(
        action=action,
        label=ACTION_LABELS.get(action, action),
        reason=reason,
        starter_position_pct=(
            base.starter_position_pct if action == "BUY_NOW" else 0.0
        ),
        better_entry_score=(0.0 if contract["hard_block"] else timing.better_entry_probability),
        better_entry_probability=(0.0 if contract["hard_block"] else timing.better_entry_probability),
        expected_better_price=optimized_price,
        expected_improvement_pct=optimized_improvement,
        recheck_minutes=(0 if contract["hard_block"] else timing.recheck_minutes),
        terminal=(True if contract["hard_block"] else timing.terminal),
        ideal_entry_price=optimization.ideal_entry_price,
        acceptable_entry_low=optimization.acceptable_entry_low,
        acceptable_entry_high=optimization.acceptable_entry_high,
        no_chase_above=optimization.no_chase_above,
        entry_candidate_source=optimization.candidate_source,
        entry_touch_score=optimization.touch_score,
        entry_ev_score=optimization.expected_value_score,
        entry_candidates=tuple(item.to_dict() if hasattr(item, "to_dict") else asdict(item) for item in optimization.candidates),
        market_regime=str(packet.get("_market_regime") or "") or None,
        **common,
    )


def _enforce_execution_contract(decision: OpenTimingDecision) -> OpenTimingDecision:
    """Final monotonicity guard: a hard risk veto can only stay hard or be re-run upstream."""
    if decision.execution_status != "HARD_REJECTED":
        return decision
    if decision.action == "NO_BUY" and decision.terminal:
        return decision
    return replace(
        decision,
        action="NO_BUY",
        label=ACTION_LABELS["NO_BUY"],
        starter_position_pct=0.0,
        better_entry_score=0.0,
        better_entry_probability=0.0,
        expected_better_price=None,
        expected_improvement_pct=0.0,
        recheck_minutes=0,
        terminal=True,
        ideal_entry_price=None,
        acceptable_entry_low=None,
        acceptable_entry_high=None,
        no_chase_above=None,
        entry_candidate_source=None,
        entry_touch_score=None,
        entry_ev_score=None,
        entry_candidates=(),
        reason=decision.reason + "；V9 最终执行契约再次确认：HARD_REJECTED 不允许盘中升级。",
    )


def _money(value: float | None) -> str:
    return "N/A" if value is None else f"${value:.2f}"


def _money_range(low: float | None, high: float | None) -> str:
    if low is None or high is None:
        return "N/A"
    return f"${low:.2f}–${high:.2f}"


def _pct(value: float | None, *, probability: bool = False) -> str:
    if value is None:
        return "N/A"
    return f"{value:.0%}" if probability else f"{value:+.2f}%"


def _execution_label(status: str) -> str:
    return EXECUTION_STATUS_LABELS.get(status, status or "未知")


def _direction_label(signal: str) -> str:
    return DIRECTION_SIGNAL_LABELS.get(signal, signal or "无有效信号")


def _reliability_text(item: OpenTimingDecision) -> str:
    hit = (
        "N/A"
        if item.historical_hit_rate_5d is None
        else f"{item.historical_hit_rate_5d:.1%}"
    )
    baseline = (
        "N/A"
        if item.majority_baseline_5d is None
        else f"{item.majority_baseline_5d:.1%}"
    )
    state = "可用于生产" if item.forecast_tradeable_5d else "研究观察"
    return (
        f"{state} · n={item.calibration_samples_5d} · 命中 {hit} · "
        f"多数基线 {baseline}"
    )


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
        "> V9 将收盘状态拆成可重新确认的 UNRESOLVED 与不可被盘中行情直接解除的 HARD_REJECTED。",
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
        "| 标的 | 昨夜执行契约 | 方向信号 | 实时执行动作 | 当前价 | 价格源 | 5D可靠度 | 理想买点 | 可接受区 | 禁止追价 |",
        "|---|---|---|---|---:|---|---|---:|---:|---:|",
    ]
    for item in decisions:
        lines.append(
            f"| {item.symbol} | **{_execution_label(item.execution_status)}** | "
            f"**{_direction_label(item.direction_signal)}** | **{item.label}** | "
            f"{_money(item.current_price)} | {item.price_source or 'N/A'} | "
            f"{_reliability_text(item)} | {_money(item.ideal_entry_price)} | "
            f"{_money_range(item.acceptable_entry_low, item.acceptable_entry_high)} | "
            f"{_money(item.no_chase_above)} |"
        )
    for index, item in enumerate(decisions, 1):
        lines += [
            "",
            f"## {index}. {item.symbol} · {item.label}",
            "",
            f"- **昨夜执行契约**：{_execution_label(item.execution_status)} (`{item.execution_status}`)",
            f"- **当前判断**：{item.reason}",
            f"- **方向信号**：{_direction_label(item.direction_signal)}；5D可靠度：{_reliability_text(item)}",
            f"- **当前价**：{_money(item.current_price)}；较开盘 {_pct(item.return_from_open_pct)}；同进度开盘量比 {('N/A' if item.volume_ratio is None else f'{item.volume_ratio:.2f}x')}",
            (
                f"- **行情校验**：来源 `{item.price_source or 'N/A'}`；"
                f"1m close {_money(item.bar_close_price)}；quote {_money(item.quote_price)}；"
                f"状态 `{item.price_validation or 'N/A'}`；"
                f"最新 1m bar `{item.source_last_bar_time or 'N/A'}`"
            ),
            f"- **研究倾向（不等于交易信号）**：1D {_pct(item.probability_up_1d, probability=True)} / 5D {_pct(item.probability_up_5d, probability=True)} / 20D {_pct(item.probability_up_20d, probability=True)}；5D 期望收益 {_pct(item.expected_return_5d_pct)}；5D 相对 SPY Alpha {_pct(item.expected_alpha_5d_pct)}",
            f"- **择时**：更好买点启发式评分（未校准） {_pct(item.better_entry_score, probability=True)}；预计可改善 {item.expected_improvement_pct:.2f}%；参考更优价 {_money(item.expected_better_price)}",
        ]
        if item.ideal_entry_price is not None:
            lines.append(
                f"- **买点优化**：理想 {_money(item.ideal_entry_price)}（{item.entry_candidate_source or 'candidate'}）；"
                f"可接受 {_money_range(item.acceptable_entry_low, item.acceptable_entry_high)}；"
                f"高于 {_money(item.no_chase_above)} 不追；候选触达评分 "
                f"{_pct(item.entry_touch_score, probability=True)}；EV score "
                f"{item.entry_ev_score if item.entry_ev_score is not None else 'N/A'}"
            )
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
                f"- **模型证据分**：{item.forecast_confidence:.0%}（不是胜率；低样本/无方向 skill 时不能用于生产动作）"
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
        "- `FULL_APPROVED / CONDITIONAL_APPROVED / UNRESOLVED` 可以在实时层继续确认；`HARD_REJECTED` 不可以。",
        "- `HARD_REJECTED` 只能由完整收盘/风险模型重新运行后解除，单纯价格、VWAP、量能或短线动量不能覆盖。",
        "- 开盘是否可买仍受行情质量、计划完整性、止损、入场/追价边界、盘中强弱和模型可靠度约束。",
        "- `等更好买点` 不是看空，而是当前价格的等待期望值高于立即追入。",
        "- `等待确认` 表示当前仍缺执行条件，首仓保持 0%。",
        "- 少于 50 个成熟样本、方向命中率低于 52%，或未超过多数类基线至少 2 个百分点时，方向模型生产权重固定为 0。",
        "- `更好买点评分` 当前是盘中启发式 score，不是校准概率。",
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
            "direction_signal": item.direction_signal,
            "forecast_tradeable_5d": item.forecast_tradeable_5d,
            "reliability_sample_bucket": item.calibration_samples_5d // 10,
            "reliability_hit_bucket": (
                None
                if item.historical_hit_rate_5d is None
                else round(item.historical_hit_rate_5d, 2)
            ),
            "better_bucket": min(9, max(0, int(item.better_entry_score * 10.0))),
            "ideal_entry": round(item.ideal_entry_price, 2) if item.ideal_entry_price else None,
            "no_chase": round(item.no_chase_above, 2) if item.no_chase_above else None,
            "price_state": _semantic_price_state(
                item.current_price, item.entry_low, item.entry_high, item.stop_loss
            ),
            "price_source": item.price_source,
            "price_validation": item.price_validation,
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
    from scripts.realtime_email import send_realtime_email

    buy = sum(item.action == "BUY_NOW" for item in decisions)
    wait = sum(
        item.action in {"WAIT_BETTER_ENTRY", "WAIT_CONFIRMATION"}
        for item in decisions
    )
    subject = f"美股开盘决策 {session_date}｜可买{buy} 等待{wait}"
    return send_realtime_email(
        subject,
        report_path.read_text(encoding="utf-8"),
        sender_name="AI 美股开盘决策",
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
        "direction_signal_counts": {
            signal: sum(item.direction_signal == signal for item in decisions)
            for signal in DIRECTION_SIGNAL_LABELS
        },
    }
    payload = {
        "version": "us-open-timing-v9.0",
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
        description="V9 U.S. open timing with durable hard-risk veto, validated live prices and reliability quarantine"
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
