from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class PortfolioRiskContext:
    status: str
    positions: tuple[dict[str, Any], ...] = ()
    portfolio_drawdown_pct: float | None = None
    total_equity: float | None = None
    gross_exposure_pct: float | None = None
    active_accounts: int = 0
    cost_method: str = "fifo"
    diagnostics: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["diagnostics"] = dict(self.diagnostics or {})
        return payload


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _parse_object(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _find_value(root: Mapping[str, Any], names: Sequence[str]) -> Any:
    wanted = {str(name) for name in names}
    queue: list[Mapping[str, Any]] = [root]
    seen: set[int] = set()
    while queue:
        current = queue.pop(0)
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        for key, value in current.items():
            if key in wanted and not isinstance(value, (dict, list, tuple, set)):
                return value
            if isinstance(value, Mapping):
                queue.append(value)
    return None


def sector_map_from_analysis_records(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    """Resolve the latest structured sector label for each symbol without LLM prose."""
    result: dict[str, tuple[str, str]] = {}
    for record in records:
        code = str(record.get("code") or "").strip().upper()
        if not code:
            continue
        context = _parse_object(record.get("context_snapshot"))
        sector = str(
            _find_value(context, ("sector", "sector_name", "industry_sector")) or ""
        ).strip()
        if not sector:
            continue
        rank = str(record.get("created_at") or "")
        current = result.get(code)
        if current is None or rank >= current[0]:
            result[code] = (rank, sector)
    return {code: value[1] for code, value in result.items()}


def _sectors_from_db(
    conn: sqlite3.Connection,
    *,
    symbols: Sequence[str],
) -> dict[str, str]:
    """Best-effort sector lookup from structured context snapshots already in production DB."""
    if not symbols or "analysis_history" not in _tables(conn):
        return {}
    columns = _columns(conn, "analysis_history")
    required = {"code", "context_snapshot"}
    if not required.issubset(columns):
        return {}
    rank_expr = "created_at" if "created_at" in columns else "id" if "id" in columns else "rowid"
    placeholders = ",".join("?" for _ in symbols)
    try:
        rows = conn.execute(
            f"""
            SELECT code,context_snapshot,{rank_expr} AS rank_value
            FROM analysis_history
            WHERE upper(code) IN ({placeholders})
            ORDER BY rank_value DESC
            """,
            tuple(str(symbol).upper() for symbol in symbols),
        ).fetchall()
    except sqlite3.Error:
        return {}
    result: dict[str, str] = {}
    for row in rows:
        code = str(row["code"] or "").strip().upper()
        if not code or code in result:
            continue
        context = _parse_object(row["context_snapshot"])
        sector = str(
            _find_value(context, ("sector", "sector_name", "industry_sector")) or ""
        ).strip()
        if sector:
            result[code] = sector
    return result


def _account_equity_and_highwater(
    conn: sqlite3.Connection,
    account_ids: Sequence[int],
    *,
    cost_method: str,
) -> tuple[float | None, float | None, int]:
    current_total = 0.0
    highwater_total = 0.0
    available = 0
    for account_id in account_ids:
        rows = conn.execute(
            """
            SELECT snapshot_date,total_equity
            FROM portfolio_daily_snapshots
            WHERE account_id=? AND cost_method=? AND total_equity>0
            ORDER BY snapshot_date ASC,id ASC
            """,
            (int(account_id), cost_method),
        ).fetchall()
        values = [
            value
            for value in (_finite(row["total_equity"]) for row in rows)
            if value is not None and value > 0
        ]
        if not values:
            continue
        available += 1
        current_total += values[-1]
        highwater_total += max(values)
    if available == 0:
        return None, None, 0
    return current_total, highwater_total, available


def load_portfolio_risk_context(
    db_path: str | Path,
    *,
    sector_by_symbol: Mapping[str, str] | None = None,
    cost_method: str = "fifo",
) -> PortfolioRiskContext:
    """Read active U.S. holdings into the deterministic V6 sizing overlay.

    Read-only and fail-safe. Existing exposure, aggregate gross exposure, sector
    concentration and portfolio drawdown can only reduce the proposed new risk.
    """
    path = Path(db_path)
    method = str(cost_method or "fifo").strip().lower() or "fifo"
    if not path.is_file():
        return PortfolioRiskContext(
            status="database_unavailable",
            cost_method=method,
            diagnostics={"database": str(path)},
        )

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        required = {"portfolio_accounts", "portfolio_positions"}
        tables = _tables(conn)
        if not required.issubset(tables):
            return PortfolioRiskContext(
                status="portfolio_tables_unavailable",
                cost_method=method,
                diagnostics={"missing_tables": sorted(required - tables)},
            )

        accounts = conn.execute(
            """
            SELECT id FROM portfolio_accounts
            WHERE is_active=1 AND lower(market)='us'
            ORDER BY id
            """
        ).fetchall()
        account_ids = [int(row["id"]) for row in accounts]
        if not account_ids:
            return PortfolioRiskContext(
                status="no_active_us_accounts",
                cost_method=method,
                diagnostics={"database": str(path)},
            )

        placeholders = ",".join("?" for _ in account_ids)
        raw_positions = conn.execute(
            f"""
            SELECT account_id,symbol,market_value_base,quantity
            FROM portfolio_positions
            WHERE account_id IN ({placeholders})
              AND lower(market)='us'
              AND cost_method=?
              AND quantity>0
              AND market_value_base>0
            ORDER BY symbol,account_id
            """,
            (*account_ids, method),
        ).fetchall()

        stale_empty_cache = False
        if (
            "portfolio_trades" in tables
            and "portfolio_daily_snapshots" in tables
            and "trade_date" in _columns(conn, "portfolio_trades")
        ):
            stale_empty_cache = conn.execute(
                f"""
                SELECT 1
                FROM portfolio_trades t
                WHERE t.account_id IN ({placeholders})
                GROUP BY t.account_id
                HAVING MAX(t.trade_date) > COALESCE((
                    SELECT MAX(s.snapshot_date)
                    FROM portfolio_daily_snapshots s
                    WHERE s.account_id=t.account_id AND s.cost_method=?
                ), '')
                LIMIT 1
                """,
                (*account_ids, method),
            ).fetchone() is not None
        if stale_empty_cache and not raw_positions:
            return PortfolioRiskContext(
                status="positions_cache_unavailable",
                cost_method=method,
                active_accounts=len(account_ids),
                diagnostics={
                    "reason": "materialized_positions_missing_after_trade_history",
                    "database": str(path),
                },
            )

        by_symbol: dict[str, float] = {}
        for row in raw_positions:
            symbol = str(row["symbol"] or "").strip().upper()
            value = _finite(row["market_value_base"])
            if symbol and value is not None and value > 0:
                by_symbol[symbol] = by_symbol.get(symbol, 0.0) + value

        snapshot_tables = "portfolio_daily_snapshots" in tables
        equity = highwater = None
        snapshot_accounts = 0
        if snapshot_tables:
            equity, highwater, snapshot_accounts = _account_equity_and_highwater(
                conn, account_ids, cost_method=method
            )

        gross_value = sum(by_symbol.values())
        denominator = equity if equity is not None and equity > 0 else gross_value
        supplied_sectors = {
            str(k).upper(): str(v)
            for k, v in (sector_by_symbol or {}).items()
            if str(v or "").strip()
        }
        inferred_sectors = _sectors_from_db(conn, symbols=tuple(by_symbol))
        sectors = {**inferred_sectors, **supplied_sectors}
        positions: list[dict[str, Any]] = []
        if denominator > 0:
            for symbol, value in sorted(by_symbol.items()):
                positions.append(
                    {
                        "symbol": symbol,
                        "weight": value / denominator,
                        "market_value_base": value,
                        "sector": sectors.get(symbol),
                    }
                )

        drawdown = None
        if (
            equity is not None
            and highwater is not None
            and equity > 0
            and highwater > 0
        ):
            drawdown = max(0.0, (highwater - equity) / highwater * 100.0)

        gross_pct = gross_value / denominator * 100.0 if denominator > 0 else None
        return PortfolioRiskContext(
            status="available" if positions or equity is not None else "empty_portfolio",
            positions=tuple(positions),
            portfolio_drawdown_pct=drawdown,
            total_equity=equity,
            gross_exposure_pct=gross_pct,
            active_accounts=len(account_ids),
            cost_method=method,
            diagnostics={
                "positions": len(positions),
                "gross_market_value_base": gross_value,
                "equity_source": (
                    "latest_daily_snapshots"
                    if equity is not None
                    else "position_market_value_fallback"
                ),
                "snapshot_accounts": snapshot_accounts,
                "drawdown_method": "sum_account_highwaters_vs_sum_current_equity",
                "sector_labels_resolved": sum(
                    bool(item.get("sector")) for item in positions
                ),
                "sector_source": "structured_analysis_history_with_optional_override",
            },
        )
    except sqlite3.Error as exc:
        return PortfolioRiskContext(
            status="read_error",
            cost_method=method,
            diagnostics={"error": f"{type(exc).__name__}: {exc}"},
        )
    finally:
        conn.close()
