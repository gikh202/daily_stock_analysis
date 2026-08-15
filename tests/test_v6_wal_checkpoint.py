from __future__ import annotations

import shutil
import sqlite3

from src.v6_daily import checkpoint_v6_database


def _row_count(path):
    conn = sqlite3.connect(str(path))
    try:
        return int(conn.execute("SELECT COUNT(*) FROM audit_rows").fetchone()[0])
    finally:
        conn.close()


def test_checkpoint_makes_main_db_copy_self_contained(tmp_path):
    db = tmp_path / "v6_daily.db"
    stale_copy = tmp_path / "before.db"
    durable_copy = tmp_path / "after.db"

    writer = sqlite3.connect(str(db))
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE audit_rows(id INTEGER PRIMARY KEY, value TEXT)")
        writer.commit()
        # Checkpoint schema first, then keep the newest data row only in WAL.
        checkpoint_v6_database(db)
        writer.execute("INSERT INTO audit_rows(value) VALUES ('latest')")
        writer.commit()

        shutil.copy2(db, stale_copy)
        assert _row_count(stale_copy) == 0

        result = checkpoint_v6_database(db)
        assert result["busy"] == 0
        shutil.copy2(db, durable_copy)
        assert _row_count(durable_copy) == 1
    finally:
        writer.close()
