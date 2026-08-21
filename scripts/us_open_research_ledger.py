from __future__ import annotations

import argparse
import json
import math
import sqlite3
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
SCHEMA_VERSION = "us-open-research-ledger-v2"


def _json(value: Any) -> str:
    if is_dataclass(value):
        value = asdict(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_dt(value: Any) -> datetime | None:
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


def connect(path: str | Path) -> sqlite3.Connection:
    db = Path(path)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS us_open_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            schema_version TEXT NOT NULL,
            signal_key TEXT NOT NULL UNIQUE,
            session_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            source_run_id TEXT,
            source_trade_date TEXT,
            evaluated_at TEXT NOT NULL,
            signal_bar_time TEXT NOT NULL,
            signal_price REAL NOT NULL,
            decision_status TEXT NOT NULL,
            packet_json TEXT NOT NULL,
            snapshot_json TEXT NOT NULL,
            decision_json TEXT NOT NULL,
            settled_at TEXT,
            close_return_pct REAL,
            return_60m_pct REAL,
            mfe_pct REAL,
            mae_pct REAL,
            stop_hit INTEGER,
            target1_hit INTEGER,
            first_touch TEXT,
            modeled_exit_return_pct REAL,
            better_entry_hit INTEGER,
            best_future_improvement_pct REAL,
            minutes_to_reference_better_price REAL,
            outcome_json TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_us_open_signals_date_symbol
            ON us_open_signals(session_date, symbol, id);
        CREATE INDEX IF NOT EXISTS ix_us_open_signals_status_settled
            ON us_open_signals(decision_status, settled_at, session_date);
        """
    )
    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(us_open_signals)")
    }
    for name, ddl in {
        "better_entry_hit": "INTEGER",
        "best_future_improvement_pct": "REAL",
        "minutes_to_reference_better_price": "REAL",
    }.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE us_open_signals ADD COLUMN {name} {ddl}")
    return conn

def signal_key(
    *,
    session_date: str,
    symbol: str,
    policy_version: str,
    source_run_id: str | None,
    signal_bar_time: str | None = None,
) -> str:
    return "|".join(
        [
            session_date,
            symbol.strip().upper(),
            policy_version.strip(),
            str(source_run_id or "").strip(),
            str(signal_bar_time or "").strip(),
        ]
    )

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
    identity = packet.get("identity") if isinstance(packet.get("identity"), Mapping) else {}
    symbol = str(identity.get("symbol") or decision.get("symbol") or "").strip().upper()
    bar_time = _parse_dt(snapshot.get("last_bar_time") or decision.get("source_last_bar_time"))
    signal_price = _finite(snapshot.get("current_price") or decision.get("current_price"))
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
    with connect(path) as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO us_open_signals(
                schema_version, signal_key, session_date, symbol,
                policy_version, source_run_id, source_trade_date,
                evaluated_at, signal_bar_time, signal_price,
                decision_status, packet_json, snapshot_json, decision_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
            ),
        )
        return cursor.rowcount > 0


def _normalize_frame(frame: Any) -> Any:
    if frame is None or frame.empty:
        return frame
    index = frame.index
    if getattr(index, "tz", None) is None:
        frame.index = index.tz_localize("UTC").tz_convert(NY)
    else:
        frame.index = index.tz_convert(NY)
    return frame[~frame.index.duplicated(keep="last")].sort_index()


def fetch_session_history(symbol: str, session_date: date) -> Any:
    import yfinance as yf

    frame = yf.Ticker(symbol).history(
        start=session_date.isoformat(),
        end=(session_date + timedelta(days=1)).isoformat(),
        interval="1m",
        auto_adjust=False,
        prepost=False,
        actions=False,
    )
    frame = _normalize_frame(frame)
    if frame is None or frame.empty:
        raise RuntimeError(f"{symbol}: no 1m history for {session_date}")
    frame = frame[frame.index.date == session_date]
    return frame.between_time("09:30", "16:00")


def _execution_levels(packet: Mapping[str, Any]) -> tuple[float | None, float | None]:
    execution = packet.get("execution") if isinstance(packet.get("execution"), Mapping) else {}
    stop = _finite(execution.get("stop_loss"))
    targets = [
        value
        for value in (_finite(item) for item in (execution.get("targets") or []))
        if value is not None and value > 0
    ]
    return stop, (targets[0] if targets else None)


def compute_outcome(row: Mapping[str, Any], frame: Any) -> dict[str, Any] | None:
    frame = _normalize_frame(frame)
    if frame is None or frame.empty:
        return None
    signal_time = _parse_dt(row.get("signal_bar_time"))
    signal_price = _finite(row.get("signal_price"))
    if signal_time is None or signal_price is None or signal_price <= 0:
        return None
    session = frame[frame.index.date == signal_time.date()].between_time("09:30", "16:00")
    future = session[session.index > signal_time]
    if future.empty:
        return None
    close_price = _finite(session.iloc[-1].get("Close"))
    if close_price is None or close_price <= 0:
        return None

    future_high = float(future["High"].max())
    future_low = float(future["Low"].min())
    close_return = (close_price / signal_price - 1.0) * 100.0
    mfe = (future_high / signal_price - 1.0) * 100.0
    mae = (future_low / signal_price - 1.0) * 100.0

    cutoff = signal_time + timedelta(minutes=60)
    first_hour = future[future.index <= cutoff]
    return_60m = None
    if not first_hour.empty:
        hour_price = _finite(first_hour.iloc[-1].get("Close"))
        if hour_price is not None and hour_price > 0:
            return_60m = (hour_price / signal_price - 1.0) * 100.0

    try:
        packet = json.loads(str(row.get("packet_json") or "{}"))
    except json.JSONDecodeError:
        packet = {}
    try:
        decision = json.loads(str(row.get("decision_json") or "{}"))
    except json.JSONDecodeError:
        decision = {}
    stop, target1 = _execution_levels(packet)
    reference_better_price = _finite(decision.get("expected_better_price"))
    wait_minutes_raw = _finite(decision.get("expected_wait_minutes"))
    if wait_minutes_raw is None:
        wait_minutes_raw = _finite(decision.get("recheck_minutes"))
    wait_minutes = int(max(1.0, min(120.0, wait_minutes_raw or 30.0)))
    wait_cutoff = signal_time + timedelta(minutes=wait_minutes)
    wait_future = future[future.index <= wait_cutoff]
    wait_low = float(wait_future["Low"].min()) if not wait_future.empty else None
    best_future_improvement = (
        None
        if wait_low is None
        else max(0.0, (signal_price - wait_low) / signal_price * 100.0)
    )
    better_entry_hit = None
    minutes_to_reference = None
    if reference_better_price is not None and 0 < reference_better_price < signal_price:
        matching = wait_future[wait_future["Low"] <= reference_better_price]
        better_entry_hit = not matching.empty
        if better_entry_hit:
            first_time = matching.index[0]
            minutes_to_reference = max(
                0.0, (first_time - signal_time).total_seconds() / 60.0
            )

    stop_hit = False
    target1_hit = False
    first_touch = "close"
    modeled_exit = close_return
    for _, bar in future.iterrows():
        low = _finite(bar.get("Low"))
        high = _finite(bar.get("High"))
        stop_here = bool(stop is not None and low is not None and low <= stop)
        target_here = bool(target1 is not None and high is not None and high >= target1)
        stop_hit = stop_hit or stop_here
        target1_hit = target1_hit or target_here
        if stop_here and target_here:
            first_touch = "ambiguous_stop_target_same_bar"
            modeled_exit = None
            break
        if stop_here:
            first_touch = "stop"
            modeled_exit = (
                (stop / signal_price - 1.0) * 100.0 if stop is not None else None
            )
            break
        if target_here:
            first_touch = "target1"
            modeled_exit = (
                (target1 / signal_price - 1.0) * 100.0
                if target1 is not None
                else None
            )
            break

    return {
        "close_return_pct": close_return,
        "return_60m_pct": return_60m,
        "mfe_pct": mfe,
        "mae_pct": mae,
        "stop_hit": stop_hit,
        "target1_hit": target1_hit,
        "first_touch": first_touch,
        "modeled_exit_return_pct": modeled_exit,
        "better_entry_hit": better_entry_hit,
        "best_future_improvement_pct": best_future_improvement,
        "minutes_to_reference_better_price": minutes_to_reference,
        "expected_wait_minutes": wait_minutes,
        "wait_window_end": wait_cutoff.isoformat(),
    }

def settle_pending(
    path: str | Path,
    *,
    as_of_date: date,
    history_fetcher: Callable[[str, date], Any] = fetch_session_history,
) -> dict[str, int]:
    settled = 0
    failed = 0
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM us_open_signals
            WHERE settled_at IS NULL AND session_date < ?
            ORDER BY session_date, symbol, id
            """,
            (as_of_date.isoformat(),),
        ).fetchall()
        for raw in rows:
            row = dict(raw)
            try:
                session_date = date.fromisoformat(str(row["session_date"]))
                frame = history_fetcher(str(row["symbol"]), session_date)
                outcome = compute_outcome(row, frame)
                if outcome is None:
                    failed += 1
                    continue
                conn.execute(
                    """
                    UPDATE us_open_signals
                    SET settled_at=?, close_return_pct=?, return_60m_pct=?,
                        mfe_pct=?, mae_pct=?, stop_hit=?, target1_hit=?,
                        first_touch=?, modeled_exit_return_pct=?,
                        better_entry_hit=?, best_future_improvement_pct=?,
                        minutes_to_reference_better_price=?, outcome_json=?
                    WHERE id=?
                    """,
                    (
                        datetime.now(NY).isoformat(),
                        outcome["close_return_pct"],
                        outcome["return_60m_pct"],
                        outcome["mfe_pct"],
                        outcome["mae_pct"],
                        int(bool(outcome["stop_hit"])),
                        int(bool(outcome["target1_hit"])),
                        outcome["first_touch"],
                        outcome["modeled_exit_return_pct"],
                        (
                            None
                            if outcome["better_entry_hit"] is None
                            else int(bool(outcome["better_entry_hit"]))
                        ),
                        outcome["best_future_improvement_pct"],
                        outcome["minutes_to_reference_better_price"],
                        _json(outcome),
                        int(row["id"]),
                    ),
                )
                settled += 1
            except Exception:
                failed += 1
    return {
        "settled": settled,
        "failed": failed,
        "pending_scanned": settled + failed,
    }

def summary(path: str | Path) -> dict[str, Any]:
    with connect(path) as conn:
        total = int(conn.execute("SELECT COUNT(*) FROM us_open_signals").fetchone()[0])
        settled = int(
            conn.execute(
                "SELECT COUNT(*) FROM us_open_signals WHERE settled_at IS NOT NULL"
            ).fetchone()[0]
        )
        buy_rows = conn.execute(
            """
            SELECT close_return_pct, return_60m_pct, mae_pct,
                   modeled_exit_return_pct
            FROM us_open_signals
            WHERE decision_status='BUY_NOW' AND settled_at IS NOT NULL
            ORDER BY session_date, symbol, id
            """
        ).fetchall()
        wait_rows = conn.execute(
            """
            SELECT better_entry_hit, best_future_improvement_pct,
                   minutes_to_reference_better_price
            FROM us_open_signals
            WHERE decision_status='WAIT_BETTER_ENTRY' AND settled_at IS NOT NULL
            ORDER BY session_date, symbol, id
            """
        ).fetchall()
    close_returns = [float(row[0]) for row in buy_rows if row[0] is not None]
    hour_returns = [float(row[1]) for row in buy_rows if row[1] is not None]
    maes = [float(row[2]) for row in buy_rows if row[2] is not None]
    modeled = [float(row[3]) for row in buy_rows if row[3] is not None]
    wait_hits = [int(row[0]) for row in wait_rows if row[0] is not None]
    wait_improvements = [float(row[1]) for row in wait_rows if row[1] is not None]
    wait_minutes = [float(row[2]) for row in wait_rows if row[2] is not None]
    return {
        "schema_version": SCHEMA_VERSION,
        "signals": total,
        "settled": settled,
        "pending": total - settled,
        "settled_buy_now": len(buy_rows),
        "buy_close_win_rate": (
            sum(value > 0 for value in close_returns) / len(close_returns)
            if close_returns
            else None
        ),
        "buy_avg_close_return_pct": mean(close_returns) if close_returns else None,
        "buy_avg_60m_return_pct": mean(hour_returns) if hour_returns else None,
        "buy_avg_mae_pct": mean(maes) if maes else None,
        "buy_avg_modeled_exit_return_pct": mean(modeled) if modeled else None,
        "settled_wait_better_entry": len(wait_rows),
        "wait_better_entry_hit_rate": (
            sum(wait_hits) / len(wait_hits) if wait_hits else None
        ),
        "wait_avg_best_future_improvement_pct": (
            mean(wait_improvements) if wait_improvements else None
        ),
        "wait_avg_minutes_to_reference_better_price": (
            mean(wait_minutes) if wait_minutes else None
        ),
        "better_entry_metric_status": (
            "collecting_outcomes"
            if len(wait_rows) < 30
            else "eligible_for_calibration_research"
        ),
    }

def export_summary(path: str | Path, output: str | Path) -> dict[str, Any]:
    payload = summary(path)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Persistent research ledger for US-open confirmation signals")
    parser.add_argument("--db", required=True)
    parser.add_argument("--summary-output")
    parser.add_argument("--settle-before", help="Settle pending sessions older than YYYY-MM-DD")
    args = parser.parse_args()
    if args.settle_before:
        result = settle_pending(args.db, as_of_date=date.fromisoformat(args.settle_before))
        print(json.dumps(result, sort_keys=True))
    payload = summary(args.db)
    if args.summary_output:
        export_summary(args.db, args.summary_output)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
