import sqlite3

from src.alpha_engine.shadow_store import read_analysis_records


def test_bounded_history_scan_always_includes_newest_records(tmp_path):
    db = tmp_path / "stock.db"
    conn = sqlite3.connect(db)
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
    for idx in range(1, 11):
        conn.execute(
            "INSERT INTO analysis_history VALUES (?,?,?,?,?,?)",
            (
                idx,
                f"q{idx}",
                "MSFT",
                "{}",
                "{}",
                f"2026-01-{idx:02d} 22:30:00",
            ),
        )
    conn.commit()
    conn.close()

    rows = read_analysis_records(str(db), limit=3)
    assert [row["id"] for row in rows] == [8, 9, 10]
