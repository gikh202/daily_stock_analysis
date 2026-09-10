from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from scripts.realtime_email import send_realtime_email
from scripts.run_us_open_confirmation import _validated_live_price

NY = ZoneInfo("America/New_York")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _money(value: float | None) -> str:
    return "N/A" if value is None else f"${value:.2f}"


def _pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value:+.2f}%"


def _load(path: str | Path) -> tuple[list[dict[str, Any]], dict[str, Mapping[str, Any]]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    packets = [
        dict(item)
        for item in (_mapping(payload.get("final_decisions")).get("packets") or [])
        if isinstance(item, Mapping)
    ]
    board = {
        str(item.get("code") or "").strip().upper(): item
        for item in (payload.get("board") or [])
        if isinstance(item, Mapping)
    }
    if not packets:
        raise RuntimeError("prior V6/V7 payload has no final decision packets")
    return packets, board


def _session(
    symbol: str,
    now: datetime,
    *,
    session_date: date | None = None,
) -> dict[str, Any]:
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    frame = ticker.history(
        period="5d",
        interval="1m",
        auto_adjust=False,
        prepost=False,
        actions=False,
    )
    if frame is None or frame.empty:
        raise RuntimeError("no 1m bars")
    if getattr(frame.index, "tz", None) is None:
        frame.index = frame.index.tz_localize("UTC").tz_convert(NY)
    else:
        frame.index = frame.index.tz_convert(NY)
    target_date = session_date or now.date()
    session = frame[
        (frame.index.date == target_date)
        & (frame.index <= now)
    ].between_time("09:30", "16:00")
    if session.empty:
        raise RuntimeError(f"no US regular-session bars for {target_date}")
    first = session.iloc[0]
    op = _finite(first.get("Open"))
    if op is None or op <= 0:
        raise RuntimeError("invalid session open")
    if target_date == now.date():
        (
            close,
            price_source,
            bar_close,
            quote_price,
            validation,
            quote_day_low,
            quote_day_high,
        ) = _validated_live_price(ticker, session, symbol)
    else:
        close = _finite(session.iloc[-1].get("Close"))
        if close is None or close <= 0:
            raise RuntimeError("invalid completed-session close")
        price_source = "yfinance_1m_completed_session"
        bar_close = close
        quote_price = None
        validation = "completed_session_bar"
        quote_day_low = None
        quote_day_high = None
    return {
        "price": close,
        "open": op,
        "high": (
            float(quote_day_high)
            if quote_day_high is not None
            else float(session["High"].max())
        ),
        "low": (
            float(quote_day_low)
            if quote_day_low is not None
            else float(session["Low"].min())
        ),
        "return_from_open_pct": (close / op - 1.0) * 100.0,
        "last_bar": session.index[-1].isoformat(),
        "price_source": price_source,
        "bar_close_price": bar_close,
        "quote_price": quote_price,
        "price_validation": validation,
    }


def _plan(packet: Mapping[str, Any]) -> dict[str, Any]:
    execution = _mapping(packet.get("execution"))
    zone = execution.get("entry_zone")
    low = high = None
    if isinstance(zone, (list, tuple)) and len(zone) == 2:
        low, high = _finite(zone[0]), _finite(zone[1])
    targets = [
        value
        for value in (_finite(item) for item in (execution.get("targets") or []))
        if value is not None
    ]
    return {
        "entry_low": low,
        "entry_high": high,
        "stop": _finite(execution.get("stop_loss")),
        "targets": targets,
    }


def _forecast(board: Mapping[str, Any]) -> dict[str, float | None]:
    intel = _mapping(_mapping(board.get("context_features")).get("forecast_intelligence"))
    horizons = _mapping(intel.get("horizons"))
    h5 = _mapping(horizons.get("5d"))
    return {
        "p5": _finite(h5.get("probability_up")),
        "ret5": _finite(h5.get("expected_return_pct")),
        "alpha5": _finite(h5.get("expected_alpha_vs_spy_pct")),
    }


def _state(price: float, plan: Mapping[str, Any]) -> str:
    low, high, stop = plan.get("entry_low"), plan.get("entry_high"), plan.get("stop")
    if stop is not None and price <= stop:
        return "计划失效/触及止损线"
    if low is not None and high is not None:
        if low <= price <= high:
            return "收盘位于计划买入区"
        if price > high:
            premium = (price / high - 1.0) * 100.0
            return f"高于计划买入区 {premium:.2f}%，次日不追价"
        return "低于原买入区，次日先确认是否止跌"
    return "原计划无完整买入区，保持等待"


def run(
    v6_payload: str | Path,
    *,
    notify: bool = True,
    now: datetime | None = None,
    session_date: date | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(NY)).astimezone(NY)
    target_date = session_date or now.date()
    packets, board = _load(v6_payload)
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for packet in packets:
        symbol = str(_mapping(packet.get("identity")).get("symbol") or "").strip().upper()
        if not symbol:
            continue
        try:
            snap = _session(symbol, now, session_date=target_date)
            plan = _plan(packet)
            fc = _forecast(board.get(symbol, {}))
            rows.append({"symbol": symbol, "snap": snap, "plan": plan, "forecast": fc})
        except Exception as exc:
            errors.append(f"{symbol}: {type(exc).__name__}: {exc}")

    if not rows:
        raise RuntimeError("close flash has no usable session data")

    lines = [
        f"# 美股收盘快讯 · {target_date.isoformat()} 收盘",
        "",
        f"- **生成时间**：{now.strftime('%Y-%m-%d %H:%M ET')}",
        "",
        "> 这是低延迟收盘快讯：先报告实际收盘位置与上一交易计划状态。完整 V4+V6/V7 深度日报随后发送。",
        "",
        "| 标的 | 收盘价 | 价格源 | 日内涨跌 | 原计划状态 | 5D P(up) | 5D Alpha |",
        "|---|---:|---|---:|---|---:|---:|",
    ]
    for row in rows:
        snap, fc = row["snap"], row["forecast"]
        p5 = "N/A" if fc["p5"] is None else f"{fc['p5']:.0%}"
        alpha = _pct(fc["alpha5"])
        lines.append(
            f"| {row['symbol']} | {_money(snap['price'])} | {snap.get('price_source') or 'N/A'} | "
            f"{_pct(snap['return_from_open_pct'])} | "
            f"{_state(snap['price'], row['plan'])} | {p5} | {alpha} |"
        )
    lines += ["", "## 明日执行原则", ""]
    for row in rows:
        symbol, snap, plan = row["symbol"], row["snap"], row["plan"]
        parts = [
            f"- **{symbol}**：{_state(snap['price'], plan)}",
            (
                f"行情 {snap.get('price_source') or 'N/A'} "
                f"(1m={_money(snap.get('bar_close_price'))}, "
                f"quote={_money(snap.get('quote_price'))}, "
                f"{snap.get('price_validation') or 'N/A'})"
            ),
        ]
        if plan["entry_low"] is not None and plan["entry_high"] is not None:
            parts.append(f"原入场 {_money(plan['entry_low'])}–{_money(plan['entry_high'])}")
        if plan["stop"] is not None:
            parts.append(f"止损 {_money(plan['stop'])}")
        lines.append("；".join(parts))
    if errors:
        lines += ["", "## 数据降级", *[f"- {item}" for item in errors[:10]]]
    report = "\n".join(lines) + "\n"
    if notify:
        send_realtime_email(
            f"美股收盘快讯 {target_date.isoformat()}",
            report,
            sender_name="AI 美股收盘快讯",
        )
    return {
        "symbols": len(rows),
        "errors": errors,
        "session_date": target_date.isoformat(),
        "generated_at": now.isoformat(),
        "report": report,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Low-latency US close flash")
    parser.add_argument("--v6-payload", required=True)
    parser.add_argument(
        "--session-date",
        default="",
        help="Completed XNYS session date YYYY-MM-DD; defaults to current NY date.",
    )
    parser.add_argument("--output", default="close_flash_reports/us_close_flash_latest.md")
    parser.add_argument("--no-notify", action="store_true")
    args = parser.parse_args()
    parsed_session_date = (
        date.fromisoformat(args.session_date)
        if str(args.session_date or "").strip()
        else None
    )
    result = run(
        args.v6_payload,
        notify=not args.no_notify,
        session_date=parsed_session_date,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(result["report"], encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "report"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
