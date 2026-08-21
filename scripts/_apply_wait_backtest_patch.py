from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected patch anchor missing in {path}: {old[:120]!r}")
    text = text.replace(old, new, 1)
    p.write_text(text, encoding="utf-8")


# 1) Make the runtime JSON contract explicit and make historical/as-of evaluation
# strictly causal even when a complete historical 1m frame is returned.
replace_once(
    "scripts/run_us_open_timing.py",
    '        session=frame[frame.index.date==now.date()]\n        if session.empty: raise RuntimeError("empty regular session")',
    '        session=frame[(frame.index.date==now.date()) & (frame.index<=now)]\n        if session.empty: raise RuntimeError("empty regular session through evaluation time")',
)
replace_once(
    "scripts/run_us_open_timing.py",
    'def _semantic_price_state(current_price:float|None,entry_low:float|None,entry_high:float|None,stop_loss:float|None)->str:',
    '''def _decision_payload(decision:OpenTimingDecision)->dict[str,Any]:\n    payload=asdict(decision)\n    payload["expected_wait_minutes"]=decision.recheck_minutes if decision.action=="WAIT_BETTER_ENTRY" else 0\n    payload["better_entry_reason"]="intraday_volatility_pullback" if decision.action=="WAIT_BETTER_ENTRY" else None\n    return payload\n\ndef _semantic_price_state(current_price:float|None,entry_low:float|None,entry_high:float|None,stop_loss:float|None)->str:''',
)
replace_once(
    "scripts/run_us_open_timing.py",
    '"decisions":[asdict(x) for x in decisions]',
    '"decisions":[_decision_payload(x) for x in decisions]',
)

# 2) The ledger used to count a target reached any time before the close as a
# successful 15/30-minute wait. Restrict better-entry settlement to the promised
# wait window and calculate the improvement on that same window.
replace_once(
    "scripts/us_open_research_ledger.py",
    '''    future_high = float(future["High"].max())\n    future_low = float(future["Low"].min())\n    close_return = (close_price / signal_price - 1.0) * 100.0\n    mfe = (future_high / signal_price - 1.0) * 100.0\n    mae = (future_low / signal_price - 1.0) * 100.0\n    best_future_improvement = max(\n        0.0, (signal_price - future_low) / signal_price * 100.0\n    )\n\n    cutoff = signal_time + timedelta(minutes=60)''',
    '''    future_high = float(future["High"].max())\n    future_low = float(future["Low"].min())\n    close_return = (close_price / signal_price - 1.0) * 100.0\n    mfe = (future_high / signal_price - 1.0) * 100.0\n    mae = (future_low / signal_price - 1.0) * 100.0\n\n    cutoff = signal_time + timedelta(minutes=60)''',
)
replace_once(
    "scripts/us_open_research_ledger.py",
    '''    stop, target1 = _execution_levels(packet)\n    reference_better_price = _finite(decision.get("expected_better_price"))\n    better_entry_hit = None\n    minutes_to_reference = None\n    if reference_better_price is not None and 0 < reference_better_price < signal_price:\n        matching = future[future["Low"] <= reference_better_price]\n        better_entry_hit = not matching.empty\n        if better_entry_hit:\n            first_time = matching.index[0]\n            minutes_to_reference = max(\n                0.0, (first_time - signal_time).total_seconds() / 60.0\n            )\n''',
    '''    stop, target1 = _execution_levels(packet)\n    reference_better_price = _finite(decision.get("expected_better_price"))\n    wait_minutes_raw = _finite(decision.get("expected_wait_minutes"))\n    if wait_minutes_raw is None:\n        wait_minutes_raw = _finite(decision.get("recheck_minutes"))\n    wait_minutes = int(max(1.0, min(120.0, wait_minutes_raw or 30.0)))\n    wait_cutoff = signal_time + timedelta(minutes=wait_minutes)\n    wait_future = future[future.index <= wait_cutoff]\n    wait_low = float(wait_future["Low"].min()) if not wait_future.empty else None\n    best_future_improvement = (\n        None\n        if wait_low is None\n        else max(0.0, (signal_price - wait_low) / signal_price * 100.0)\n    )\n    better_entry_hit = None\n    minutes_to_reference = None\n    if reference_better_price is not None and 0 < reference_better_price < signal_price:\n        matching = wait_future[wait_future["Low"] <= reference_better_price]\n        better_entry_hit = not matching.empty\n        if better_entry_hit:\n            first_time = matching.index[0]\n            minutes_to_reference = max(\n                0.0, (first_time - signal_time).total_seconds() / 60.0\n            )\n''',
)
replace_once(
    "scripts/us_open_research_ledger.py",
    '''        "minutes_to_reference_better_price": minutes_to_reference,\n    }''',
    '''        "minutes_to_reference_better_price": minutes_to_reference,\n        "expected_wait_minutes": wait_minutes,\n        "wait_window_end": wait_cutoff.isoformat(),\n    }''',
)

# 3) Existing unit fixtures should exercise the explicit runtime contract.
replace_once(
    "tests/test_us_open_research_ledger.py",
    '''        "expected_better_price": 99.5,\n    }''',
    '''        "expected_better_price": 99.5,\n        "expected_wait_minutes": 30,\n        "better_entry_reason": "intraday_volatility_pullback",\n    }''',
)
append = r'''


def test_wait_better_entry_does_not_count_hit_after_promised_wait_window():
    decision = _decision("WAIT_BETTER_ENTRY")
    decision["expected_wait_minutes"] = 15
    row = {
        "signal_bar_time": "2026-08-14T09:44:00-04:00",
        "signal_price": 100.0,
        "packet_json": json.dumps(_packet(stop=98.0, target=104.0)),
        "decision_json": json.dumps(decision),
    }
    frame = _frame(
        [
            ("2026-08-14 09:44", 100.0, 100.1, 99.9, 100.0, 1000),
            ("2026-08-14 09:50", 100.0, 100.4, 99.8, 100.2, 1200),
            ("2026-08-14 10:30", 100.2, 100.3, 99.4, 99.7, 1200),
            ("2026-08-14 16:00", 101.0, 101.2, 100.8, 101.0, 1000),
        ]
    )
    outcome = compute_outcome(row, frame)
    assert outcome is not None
    assert outcome["better_entry_hit"] is False
    assert outcome["minutes_to_reference_better_price"] is None
    assert outcome["expected_wait_minutes"] == 15
    assert outcome["best_future_improvement_pct"] == pytest.approx(0.2)
'''
p = Path("tests/test_us_open_research_ledger.py")
text = p.read_text(encoding="utf-8")
if "test_wait_better_entry_does_not_count_hit_after_promised_wait_window" not in text:
    p.write_text(text + append, encoding="utf-8")

# 4) Explicit output/no-lookahead contract tests.
Path("tests/test_wait_better_entry_contract.py").write_text(r'''from __future__ import annotations

import inspect

from scripts.run_us_open_timing import OpenTimingDecision, _decision_payload
from src.forecasting.timing import IntradayTimingModel


def test_wait_decision_payload_exposes_reference_price_window_and_reason():
    decision = OpenTimingDecision(
        symbol="TEST", action="WAIT_BETTER_ENTRY", label="wait", reason="test",
        current_price=100.0, entry_low=98.0, entry_high=101.0, stop_loss=95.0,
        targets=(105.0,), starter_position_pct=0.0, max_position_pct=20.0,
        return_from_open_pct=0.5, volume_ratio=1.0, probability_up_1d=0.55,
        probability_up_5d=0.60, probability_up_20d=0.58,
        expected_return_5d_pct=1.0, expected_alpha_5d_pct=0.2,
        forecast_confidence=0.7, better_entry_score=0.7,
        better_entry_probability=0.7, expected_better_price=99.4,
        expected_improvement_pct=0.6, recheck_minutes=30, terminal=False,
        source_trade_date="2026-08-20", source_last_bar_time="2026-08-21T09:44:00-04:00",
    )
    payload = _decision_payload(decision)
    assert payload["expected_better_price"] == 99.4
    assert payload["expected_wait_minutes"] == 30
    assert payload["better_entry_reason"] == "intraday_volatility_pullback"


def test_intraday_timing_model_has_no_future_market_input_contract():
    forbidden = {"future", "future_low", "future_high", "close_price", "session_close"}
    params = set(inspect.signature(IntradayTimingModel.assess).parameters)
    assert forbidden.isdisjoint(params)
    decision = IntradayTimingModel().assess(
        base_status="BUY_NOW", current_price=105.0, entry_low=100.0,
        entry_high=105.0, stop_loss=96.0, session_low=101.0,
        session_high=105.2, session_vwap=103.5, last_5m_return_pct=-0.25,
        intraday_volatility_pct=1.2, minutes_since_open=25,
        probability_up_1d=0.54, probability_up_5d=0.61,
    )
    assert decision.action == "WAIT_BETTER_ENTRY"
    assert 96.0 < decision.expected_better_price < 105.0
''', encoding="utf-8")

# 5) Historical WAIT A/B replay using the exact production V2 guard and V7.1
# timing model. Intraday Yahoo retention limits are reported explicitly; the
# script never claims three years when 1m data are not available.
Path("scripts/backtest_wait_better_entry.py").write_text(r'''from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from scripts.backtest_us_open_confirmation import (
    PlanCandidate,
    _finite,
    _latest_causal_candidate,
    _mapping,
    _next_session_date,
    _opening_window,
    _regular_session,
    _volume_ratio,
    fetch_history,
    load_candidates,
)
from scripts.run_us_open_confirmation import LiveSnapshot
from scripts.run_us_open_confirmation_v2 import classify_confirmation_v2
from src.forecasting.timing import IntradayTimingModel

NY = ZoneInfo("America/New_York")


def _forecast_probabilities(db_path: str | Path) -> dict[tuple[str, str], dict[int, float]]:
    result: dict[tuple[str, str], dict[int, float]] = {}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT f.symbol, f.effective_trade_date, f.v6_created_at,
                   h.horizon_days, h.score, h.payload_json
            FROM v6_forecast_runs f
            JOIN v6_horizon_forecasts h ON h.forecast_run_id=f.id
            WHERE f.effective_trade_date IS NOT NULL AND h.horizon_days IN (1,5)
            ORDER BY f.v6_created_at, f.id, h.horizon_days
            """
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        payload: Mapping[str, Any] = {}
        try:
            parsed = json.loads(str(row["payload_json"] or "{}"))
            payload = parsed if isinstance(parsed, Mapping) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        probability = _finite(payload.get("probability_up"))
        if probability is None:
            score = _finite(row["score"])
            probability = None if score is None else score / 100.0
        if probability is None:
            continue
        key = (str(row["symbol"]).upper(), str(row["effective_trade_date"]))
        result.setdefault(key, {})[int(row["horizon_days"])] = max(0.02, min(0.98, probability))
    return result


def _snapshot(candidate: PlanCandidate, frame: Any, session_date: date) -> tuple[LiveSnapshot, Any] | None:
    session = _regular_session(frame, session_date)
    opening = _opening_window(frame, session_date)
    if session.empty or len(opening) < 12:
        return None
    first, last = opening.iloc[0], opening.iloc[-1]
    price, session_open = _finite(last.get("Close")), _finite(first.get("Open"))
    if price is None or session_open is None or price <= 0 or session_open <= 0:
        return None
    opening_volume = float(opening["Volume"].fillna(0).sum())
    prior_median, volume_ratio = _volume_ratio(frame, session_date, opening_volume)
    high, low = float(opening["High"].max()), float(opening["Low"].min())
    snap = LiveSnapshot(
        symbol=candidate.symbol, current_price=price, session_open=session_open,
        session_high=high, session_low=low, opening_15m_high=high,
        opening_15m_low=low, return_from_open_pct=(price/session_open-1.0)*100.0,
        opening_15m_volume=opening_volume, recent_opening_volume_median=prior_median,
        volume_ratio=volume_ratio, bar_count=int(len(opening)),
        last_bar_time=opening.index[-1].isoformat(),
    )
    return snap, session


def _timing_inputs(opening: Any) -> tuple[float | None, float | None, float]:
    volumes = opening["Volume"].fillna(0)
    typical = (opening["High"] + opening["Low"] + opening["Close"]) / 3.0
    total = float(volumes.sum())
    vwap = float((typical*volumes).sum()/total) if total > 0 else None
    closes = [float(v) for v in opening["Close"].dropna().tolist()]
    last5 = (closes[-1]/closes[-6]-1.0)*100.0 if len(closes) >= 6 and closes[-6] > 0 else None
    minute_returns = [(closes[i]/closes[i-1]-1.0)*100.0 for i in range(1, len(closes)) if closes[i-1] > 0]
    vol = pstdev(minute_returns)*math.sqrt(30.0) if len(minute_returns) >= 3 else 0.60
    return vwap, last5, vol


def _entry_zone(packet: Mapping[str, Any]) -> tuple[float | None, float | None, float | None]:
    execution = _mapping(packet.get("execution"))
    zone = execution.get("entry_zone")
    low = high = None
    if isinstance(zone, (list, tuple)) and len(zone) == 2:
        low, high = _finite(zone[0]), _finite(zone[1])
    return low, high, _finite(execution.get("stop_loss"))


def _drawdown(rows: list[dict[str, Any]], key: str) -> float | None:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        value = row.get(key)
        if value is not None and math.isfinite(float(value)):
            grouped[row["session_date"]].append(float(value))
    if not grouped:
        return None
    equity = peak = 1.0
    worst = 0.0
    for day in sorted(grouped):
        equity *= max(0.0, 1.0 + mean(grouped[day]) / 100.0)
        peak = max(peak, equity)
        worst = min(worst, equity/peak - 1.0)
    return 100.0 * worst


def _avg(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return mean(values) if values else None


def run_backtest(db_path: str | Path) -> dict[str, Any]:
    candidates = load_candidates(db_path)
    probabilities = _forecast_probabilities(db_path)
    by_symbol: dict[str, list[PlanCandidate]] = defaultdict(list)
    for candidate in candidates:
        by_symbol[candidate.symbol].append(candidate)
    if not candidates:
        raise RuntimeError("no V6/V7 plan candidates")
    min_date = min(date.fromisoformat(x.effective_trade_date) for x in candidates)
    max_date = max(date.fromisoformat(x.effective_trade_date) for x in candidates)
    requested_start = min_date - timedelta(days=8)
    requested_end = max(date.today(), max_date + timedelta(days=8)) + timedelta(days=1)
    rows: list[dict[str, Any]] = []
    coverage_dates: set[str] = set()
    fetch_errors: list[str] = []

    for symbol, symbol_candidates in sorted(by_symbol.items()):
        try:
            frame = fetch_history(symbol, requested_start, requested_end)
        except Exception as exc:
            fetch_errors.append(f"{symbol}: {type(exc).__name__}: {exc}")
            continue
        grouped: dict[tuple[str, str], list[PlanCandidate]] = defaultdict(list)
        for item in symbol_candidates:
            grouped[(item.source, item.effective_trade_date)].append(item)
        for (_, plan_date_text), group in sorted(grouped.items()):
            plan_date = date.fromisoformat(plan_date_text)
            session_date = _next_session_date(frame, plan_date)
            if session_date is None:
                continue
            candidate = _latest_causal_candidate(group, session_date=session_date)
            if candidate is None:
                continue
            built = _snapshot(candidate, frame, session_date)
            if built is None:
                continue
            snap, session = built
            signal_time = datetime.fromisoformat(snap.last_bar_time).astimezone(NY)
            base = classify_confirmation_v2(candidate.packet, snap, evaluated_at=signal_time)
            entry_low, entry_high, stop = _entry_zone(candidate.packet)
            opening = session[session.index <= signal_time]
            vwap, last5, vol = _timing_inputs(opening)
            probs = probabilities.get((candidate.symbol, candidate.effective_trade_date), {})
            timing = IntradayTimingModel().assess(
                base_status=base.status, current_price=snap.current_price,
                entry_low=entry_low, entry_high=entry_high, stop_loss=stop,
                session_low=snap.session_low, session_high=snap.session_high,
                session_vwap=vwap, last_5m_return_pct=last5,
                intraday_volatility_pct=vol, minutes_since_open=14,
                probability_up_1d=probs.get(1), probability_up_5d=probs.get(5),
            )
            if timing.action != "WAIT_BETTER_ENTRY" or timing.expected_better_price is None:
                continue
            future = session[session.index > signal_time]
            if future.empty:
                continue
            close = _finite(session.iloc[-1].get("Close"))
            if close is None or close <= 0:
                continue
            wait_minutes = max(1, min(120, int(timing.recheck_minutes)))
            wait_future = future[future.index <= signal_time + timedelta(minutes=wait_minutes)]
            if wait_future.empty:
                continue
            expected = float(timing.expected_better_price)
            matching = wait_future[wait_future["Low"] <= expected]
            hit = not matching.empty
            immediate_return = (close/snap.current_price-1.0)*100.0
            immediate_mfe = (float(future["High"].max())/snap.current_price-1.0)*100.0
            immediate_mae = (float(future["Low"].min())/snap.current_price-1.0)*100.0
            wait_return = 0.0
            wait_mfe = wait_mae = None
            minutes_to_entry = None
            if hit:
                first_time = matching.index[0]
                minutes_to_entry = max(0.0, (first_time-signal_time).total_seconds()/60.0)
                after = future[future.index >= first_time]
                wait_return = (close/expected-1.0)*100.0
                wait_mfe = (float(after["High"].max())/expected-1.0)*100.0
                wait_mae = (float(after["Low"].min())/expected-1.0)*100.0
            best_low = float(wait_future["Low"].min())
            improvement = max(0.0, (snap.current_price-best_low)/snap.current_price*100.0)
            max_wait_upside = (float(wait_future["High"].max())/snap.current_price-1.0)*100.0
            rows.append({
                "source": candidate.source, "symbol": candidate.symbol,
                "session_date": session_date.isoformat(), "signal_price": snap.current_price,
                "expected_better_price": expected, "expected_wait_minutes": wait_minutes,
                "better_entry_hit": hit, "minutes_to_entry": minutes_to_entry,
                "best_price_improvement_pct": improvement,
                "immediate_return_pct": immediate_return,
                "wait_policy_return_pct": wait_return,
                "entry_timing_alpha_vs_immediate_pct": wait_return-immediate_return,
                "immediate_mfe_pct": immediate_mfe, "immediate_mae_pct": immediate_mae,
                "wait_mfe_pct": wait_mfe, "wait_mae_pct": wait_mae,
                "missed_continuation": bool((not hit) and max_wait_upside >= 0.50),
                "max_wait_window_upside_pct": max_wait_upside,
            })
            coverage_dates.add(session_date.isoformat())

    hits = [row for row in rows if row["better_entry_hit"]]
    misses = [row for row in rows if not row["better_entry_hit"]]
    payload = {
        "version": "wait-better-entry-backtest-v1",
        "method": "production_v2_guard_plus_v7_1_timing_same_asof_1m",
        "requested_history_start": requested_start.isoformat(),
        "requested_history_end": requested_end.isoformat(),
        "actual_session_start": min(coverage_dates) if coverage_dates else None,
        "actual_session_end": max(coverage_dates) if coverage_dates else None,
        "intraday_history_limit": "Yahoo/yfinance 1m retention can be shorter than requested history; actual_session_start/end are authoritative and no 3-year claim is made unless coverage proves it.",
        "wait_sample_count": len(rows),
        "better_entry_hit_count": len(hits),
        "better_entry_hit_rate": len(hits)/len(rows) if rows else None,
        "avg_best_price_improvement_pct": _avg(rows, "best_price_improvement_pct"),
        "avg_minutes_to_entry": _avg(hits, "minutes_to_entry"),
        "missed_continuation_count": sum(bool(row["missed_continuation"]) for row in misses),
        "missed_continuation_rate": (sum(bool(row["missed_continuation"]) for row in misses)/len(misses)) if misses else None,
        "immediate": {
            "avg_return_pct": _avg(rows, "immediate_return_pct"),
            "win_rate": (sum(row["immediate_return_pct"] > 0 for row in rows)/len(rows)) if rows else None,
            "avg_mfe_pct": _avg(rows, "immediate_mfe_pct"),
            "avg_mae_pct": _avg(rows, "immediate_mae_pct"),
            "max_drawdown_pct": _drawdown(rows, "immediate_return_pct"),
        },
        "wait_policy": {
            "definition": "enter at expected_better_price if touched within expected_wait_minutes; otherwise remain in cash for the session (0% return)",
            "avg_return_pct": _avg(rows, "wait_policy_return_pct"),
            "win_rate": (sum(row["wait_policy_return_pct"] > 0 for row in rows)/len(rows)) if rows else None,
            "avg_mfe_pct_executed": _avg(hits, "wait_mfe_pct"),
            "avg_mae_pct_executed": _avg(hits, "wait_mae_pct"),
            "max_drawdown_pct": _drawdown(rows, "wait_policy_return_pct"),
        },
        "entry_timing_alpha": {
            "definition": "WAIT policy session return minus immediate-entry session return on the identical WAIT signals",
            "avg_vs_immediate_pct": _avg(rows, "entry_timing_alpha_vs_immediate_pct"),
            "median_vs_immediate_pct": median([row["entry_timing_alpha_vs_immediate_pct"] for row in rows]) if rows else None,
        },
        "promotion_check": {
            "minimum_samples": 20,
            "hit_rate_gt_50pct": bool(rows and len(hits)/len(rows) > 0.50),
            "positive_timing_alpha": bool(rows and (_avg(rows, "entry_timing_alpha_vs_immediate_pct") or 0.0) > 0.0),
            "sample_count_ok": len(rows) >= 20,
            "eligible": bool(len(rows) >= 20 and len(hits)/len(rows) > 0.50 and (_avg(rows, "entry_timing_alpha_vs_immediate_pct") or 0.0) > 0.0) if rows else False,
        },
        "fetch_errors": fetch_errors,
        "rows": rows,
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest V7 WAIT_BETTER_ENTRY vs immediate entry")
    parser.add_argument("--v6-db", required=True)
    parser.add_argument("--output", default="wait_better_entry_backtest.json")
    args = parser.parse_args()
    payload = run_backtest(args.v6_db)
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''', encoding="utf-8")

# Changelog: user-visible decision JSON contract + research correctness.
p = Path("docs/CHANGELOG.md")
text = p.read_text(encoding="utf-8")
line = "- [修复] V7.1 WAIT_BETTER_ENTRY 决策 JSON 显式输出 expected_wait_minutes / better_entry_reason，并将 research ledger 的 better-entry 命中严格限制在承诺等待窗口；新增同信号立即买入 vs 等待买入的因果 1m A/B 回测。\n"
anchor = "## [Unreleased]\n\n"
if line not in text:
    if anchor not in text:
        raise SystemExit("CHANGELOG Unreleased anchor missing")
    p.write_text(text.replace(anchor, anchor + line, 1), encoding="utf-8")

print("WAIT_BETTER_ENTRY patch applied")
