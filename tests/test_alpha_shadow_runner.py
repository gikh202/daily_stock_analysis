import json
import sqlite3
from pathlib import Path

from scripts.run_alpha_shadow import run


def _build_fixture(path: Path) -> None:
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
            "signal_score": 88,
            "current_price": 200,
            "support_levels": [194],
            "resistance_levels": [220],
        },
        "prediction_context": {
            "horizons": {
                "5d": {"target_return_pct": 1.5},
                "20d": {
                    "target_return_pct": 7.0,
                    "excess_vs_spy_pct": 4.0,
                    "excess_vs_qqq_pct": 3.0,
                },
                "60d": {
                    "target_return_pct": 14.0,
                    "excess_vs_spy_pct": 7.0,
                    "excess_vs_qqq_pct": 5.0,
                },
            },
            "realized_vol_20d_pct": 22,
        },
        "market_regime": {
            "regime": "risk_on",
            "market_breadth": {"breadth": "broad"},
        },
        "earnings_event": {"days_until_earnings": 25},
        "rvol": 1.4,
    }
    conn.execute(
        "INSERT INTO analysis_history VALUES (1,'q1','GOOGL',?,?,?)",
        (json.dumps(snapshot), "{}", "2026-01-01 22:30:00"),
    )
    for day in range(2, 22):
        price = 200 + day
        conn.execute(
            "INSERT INTO stock_daily(code,date,open,high,low,close) VALUES ('GOOGL',?,?,?,?,?)",
            (f"2026-01-{day:02d}", price - 0.5, price + 1.0, price - 1.0, price),
        )
    conn.commit()
    conn.close()


def test_shadow_runner_is_end_to_end_and_idempotent(tmp_path):
    stock_db = tmp_path / "stock.db"
    alpha_db = tmp_path / "alpha.db"
    reports = tmp_path / "reports"
    _build_fixture(stock_db)

    first = run(
        stock_db_path=str(stock_db),
        alpha_db_path=str(alpha_db),
        report_dir=str(reports),
        limit=100,
    )
    assert first["quick_check"] == "ok"
    assert first["new_signals"] == 1
    assert first["new_outcomes"] == 3
    assert (reports / "latest.json").is_file()
    assert (reports / "latest.md").is_file()

    payload = json.loads((reports / "latest.json").read_text(encoding="utf-8"))
    assert payload["alpha_board"]
    board_item = payload["alpha_board"][0]
    assert board_item["code"] == "GOOGL"
    assert board_item["decision"] in {"BUY_SETUP", "WATCH", "WAIT", "AVOID"}
    assert "max_position_pct" in board_item
    assert "risk_reward" in board_item

    markdown = (reports / "latest.md").read_text(encoding="utf-8")
    assert "## Alpha Board" in markdown
    assert "GOOGL" in markdown

    second = run(
        stock_db_path=str(stock_db),
        alpha_db_path=str(alpha_db),
        report_dir=str(reports),
        limit=100,
    )
    assert second["new_signals"] == 0
    assert second["new_outcomes"] == 0
