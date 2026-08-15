from __future__ import annotations

import argparse
import itertools
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

try:
    from scripts.run_us_open_confirmation import LiveSnapshot, classify_confirmation
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from run_us_open_confirmation import LiveSnapshot, classify_confirmation


NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class PlanCandidate:
    source: str
    symbol: str
    effective_trade_date: str
    created_at: str
    packet: dict[str, Any]


@dataclass(frozen=True)
class Observation:
    source: str
    symbol: str
    plan_date: str
    session_date: str
    created_at: str
    snapshot: LiveSnapshot
    packet: dict[str, Any]
    close_return_pct: float
    return_60m_pct: float | None
    mfe_pct: float
    mae_pct: float
    stop_hit: bool
    target1_hit: bool
    opening_range_position: float | None


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=NY)
    return parsed.astimezone(NY)


def _load_final_candidates(conn: sqlite3.Connection) -> list[PlanCandidate]:
    rows = conn.execute(
        """
        SELECT symbol, effective_trade_date, persisted_at, packet_json
        FROM v6_final_decisions
        WHERE effective_trade_date IS NOT NULL AND packet_json IS NOT NULL
        ORDER BY persisted_at, id
        """
    ).fetchall()
    result: list[PlanCandidate] = []
    for row in rows:
        packet = json.loads(row["packet_json"])
        result.append(
            PlanCandidate(
                source="final_fusion",
                symbol=str(row["symbol"]).upper(),
                effective_trade_date=str(row["effective_trade_date"]),
                created_at=str(row["persisted_at"]),
                packet=packet,
            )
        )
    return result


def _load_deterministic_candidates(conn: sqlite3.Connection) -> list[PlanCandidate]:
    rows = conn.execute(
        """
        SELECT f.symbol, f.effective_trade_date, f.v6_created_at,
               e.plan_json, e.has_active_plan
        FROM v6_decision_runs d
        JOIN v6_forecast_runs f ON f.id=d.forecast_run_id
        JOIN v6_execution_plans e ON e.decision_run_id=d.id
        WHERE f.effective_trade_date IS NOT NULL
          AND e.has_active_plan=1
        ORDER BY f.v6_created_at, d.id
        """
    ).fetchall()
    result: list[PlanCandidate] = []
    for row in rows:
        plan = json.loads(row["plan_json"])
        packet = {
            "identity": {
                "symbol": str(row["symbol"]).upper(),
                "instrument_type": "STOCK",
                "effective_trade_date": str(row["effective_trade_date"]),
            },
            # This expanded sample intentionally isolates the intraday filter.
            # The real production path still requires final-fusion worth_buying=true.
            "assessment": {
                "verdict": "conditional_buy",
                "worth_buying": True,
                "execution_authorized": False,
            },
            "execution": plan,
        }
        result.append(
            PlanCandidate(
                source="deterministic_active_plan",
                symbol=str(row["symbol"]).upper(),
                effective_trade_date=str(row["effective_trade_date"]),
                created_at=str(row["v6_created_at"]),
                packet=packet,
            )
        )
    return result


def load_candidates(path: str | Path) -> list[PlanCandidate]:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        required = {
            "v6_final_decisions",
            "v6_decision_runs",
            "v6_forecast_runs",
            "v6_execution_plans",
        }
        missing = sorted(required - tables)
        if missing:
            raise RuntimeError(f"V6 database missing required tables: {missing}")
        return _load_final_candidates(conn) + _load_deterministic_candidates(conn)
    finally:
        conn.close()


def _normalize_frame(frame: Any) -> Any:
    if frame is None or frame.empty:
        return frame
    index = frame.index
    if getattr(index, "tz", None) is None:
        frame.index = index.tz_localize("UTC").tz_convert(NY)
    else:
        frame.index = index.tz_convert(NY)
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    return frame


def fetch_history(symbol: str, start: date, end: date) -> Any:
    import pandas as pd
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    chunks: list[Any] = []
    cursor = start
    # Yahoo may reject a single long 1m request. Small calendar chunks stay
    # inside its intraday range limits while still covering several sessions.
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=5), end)
        frame = ticker.history(
            start=cursor.isoformat(),
            end=chunk_end.isoformat(),
            interval="1m",
            auto_adjust=False,
            prepost=False,
            actions=False,
        )
        if frame is not None and not frame.empty:
            chunks.append(frame)
        cursor = chunk_end
    if not chunks:
        raise RuntimeError(f"{symbol}: no historical 1m bars")
    return _normalize_frame(pd.concat(chunks))


def _regular_session(frame: Any, session_date: date) -> Any:
    session = frame[frame.index.date == session_date]
    if session.empty:
        return session
    return session.between_time("09:30", "16:00")


def _opening_window(frame: Any, session_date: date) -> Any:
    session = _regular_session(frame, session_date)
    if session.empty:
        return session
    return session.between_time("09:30", "09:44")


def _next_session_date(frame: Any, plan_date: date) -> date | None:
    dates = sorted(
        {
            item
            for item in frame.index.date
            if item > plan_date and not _regular_session(frame, item).empty
        }
    )
    return dates[0] if dates else None


def _latest_causal_candidate(
    candidates: Sequence[PlanCandidate],
    *,
    session_date: date,
) -> PlanCandidate | None:
    open_dt = datetime.combine(session_date, time(9, 30), tzinfo=NY)
    causal = [item for item in candidates if _parse_datetime(item.created_at) < open_dt]
    if not causal:
        return None
    return max(causal, key=lambda item: _parse_datetime(item.created_at))


def _volume_ratio(frame: Any, session_date: date, current_volume: float) -> tuple[float | None, float | None]:
    prior: list[float] = []
    prior_dates = sorted({item for item in frame.index.date if item < session_date}, reverse=True)
    for prior_date in prior_dates[:8]:
        opening = _opening_window(frame, prior_date)
        if len(opening) < 12:
            continue
        volume = _finite(opening["Volume"].fillna(0).sum())
        if volume is not None and volume > 0:
            prior.append(volume)
        if len(prior) >= 4:
            break
    if not prior:
        return None, None
    baseline = median(prior)
    return baseline, current_volume / baseline if baseline > 0 else None


def build_observation(candidate: PlanCandidate, frame: Any, session_date: date) -> Observation | None:
    session = _regular_session(frame, session_date)
    opening = _opening_window(frame, session_date)
    if session.empty or len(opening) < 12:
        return None

    first = opening.iloc[0]
    last = opening.iloc[-1]
    price = _finite(last.get("Close"))
    session_open = _finite(first.get("Open"))
    if price is None or session_open is None or price <= 0 or session_open <= 0:
        return None

    opening_volume = float(opening["Volume"].fillna(0).sum())
    prior_median, volume_ratio = _volume_ratio(frame, session_date, opening_volume)
    opening_high = float(opening["High"].max())
    opening_low = float(opening["Low"].min())
    opening_range = opening_high - opening_low
    range_position = (
        (price - opening_low) / opening_range
        if opening_range > 1e-12
        else 0.5
    )

    snapshot = LiveSnapshot(
        symbol=candidate.symbol,
        current_price=price,
        session_open=session_open,
        session_high=opening_high,
        session_low=opening_low,
        opening_15m_high=opening_high,
        opening_15m_low=opening_low,
        return_from_open_pct=(price / session_open - 1.0) * 100.0,
        opening_15m_volume=opening_volume,
        recent_opening_volume_median=prior_median,
        volume_ratio=volume_ratio,
        bar_count=int(len(opening)),
        last_bar_time=opening.index[-1].isoformat(),
    )

    future = session[session.index > opening.index[-1]]
    if future.empty:
        return None
    close_price = _finite(session.iloc[-1].get("Close"))
    if close_price is None or close_price <= 0:
        return None

    future_high = float(future["High"].max())
    future_low = float(future["Low"].min())
    close_return = (close_price / price - 1.0) * 100.0
    mfe = (future_high / price - 1.0) * 100.0
    mae = (future_low / price - 1.0) * 100.0

    sixty_cutoff = opening.index[-1] + timedelta(minutes=60)
    first_hour = future[future.index <= sixty_cutoff]
    return_60m = None
    if not first_hour.empty:
        hour_price = _finite(first_hour.iloc[-1].get("Close"))
        if hour_price is not None and hour_price > 0:
            return_60m = (hour_price / price - 1.0) * 100.0

    execution = _mapping(candidate.packet.get("execution"))
    stop = _finite(execution.get("stop_loss"))
    targets = [
        value
        for value in (_finite(item) for item in execution.get("targets") or [])
        if value is not None and value > 0
    ]
    return Observation(
        source=candidate.source,
        symbol=candidate.symbol,
        plan_date=candidate.effective_trade_date,
        session_date=session_date.isoformat(),
        created_at=candidate.created_at,
        snapshot=snapshot,
        packet=candidate.packet,
        close_return_pct=close_return,
        return_60m_pct=return_60m,
        mfe_pct=mfe,
        mae_pct=mae,
        stop_hit=bool(stop is not None and future_low <= stop),
        target1_hit=bool(targets and future_high >= targets[0]),
        opening_range_position=range_position,
    )


def build_observations(candidates: Sequence[PlanCandidate]) -> list[Observation]:
    if not candidates:
        return []
    by_symbol: dict[str, list[PlanCandidate]] = {}
    for candidate in candidates:
        by_symbol.setdefault(candidate.symbol, []).append(candidate)

    min_date = min(date.fromisoformat(item.effective_trade_date) for item in candidates)
    max_date = max(date.fromisoformat(item.effective_trade_date) for item in candidates)
    start = min_date - timedelta(days=8)
    end = max(date.today(), max_date + timedelta(days=8)) + timedelta(days=1)

    observations: list[Observation] = []
    for symbol, symbol_candidates in sorted(by_symbol.items()):
        frame = fetch_history(symbol, start, end)
        grouped: dict[tuple[str, str], list[PlanCandidate]] = {}
        for item in symbol_candidates:
            grouped.setdefault((item.source, item.effective_trade_date), []).append(item)

        for (_, plan_date_text), group in sorted(grouped.items()):
            plan_date = date.fromisoformat(plan_date_text)
            session_date = _next_session_date(frame, plan_date)
            if session_date is None:
                continue
            candidate = _latest_causal_candidate(group, session_date=session_date)
            if candidate is None:
                continue
            observation = build_observation(candidate, frame, session_date)
            if observation is not None:
                observations.append(observation)
    return observations


def _avg(values: Iterable[float]) -> float | None:
    rows = list(values)
    return mean(rows) if rows else None


def evaluate(
    observations: Sequence[Observation],
    *,
    chase_tolerance_pct: float,
    weak_open_pct: float,
    min_volume_ratio: float,
    min_opening_range_position: float = 0.0,
) -> dict[str, Any]:
    decisions: list[tuple[Observation, Any]] = []
    statuses: dict[str, int] = {}
    for observation in observations:
        decision = classify_confirmation(
            observation.packet,
            observation.snapshot,
            chase_tolerance_pct=chase_tolerance_pct,
            weak_open_pct=weak_open_pct,
            min_volume_ratio=min_volume_ratio,
        )
        # Optional research-only filter. This is not active in production unless
        # a later tuning change explicitly promotes it.
        if (
            decision.status == "BUY_NOW"
            and observation.opening_range_position is not None
            and observation.opening_range_position < min_opening_range_position
        ):
            status = "WAIT_OPENING_RANGE"
        else:
            status = decision.status
        statuses[status] = statuses.get(status, 0) + 1
        if status == "BUY_NOW":
            decisions.append((observation, decision))

    buys = [item[0] for item in decisions]
    close_returns = [item.close_return_pct for item in buys]
    hour_returns = [item.return_60m_pct for item in buys if item.return_60m_pct is not None]
    win_rate = (
        sum(value > 0 for value in close_returns) / len(close_returns)
        if close_returns
        else None
    )
    stop_rate = sum(item.stop_hit for item in buys) / len(buys) if buys else None
    target_rate = sum(item.target1_hit for item in buys) / len(buys) if buys else None
    avg_close = _avg(close_returns)
    avg_mae = _avg(item.mae_pct for item in buys)
    # A simple risk-adjusted diagnostic only; never auto-promote parameters on
    # a tiny sample. Higher is better, with adverse excursion penalized.
    diagnostic_score = None
    if avg_close is not None and avg_mae is not None:
        diagnostic_score = avg_close - 0.35 * abs(avg_mae)

    return {
        "params": {
            "chase_tolerance_pct": chase_tolerance_pct,
            "weak_open_pct": weak_open_pct,
            "min_volume_ratio": min_volume_ratio,
            "min_opening_range_position": min_opening_range_position,
        },
        "observations": len(observations),
        "buy_count": len(buys),
        "buy_rate": len(buys) / len(observations) if observations else 0.0,
        "status_counts": statuses,
        "win_rate_close": win_rate,
        "avg_close_return_pct": avg_close,
        "median_close_return_pct": median(close_returns) if close_returns else None,
        "avg_60m_return_pct": _avg(hour_returns),
        "avg_mfe_pct": _avg(item.mfe_pct for item in buys),
        "avg_mae_pct": avg_mae,
        "stop_hit_rate": stop_rate,
        "target1_hit_rate": target_rate,
        "diagnostic_score": diagnostic_score,
        "buys": [
            {
                "source": item.source,
                "symbol": item.symbol,
                "plan_date": item.plan_date,
                "session_date": item.session_date,
                "signal_price": item.snapshot.current_price,
                "return_from_open_pct": item.snapshot.return_from_open_pct,
                "volume_ratio": item.snapshot.volume_ratio,
                "opening_range_position": item.opening_range_position,
                "close_return_pct": item.close_return_pct,
                "return_60m_pct": item.return_60m_pct,
                "mfe_pct": item.mfe_pct,
                "mae_pct": item.mae_pct,
                "stop_hit": item.stop_hit,
                "target1_hit": item.target1_hit,
            }
            for item in buys
        ],
    }


def tune(observations: Sequence[Observation]) -> dict[str, Any]:
    baseline = evaluate(
        observations,
        chase_tolerance_pct=0.5,
        weak_open_pct=-0.75,
        min_volume_ratio=0.55,
        min_opening_range_position=0.0,
    )
    rows: list[dict[str, Any]] = []
    for chase, weak, volume, range_position in itertools.product(
        (0.0, 0.25, 0.5, 0.75, 1.0),
        (-0.25, -0.5, -0.75, -1.0, -1.25),
        (0.4, 0.55, 0.7, 0.85),
        (0.0, 0.25, 0.5, 0.75),
    ):
        rows.append(
            evaluate(
                observations,
                chase_tolerance_pct=chase,
                weak_open_pct=weak,
                min_volume_ratio=volume,
                min_opening_range_position=range_position,
            )
        )

    baseline_buys = int(baseline["buy_count"])
    minimum_buys = max(3, math.ceil(baseline_buys * 0.6)) if baseline_buys else 3
    eligible = [row for row in rows if row["buy_count"] >= minimum_buys]
    eligible.sort(
        key=lambda row: (
            row["diagnostic_score"] if row["diagnostic_score"] is not None else -999.0,
            row["win_rate_close"] if row["win_rate_close"] is not None else -1.0,
            row["avg_close_return_pct"] if row["avg_close_return_pct"] is not None else -999.0,
            row["buy_count"],
        ),
        reverse=True,
    )
    return {
        "baseline": baseline,
        "minimum_buys_for_ranking": minimum_buys,
        "grid_size": len(rows),
        "top": eligible[:12],
    }


def _round_floats(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, list):
        return [_round_floats(item) for item in value]
    if isinstance(value, dict):
        return {key: _round_floats(item) for key, item in value.items()}
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest/tune the U.S. open +15m confirmation rules")
    parser.add_argument("--v6-db", required=True)
    parser.add_argument("--output", default="open_confirmation_backtest.json")
    args = parser.parse_args()

    candidates = load_candidates(args.v6_db)
    observations = build_observations(candidates)
    if not observations:
        raise SystemExit("no causal observations with historical minute bars")

    by_source: dict[str, list[Observation]] = {}
    for observation in observations:
        by_source.setdefault(observation.source, []).append(observation)

    result: dict[str, Any] = {
        "version": "us-open-backtest-v1",
        "generated_at": datetime.now(NY).isoformat(),
        "observation_count": len(observations),
        "sources": {},
    }
    for source, source_rows in sorted(by_source.items()):
        result["sources"][source] = {
            "observation_count": len(source_rows),
            "session_dates": sorted({item.session_date for item in source_rows}),
            "symbols": sorted({item.symbol for item in source_rows}),
            "tuning": tune(source_rows),
        }

    final_rows = by_source.get("final_fusion", [])
    # Never claim statistical promotion from a handful of days. The threshold
    # is intentionally conservative so the daily collector can accumulate
    # evidence before production defaults change automatically.
    result["promotion_gate"] = {
        "required_final_observations": 20,
        "actual_final_observations": len(final_rows),
        "eligible": len(final_rows) >= 20,
        "reason": (
            "enough final-fusion observations for parameter promotion"
            if len(final_rows) >= 20
            else "insufficient final-fusion history; use results diagnostically and keep collecting"
        ),
    }
    result = _round_floats(result)
    Path(args.output).write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
