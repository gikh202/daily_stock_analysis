"""V6 Daily Intelligence.

A deterministic, auditable US-stock daily decision layer built on persisted V4
analysis snapshots. V6 does not place orders and does not let LLM prose directly
set numeric scores.
"""

from __future__ import annotations

import atexit
import logging
import os
import sqlite3
from pathlib import Path

from .engine import V6DailyEngine
from .models import V6Signal
from .store import V6DailyStore


logger = logging.getLogger("v6_daily")


def checkpoint_v6_database(path: str | Path) -> dict[str, int]:
    """Flush committed WAL frames into the main DB before standalone archival.

    GitHub's V6 artifact historically uploaded only ``v6_daily.db``. When the
    database remained in WAL mode, the JSON report could describe newer rows
    than the standalone DB artifact. A TRUNCATE checkpoint makes the main file
    self-contained without changing production transaction semantics.
    """
    db_path = Path(path)
    if not db_path.is_file():
        return {"busy": 0, "log_frames": 0, "checkpointed_frames": 0}

    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    finally:
        conn.close()

    busy, log_frames, checkpointed_frames = (int(value) for value in (row or (0, 0, 0)))
    if busy != 0:
        raise RuntimeError(
            "V6 WAL checkpoint could not obtain a complete checkpoint "
            f"busy={busy} log_frames={log_frames} checkpointed={checkpointed_frames}"
        )
    return {
        "busy": busy,
        "log_frames": log_frames,
        "checkpointed_frames": checkpointed_frames,
    }


def _checkpoint_github_actions_database() -> None:
    # Scope the side effect to CI/production GitHub Actions processes only.
    # Local imports and library consumers do not checkpoint files implicitly.
    if str(os.getenv("GITHUB_ACTIONS") or "").strip().lower() != "true":
        return
    path = Path(os.getenv("V6_DAILY_DB_PATH", "v6_data/v6_daily.db"))
    if not path.is_file():
        return
    try:
        result = checkpoint_v6_database(path)
        logger.info("V6 WAL archive checkpoint: %s", result)
    except Exception:
        # atexit cannot propagate a useful process error. The normal workflow
        # integrity checks still run afterward; keep a visible diagnostic here.
        logger.exception("V6 WAL archive checkpoint failed for %s", path)


atexit.register(_checkpoint_github_actions_database)

__all__ = [
    "V6DailyEngine",
    "V6DailyStore",
    "V6Signal",
    "checkpoint_v6_database",
]
