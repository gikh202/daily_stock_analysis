import json
import sqlite3
from pathlib import Path

from src.alpha_engine import AlphaDecisionEngine
from src.alpha_engine.features import AlphaFeatureAdapter
from src.alpha_engine.shadow_store import AlphaShadowStore, mature_pending_outcomes


def _make_stock_db(path: Path, *, bars: int = 20) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE analysis_history(
            id INTEGER PRIMARY KEY,
            query_id TEXT,
            code TEXT,
            context_snapshot TEXT,
            raw_result TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE stock_daily(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT,
            date TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL
        )
        """
    )
    snapshot = {
        "trend_result": {
            "signal_score": 85,
            "current_price": 100,
            "support_levels": [96],
            "resistance_levels": [112],
        },
        "prediction_context": {
            "horizons": {
                "5d": {"target_return_pct": 1.0, "excess_vs_spy_pct": 1.0, "excess_vs_qqq_pct": 0.5},
                "20d": {"target_return_pct": 6.0, "excess_vs_spy_pct": 3.0, "excess_vs_qqq_pct": 2.0},
                "60d": {"target_return_pct": 10.0, "excess_vs_spy_pct": 5.0, "excess_vs_qqq_pct": 4.0},
            },
            "realized_vol_20d_pct": 20,
        },
        "market_regime": {"regime": "risk_on", "market_breadth": {"breadth": "broad"}},
        "earnings_event": {"days_until_earnings": 25},
        "rvol": 1.3,
    }
    conn.execute(
        "INSERT INTO analysis_history(id,query_id,code,context_snapshot,raw_result,created_at) VALUES (1,'q1','MSFT',?,?,?)",
        (json.dumps(snapshot), "{}", "2026-01-01 23:00:00"),
    )
    for day in range(2, bars + 2):
        close = 100 + day * 0.5
        conn.execute(
            "INSERT INTO stock_daily(code,date,open,high,low,close) VALUES ('MSFT',?,?,?,?,?)",
            (f"2026-01-{day:02d}", close - 0.2, close + 1.0, close - 1.0, close),
        )
    conn.commit()
    conn.close()


def _persist_one(store: AlphaShadowStore) -> None:
    snapshot = {
        "trend_result": {
            "signal_score": 85,
            "current_price": 100,
            "support_levels": [96],
            "resistance_levels": [112],
        },
        "prediction_context": {
            "horizons": {
                "20d": {"target_return_pct": 6.0, "excess_vs_spy_pct": 3.0, "excess_vs_qqq_pct": 2.0},
                "60d": {"target_return_pct": 10.0, "excess_vs_spy_pct": 5.0, "excess_vs_qqq_pct": 4.0},
            },
            "realized_vol_20d_pct": 20,
        },
        "market_regime": {"regime": "risk_on", "market_breadth": {"breadth": "broad"}},
        "earnings_event": {"days_until_earnings": 25},
        "rvol": 1.3,
    }
    adapted = AlphaFeatureAdapter.from_snapshot(snapshot)
    decision = AlphaDecisionEngine().evaluate(
        "MSFT",
        adapted.features,
        current_price=adapted.current_price,
        support=adapted.support,
        resistance=adapted.resistance,
        atr=adapted.atr,
    )
    inserted = store.save_signal(
        analysis_history_id=1,
        query_id="q1",
        code="MSFT",
        analysis_created_at="2026-01-01 23:00:00",
        engine_version="test",
        feature_version=AlphaFeatureAdapter.version,
        decision=decision,
        baseline_price=100,
        market_regime="risk_on",
        adapter_diagnostics=adapted.diagnostics,
    )
    assert inserted is True


def test_shadow_store_is_idempotent_and_matures_trading_horizons(tmp_path):
    stock_db = tmp_path / "stock.db"
    alpha_db = tmp_path / "alpha.db"
    _make_stock_db(stock_db, bars=20)
    store = AlphaShadowStore(str(alpha_db))
    _persist_one(store)

    assert store.quick_check() == "ok"
    assert store.counts()["signals"] == 1
    assert store.has_analysis_history_id(1)

    result = mature_pending_outcomes(store, str(stock_db))
    assert result["evaluated"] == 3
    assert store.counts()["outcomes"] == 3

    second = mature_pending_outcomes(store, str(stock_db))
    assert second["evaluated"] == 0
    assert store.counts()["outcomes"] == 3


def test_shadow_store_never_uses_fewer_than_requested_future_bars(tmp_path):
    stock_db = tmp_path / "stock.db"
    alpha_db = tmp_path / "alpha.db"
    _make_stock_db(stock_db, bars=4)
    store = AlphaShadowStore(str(alpha_db))
    _persist_one(store)

    result = mature_pending_outcomes(store, str(stock_db))
    assert result["evaluated"] == 0
    assert store.counts()["outcomes"] == 0
