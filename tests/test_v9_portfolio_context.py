from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from src.alpha_engine.portfolio import PortfolioRiskOverlay
from src.v6_daily.portfolio_context import load_portfolio_risk_context


def test_portfolio_context_reads_real_exposure_drawdown_and_sector(tmp_path: Path) -> None:
    db = tmp_path / "stock.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            """
            CREATE TABLE portfolio_accounts (
                id INTEGER PRIMARY KEY,
                is_active INTEGER,
                market TEXT
            );
            CREATE TABLE portfolio_positions (
                account_id INTEGER,
                symbol TEXT,
                market TEXT,
                cost_method TEXT,
                quantity REAL,
                market_value_base REAL
            );
            CREATE TABLE portfolio_daily_snapshots (
                id INTEGER PRIMARY KEY,
                account_id INTEGER,
                snapshot_date TEXT,
                cost_method TEXT,
                total_equity REAL
            );
            CREATE TABLE analysis_history (
                id INTEGER PRIMARY KEY,
                code TEXT,
                context_snapshot TEXT,
                created_at TEXT
            );
            """
        )
        conn.execute("INSERT INTO portfolio_accounts VALUES (1,1,'us')")
        conn.execute(
            "INSERT INTO portfolio_positions VALUES (1,'AAA','us','fifo',10,300)"
        )
        conn.execute(
            "INSERT INTO portfolio_daily_snapshots VALUES (1,1,'2026-09-08','fifo',1000)"
        )
        conn.execute(
            "INSERT INTO portfolio_daily_snapshots VALUES (2,1,'2026-09-09','fifo',900)"
        )
        conn.execute(
            "INSERT INTO analysis_history VALUES (1,'AAA',?, '2026-09-09T16:00:00')",
            (json.dumps({"fundamental_context": {"sector": "Technology"}}),),
        )
        conn.commit()
    finally:
        conn.close()

    context = load_portfolio_risk_context(db)
    assert context.status == "available"
    assert context.total_equity == pytest.approx(900.0)
    assert context.portfolio_drawdown_pct == pytest.approx(10.0)
    assert context.gross_exposure_pct == pytest.approx(100.0 / 3.0)
    assert context.positions[0]["sector"] == "Technology"
    assert context.positions[0]["weight"] == pytest.approx(1.0 / 3.0)


def test_portfolio_overlay_can_only_reduce_proposed_risk() -> None:
    overlay = PortfolioRiskOverlay(
        max_single_name_pct=0.15,
        max_sector_pct=0.40,
        max_gross_pct=1.00,
        drawdown_soft_limit_pct=8.0,
        drawdown_hard_limit_pct=15.0,
    )
    cap, reasons = overlay.position_cap(
        symbol="NEW",
        proposed_max_position_pct=0.10,
        positions=(
            {"symbol": "AAA", "weight": 0.35, "sector": "Technology"},
        ),
        target_sector="Technology",
        portfolio_drawdown_pct=10.0,
    )
    # Sector capacity is 5%; the soft-drawdown cap is also 5%.
    assert cap == pytest.approx(0.05)
    assert cap <= 0.10
    assert any("drawdown" in reason for reason in reasons)

    blocked, hard_reasons = overlay.position_cap(
        symbol="NEW",
        proposed_max_position_pct=0.10,
        positions=(),
        target_sector="Technology",
        portfolio_drawdown_pct=16.0,
    )
    assert blocked == 0.0
    assert any("hard drawdown" in reason for reason in hard_reasons)


def test_trade_history_without_materialized_positions_fails_closed(tmp_path: Path) -> None:
    db = tmp_path / "stock.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            """
            CREATE TABLE portfolio_accounts (
                id INTEGER PRIMARY KEY, is_active INTEGER, market TEXT
            );
            CREATE TABLE portfolio_positions (
                account_id INTEGER, symbol TEXT, market TEXT, cost_method TEXT,
                quantity REAL, market_value_base REAL
            );
            CREATE TABLE portfolio_trades (
                id INTEGER PRIMARY KEY, account_id INTEGER, trade_date TEXT
            );
            CREATE TABLE portfolio_daily_snapshots (
                id INTEGER PRIMARY KEY, account_id INTEGER, snapshot_date TEXT,
                cost_method TEXT, total_equity REAL
            );
            """
        )
        conn.execute("INSERT INTO portfolio_accounts VALUES (1,1,'us')")
        conn.execute("INSERT INTO portfolio_trades VALUES (1,1,'2026-09-10')")
        conn.execute(
            "INSERT INTO portfolio_daily_snapshots VALUES (1,1,'2026-09-09','fifo',1000)"
        )
        conn.commit()
    finally:
        conn.close()

    context = load_portfolio_risk_context(db)
    assert context.status == "positions_cache_unavailable"
    assert context.positions == ()
    assert context.gross_exposure_pct is None
    assert context.diagnostics["reason"] == "materialized_positions_missing_after_trade_history"


def test_replayed_empty_portfolio_is_not_treated_as_stale_cache(tmp_path: Path) -> None:
    db = tmp_path / "stock.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            """
            CREATE TABLE portfolio_accounts (
                id INTEGER PRIMARY KEY, is_active INTEGER, market TEXT
            );
            CREATE TABLE portfolio_positions (
                account_id INTEGER, symbol TEXT, market TEXT, cost_method TEXT,
                quantity REAL, market_value_base REAL
            );
            CREATE TABLE portfolio_trades (
                id INTEGER PRIMARY KEY, account_id INTEGER, trade_date TEXT
            );
            CREATE TABLE portfolio_daily_snapshots (
                id INTEGER PRIMARY KEY, account_id INTEGER, snapshot_date TEXT,
                cost_method TEXT, total_equity REAL
            );
            """
        )
        conn.execute("INSERT INTO portfolio_accounts VALUES (1,1,'us')")
        conn.execute("INSERT INTO portfolio_trades VALUES (1,1,'2026-09-10')")
        conn.execute(
            "INSERT INTO portfolio_daily_snapshots VALUES (1,1,'2026-09-10','fifo',1000)"
        )
        conn.commit()
    finally:
        conn.close()

    context = load_portfolio_risk_context(db)
    assert context.status == "available"
    assert context.positions == ()
    assert context.gross_exposure_pct == 0.0
