from __future__ import annotations

import math
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional, Sequence

import pandas as pd

from data_provider.yfinance_fetcher import YfinanceFetcher


BENCHMARK_CODES = ("SPY", "QQQ")
DEFAULT_HISTORY_DAYS = 730
REQUIRED_COLUMNS = {"code", "date", "open", "high", "low", "close", "volume"}
OPTIONAL_COLUMNS = (
    "amount",
    "pct_chg",
    "ma5",
    "ma10",
    "ma20",
    "volume_ratio",
    "data_source",
)


def _quick_check(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA quick_check").fetchone()
    return str(row[0] if row else "").strip().lower()


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(stock_daily)").fetchall()
    }


def _count(conn: sqlite3.Connection, code: str) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM stock_daily WHERE UPPER(code)=?",
            (str(code).upper(),),
        ).fetchone()[0]
    )


def _coerce_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
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


def _clone_sqlite(source: Path, target: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    source = source.resolve()
    target = target.resolve()
    if source == target:
        raise ValueError("benchmark settlement DB must differ from source DB")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    with sqlite3.connect(
        f"file:{source}?mode=ro", uri=True, timeout=30
    ) as src, sqlite3.connect(target, timeout=30) as dst:
        if _quick_check(src) != "ok":
            raise RuntimeError("source stock database quick_check failed")
        src.backup(dst)
        if _quick_check(dst) != "ok":
            raise RuntimeError("benchmark clone quick_check failed")


def _upsert_frame(
    conn: sqlite3.Connection,
    *,
    code: str,
    frame: pd.DataFrame,
) -> int:
    columns = _columns(conn)
    missing = REQUIRED_COLUMNS - columns
    if missing:
        raise RuntimeError(
            f"stock_daily missing required benchmark columns: {sorted(missing)}"
        )
    write_columns = [
        "code",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]
    write_columns.extend(
        name for name in OPTIONAL_COLUMNS if name in columns
    )
    rows: list[tuple[Any, ...]] = []
    for _, item in frame.iterrows():
        trade_date = _coerce_date(item.get("date"))
        close = _coerce_float(item.get("close"))
        if not trade_date or close is None or close <= 0:
            continue
        values = {
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
            "data_source": "YfinanceFetcher:v6_benchmark_settlement",
        }
        rows.append(tuple(values.get(name) for name in write_columns))
    if not rows:
        return 0

    placeholders = ",".join("?" for _ in write_columns)
    update_columns = [
        name for name in write_columns if name not in {"code", "date"}
    ]
    update_clause = ",".join(
        f"{name}=excluded.{name}" for name in update_columns
    )
    conn.executemany(
        f"INSERT INTO stock_daily ({','.join(write_columns)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT(code,date) DO UPDATE SET {update_clause}",
        rows,
    )
    conn.commit()
    return len(rows)


def prepare_benchmark_settlement_db(
    source_db_path: str | Path,
    settlement_db_path: str | Path,
    *,
    benchmark_codes: Sequence[str] = BENCHMARK_CODES,
    history_days: int = DEFAULT_HISTORY_DAYS,
    minimum_bars: int = 81,
    fetcher: Any | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Clone the production stock DB and hydrate benchmark-only daily history.

    The production source remains read-only. Failure to hydrate one benchmark is
    reported in the returned payload; callers can safely fall back to the source
    DB without invalidating the main V6 run.
    """
    source = Path(source_db_path).resolve()
    target = Path(settlement_db_path).resolve()
    with sqlite3.connect(
        f"file:{source}?mode=ro", uri=True, timeout=30
    ) as conn:
        source_rows = int(
            conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0]
        )
        source_check = _quick_check(conn)
    if source_check != "ok":
        raise RuntimeError(
            f"source stock database quick_check failed: {source_check}"
        )

    _clone_sqlite(source, target)
    provider = fetcher or YfinanceFetcher()
    end_day = as_of or date.today()
    start_day = end_day - timedelta(days=max(30, int(history_days)))
    yahoo_end = end_day + timedelta(days=1)
    stats: dict[str, Any] = {}
    errors: list[dict[str, str]] = []

    with sqlite3.connect(target, timeout=30) as conn:
        for raw_code in benchmark_codes:
            code = str(raw_code).strip().upper()
            if not code:
                continue
            before = _count(conn, code)
            written = 0
            error = None
            try:
                frame = provider.get_daily_data(
                    code,
                    start_date=start_day.isoformat(),
                    end_date=yahoo_end.isoformat(),
                )
                if frame is None or frame.empty:
                    raise RuntimeError("provider returned no benchmark rows")
                written = _upsert_frame(conn, code=code, frame=frame)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                errors.append({"code": code, "error": error})
            after = _count(conn, code)
            stats[code] = {
                "rows_before": before,
                "rows_after": after,
                "written_rows": written,
                "ready": after >= max(1, int(minimum_bars)),
                "error": error,
            }
        target_check = _quick_check(conn)

    with sqlite3.connect(
        f"file:{source}?mode=ro", uri=True, timeout=30
    ) as conn:
        source_rows_after = int(
            conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0]
        )
        source_check_after = _quick_check(conn)

    ready = bool(stats) and all(
        bool(item.get("ready")) for item in stats.values()
    )
    source_unchanged = (
        source_rows == source_rows_after
        and source_check == "ok"
        and source_check_after == "ok"
    )
    return {
        "version": "v6-benchmark-settlement-clone-v1",
        "status": "ready" if ready else "degraded",
        "ready": ready,
        "source_database": str(source),
        "settlement_database": str(target),
        "source_read_only": True,
        "source_unchanged": source_unchanged,
        "history_days": max(30, int(history_days)),
        "window_start": start_day.isoformat(),
        "window_end": end_day.isoformat(),
        "benchmarks": stats,
        "errors": errors,
        "settlement_quick_check": target_check,
    }
