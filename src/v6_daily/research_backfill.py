from __future__ import annotations

import math
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

import pandas as pd

from data_provider.yfinance_fetcher import YfinanceFetcher


RESEARCH_BACKFILL_VERSION = "v6.2-accuracy-history-backfill.1"
DEFAULT_HISTORY_YEARS = 3
DEFAULT_MINIMUM_BARS = 81
BENCHMARK_CODES = ("SPY", "QQQ")
_REQUIRED_STOCK_DAILY_COLUMNS = {"code", "date", "open", "high", "low", "close", "volume"}
_OPTIONAL_STOCK_DAILY_COLUMNS = (
    "amount",
    "pct_chg",
    "ma5",
    "ma10",
    "ma20",
    "volume_ratio",
    "data_source",
)


def _normalize_codes(codes: Sequence[str]) -> list[str]:
    return list(
        dict.fromkeys(
            str(code).strip().upper()
            for code in codes
            if str(code).strip()
        )
    )


def _readonly_connection(path: str | Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True, timeout=30)


def _quick_check(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA quick_check").fetchone()
    return str(row[0] if row else "").strip().lower()


def _stock_daily_columns(conn: sqlite3.Connection) -> set[str]:
    return {str(row[1]) for row in conn.execute("PRAGMA table_info(stock_daily)").fetchall()}


def _count_stock_daily(conn: sqlite3.Connection, code: Optional[str] = None) -> int:
    if code is None:
        return int(conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0])
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM stock_daily WHERE UPPER(code) = ?",
            (str(code).upper(),),
        ).fetchone()[0]
    )


def _date_bounds(conn: sqlite3.Connection, code: str) -> tuple[Optional[str], Optional[str]]:
    row = conn.execute(
        "SELECT MIN(date), MAX(date) FROM stock_daily WHERE UPPER(code) = ?",
        (str(code).upper(),),
    ).fetchone()
    if not row:
        return None, None
    return (
        None if row[0] is None else str(row[0]),
        None if row[1] is None else str(row[1]),
    )


def discover_target_codes(stock_db_path: str | Path) -> list[str]:
    """Discover non-benchmark symbols already present in the production cache."""
    with _readonly_connection(stock_db_path) as conn:
        if "stock_daily" not in {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }:
            return []
        rows = conn.execute(
            "SELECT DISTINCT UPPER(code) FROM stock_daily "
            "WHERE code IS NOT NULL AND TRIM(code) <> '' ORDER BY UPPER(code)"
        ).fetchall()
    return [str(row[0]) for row in rows if str(row[0]) not in BENCHMARK_CODES]


def clone_sqlite_database(source_db_path: str | Path, research_db_path: str | Path) -> None:
    """Create a consistent isolated SQLite clone without mutating the source DB."""
    source = Path(source_db_path).resolve()
    research = Path(research_db_path).resolve()
    if source == research:
        raise ValueError("research database must be different from source database")
    if not source.is_file():
        raise FileNotFoundError(f"source database does not exist: {source}")

    research.parent.mkdir(parents=True, exist_ok=True)
    if research.exists():
        research.unlink()

    with _readonly_connection(source) as src, sqlite3.connect(research, timeout=30) as dst:
        if _quick_check(src) != "ok":
            raise RuntimeError("source database quick_check failed")
        src.backup(dst)
        if _quick_check(dst) != "ok":
            raise RuntimeError("research database quick_check failed after clone")


def _coerce_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date().isoformat()


def _coerce_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _upsert_research_rows(
    conn: sqlite3.Connection,
    *,
    code: str,
    frame: pd.DataFrame,
) -> int:
    columns = _stock_daily_columns(conn)
    missing = _REQUIRED_STOCK_DAILY_COLUMNS - columns
    if missing:
        raise RuntimeError(f"stock_daily missing required columns: {sorted(missing)}")

    write_columns = ["code", "date", "open", "high", "low", "close", "volume"]
    write_columns.extend(name for name in _OPTIONAL_STOCK_DAILY_COLUMNS if name in columns)
    rows: list[tuple[Any, ...]] = []

    for _, item in frame.iterrows():
        trade_date = _coerce_date(item.get("date"))
        close = _coerce_float(item.get("close"))
        if not trade_date or close is None or close <= 0:
            continue
        values: Dict[str, Any] = {
            "code": str(code).upper(),
            "date": trade_date,
            "open": _coerce_float(item.get("open")),
            "high": _coerce_float(item.get("high")),
            "low": _coerce_float(item.get("low")),
            "close": close,
            "volume": _coerce_float(item.get("volume")),
            "amount": _coerce_float(item.get("amount")),
            "pct_chg": _coerce_float(item.get("pct_chg")),
            "ma5": _coerce_float(item.get("ma5")),
            "ma10": _coerce_float(item.get("ma10")),
            "ma20": _coerce_float(item.get("ma20")),
            "volume_ratio": _coerce_float(item.get("volume_ratio")),
            "data_source": "YfinanceFetcher:accuracy_research",
        }
        rows.append(tuple(values.get(name) for name in write_columns))

    if not rows:
        return 0

    placeholders = ",".join("?" for _ in write_columns)
    update_columns = [name for name in write_columns if name not in {"code", "date"}]
    update_clause = ",".join(f"{name}=excluded.{name}" for name in update_columns)
    sql = (
        f"INSERT INTO stock_daily ({','.join(write_columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(code,date) DO UPDATE SET {update_clause}"
    )
    conn.executemany(sql, rows)
    conn.commit()
    return len(rows)


def backfill_accuracy_research_history(
    source_db_path: str | Path,
    research_db_path: str | Path,
    *,
    codes: Optional[Sequence[str]] = None,
    history_years: int = DEFAULT_HISTORY_YEARS,
    minimum_bars: int = DEFAULT_MINIMUM_BARS,
    fetcher: Optional[Any] = None,
    as_of: Optional[date] = None,
) -> Dict[str, Any]:
    """Clone production history and backfill an isolated research database.

    The source database is always opened read-only. Historical downloads are written
    only to the cloned research DB, so weekly replay can obtain sufficient OHLCV
    history without polluting the production cache or V6 live-outcome history.
    """
    source = Path(source_db_path).resolve()
    research = Path(research_db_path).resolve()
    history_years_i = max(1, int(history_years))
    minimum_bars_i = max(1, int(minimum_bars))
    target_codes = _normalize_codes(codes or discover_target_codes(source))
    target_codes = [code for code in target_codes if code not in BENCHMARK_CODES]
    if not target_codes:
        raise ValueError("no research target codes were provided or discovered")

    all_codes = _normalize_codes([*target_codes, *BENCHMARK_CODES])
    end_day = as_of or date.today()
    start_day = end_day - timedelta(days=history_years_i * 366)
    yahoo_end_day = end_day + timedelta(days=1)

    with _readonly_connection(source) as conn:
        source_rows_before = _count_stock_daily(conn)
        source_quick_check_before = _quick_check(conn)
    if source_quick_check_before != "ok":
        raise RuntimeError(f"source database quick_check failed: {source_quick_check_before!r}")

    clone_sqlite_database(source, research)
    provider = fetcher or YfinanceFetcher()
    symbol_stats: Dict[str, Dict[str, Any]] = {}
    errors: list[Dict[str, str]] = []

    with sqlite3.connect(research, timeout=30) as conn:
        if _quick_check(conn) != "ok":
            raise RuntimeError("research database quick_check failed before backfill")
        for code in all_codes:
            before = _count_stock_daily(conn, code)
            fetched_rows = 0
            written_rows = 0
            error: Optional[str] = None
            try:
                frame = provider.get_daily_data(
                    code,
                    start_date=start_day.isoformat(),
                    end_date=yahoo_end_day.isoformat(),
                )
                fetched_rows = 0 if frame is None else int(len(frame))
                if frame is None or frame.empty:
                    raise RuntimeError("provider returned no historical rows")
                written_rows = _upsert_research_rows(conn, code=code, frame=frame)
            except Exception as exc:  # individual symbols are reported, not hidden
                error = f"{type(exc).__name__}: {exc}"
                errors.append({"code": code, "error": error})

            after = _count_stock_daily(conn, code)
            first_date, last_date = _date_bounds(conn, code)
            symbol_stats[code] = {
                "rows_before": before,
                "rows_after": after,
                "fetched_rows": fetched_rows,
                "written_rows": written_rows,
                "first_date": first_date,
                "last_date": last_date,
                "minimum_bars": minimum_bars_i,
                "eligible": after >= minimum_bars_i,
                "error": error,
            }

        research_rows = _count_stock_daily(conn)
        research_quick_check = _quick_check(conn)

    with _readonly_connection(source) as conn:
        source_rows_after = _count_stock_daily(conn)
        source_quick_check_after = _quick_check(conn)

    eligible_targets = [
        code for code in target_codes if bool(symbol_stats.get(code, {}).get("eligible"))
    ]
    ineligible_targets = [code for code in target_codes if code not in eligible_targets]
    benchmark_ready = {
        code: bool(symbol_stats.get(code, {}).get("eligible")) for code in BENCHMARK_CODES
    }
    source_unchanged = (
        source_rows_before == source_rows_after
        and source_quick_check_before == "ok"
        and source_quick_check_after == "ok"
    )
    usable = bool(eligible_targets) and all(benchmark_ready.values()) and research_quick_check == "ok"
    if not usable:
        status = "insufficient_data"
    elif errors or ineligible_targets:
        status = "partial"
    else:
        status = "ok"

    return {
        "version": RESEARCH_BACKFILL_VERSION,
        "status": status,
        "source_database": str(source),
        "research_database": str(research),
        "source_read_only": True,
        "source_unchanged": source_unchanged,
        "source_rows_before": source_rows_before,
        "source_rows_after": source_rows_after,
        "research_rows": research_rows,
        "research_quick_check": research_quick_check,
        "history_years": history_years_i,
        "minimum_bars": minimum_bars_i,
        "window_start": start_day.isoformat(),
        "window_end_inclusive": end_day.isoformat(),
        "targets": target_codes,
        "benchmarks": list(BENCHMARK_CODES),
        "eligible_targets": eligible_targets,
        "ineligible_targets": ineligible_targets,
        "eligible_target_count": len(eligible_targets),
        "benchmark_ready": benchmark_ready,
        "symbol_stats": symbol_stats,
        "errors": errors,
    }
