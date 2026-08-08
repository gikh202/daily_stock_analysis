from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .models import AlphaDecision


ALPHA_SCHEMA_VERSION = "v5.0-shadow.1"
DEFAULT_HORIZONS = (5, 10, 20)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class AlphaShadowStore:
    """Independent SQLite persistence for V5 shadow decisions.

    Keeping the shadow database separate from the production database gives us
    two useful guarantees during incubation:
    - V5 schema changes cannot break the proven V4 storage layer;
    - removing V5 is as simple as deleting alpha_shadow.db.
    """

    def __init__(self, path: str = "alpha_data/alpha_shadow.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS alpha_schema_meta (
                    version TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS alpha_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    analysis_history_id INTEGER NOT NULL UNIQUE,
                    query_id TEXT,
                    code TEXT NOT NULL,
                    analysis_created_at TEXT NOT NULL,
                    shadow_created_at TEXT NOT NULL,
                    engine_version TEXT NOT NULL,
                    feature_version TEXT NOT NULL,
                    quality_score REAL,
                    opportunity_score REAL,
                    risk_score REAL,
                    confidence REAL NOT NULL,
                    decision TEXT NOT NULL,
                    baseline_price REAL,
                    market_regime TEXT,
                    features_json TEXT NOT NULL,
                    trade_plan_json TEXT NOT NULL,
                    reasons_json TEXT NOT NULL,
                    limitations_json TEXT NOT NULL,
                    diagnostics_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_alpha_signals_code_time
                    ON alpha_signals(code, analysis_created_at);
                CREATE INDEX IF NOT EXISTS ix_alpha_signals_decision_time
                    ON alpha_signals(decision, analysis_created_at);

                CREATE TABLE IF NOT EXISTS alpha_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id INTEGER NOT NULL,
                    horizon_days INTEGER NOT NULL,
                    evaluated_at TEXT NOT NULL,
                    end_trade_date TEXT NOT NULL,
                    start_price REAL NOT NULL,
                    end_price REAL NOT NULL,
                    return_pct REAL NOT NULL,
                    max_high REAL,
                    min_low REAL,
                    mfe_pct REAL,
                    mae_pct REAL,
                    directional_hit INTEGER,
                    UNIQUE(signal_id, horizon_days),
                    FOREIGN KEY(signal_id) REFERENCES alpha_signals(id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS ix_alpha_outcomes_horizon
                    ON alpha_outcomes(horizon_days, evaluated_at);
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO alpha_schema_meta(version, applied_at) "
                "VALUES (?, ?)",
                (ALPHA_SCHEMA_VERSION, _utc_now()),
            )

    def quick_check(self) -> str:
        with self.connect() as conn:
            row = conn.execute("PRAGMA quick_check").fetchone()
        return str(row[0]) if row else "unknown"

    def has_analysis_history_id(self, analysis_history_id: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM alpha_signals "
                "WHERE analysis_history_id=? LIMIT 1",
                (int(analysis_history_id),),
            ).fetchone()
        return row is not None

    def save_signal(
        self,
        *,
        analysis_history_id: int,
        query_id: Optional[str],
        code: str,
        analysis_created_at: str,
        engine_version: str,
        feature_version: str,
        decision: AlphaDecision,
        baseline_price: Optional[float],
        market_regime: Optional[str],
        adapter_diagnostics: Optional[Mapping[str, Any]] = None,
    ) -> bool:
        diagnostics = dict(decision.diagnostics)
        if adapter_diagnostics:
            diagnostics["feature_adapter"] = dict(adapter_diagnostics)

        payload = (
            int(analysis_history_id),
            query_id,
            str(code or "").strip().upper(),
            str(analysis_created_at),
            _utc_now(),
            str(engine_version),
            str(feature_version),
            _finite(decision.quality_score),
            _finite(decision.opportunity_score),
            _finite(decision.risk_score),
            float(decision.confidence),
            decision.decision,
            _finite(baseline_price),
            market_regime,
            _json_dumps(asdict(decision.features)),
            _json_dumps(asdict(decision.trade_plan)),
            _json_dumps(list(decision.reasons)),
            _json_dumps(list(decision.limitations)),
            _json_dumps(diagnostics),
        )
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO alpha_signals(
                    analysis_history_id, query_id, code,
                    analysis_created_at, shadow_created_at,
                    engine_version, feature_version,
                    quality_score, opportunity_score, risk_score,
                    confidence, decision, baseline_price, market_regime,
                    features_json, trade_plan_json, reasons_json,
                    limitations_json, diagnostics_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                payload,
            )
        return cursor.rowcount > 0

    def pending_signals(self) -> List[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    """
                    SELECT s.*
                    FROM alpha_signals s
                    ORDER BY s.analysis_created_at ASC, s.id ASC
                    """
                ).fetchall()
            )

    def evaluated_horizons(self, signal_id: int) -> set[int]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT horizon_days FROM alpha_outcomes WHERE signal_id=?",
                (int(signal_id),),
            ).fetchall()
        return {int(row[0]) for row in rows}

    def save_outcome(
        self,
        *,
        signal_id: int,
        horizon_days: int,
        end_trade_date: str,
        start_price: float,
        end_price: float,
        max_high: Optional[float],
        min_low: Optional[float],
        decision: str,
    ) -> bool:
        return_pct = (end_price / start_price - 1.0) * 100.0
        mfe = (
            None
            if max_high is None
            else (max_high / start_price - 1.0) * 100.0
        )
        mae = (
            None
            if min_low is None
            else (min_low / start_price - 1.0) * 100.0
        )
        directional_hit: Optional[int]
        if decision == "BUY_SETUP":
            directional_hit = int(return_pct > 0.0)
        elif decision == "AVOID":
            directional_hit = int(return_pct <= 0.0)
        else:
            directional_hit = None

        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO alpha_outcomes(
                    signal_id, horizon_days, evaluated_at, end_trade_date,
                    start_price, end_price, return_pct, max_high, min_low,
                    mfe_pct, mae_pct, directional_hit
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(signal_id),
                    int(horizon_days),
                    _utc_now(),
                    end_trade_date,
                    float(start_price),
                    float(end_price),
                    round(return_pct, 6),
                    _finite(max_high),
                    _finite(min_low),
                    None if mfe is None else round(mfe, 6),
                    None if mae is None else round(mae, 6),
                    directional_hit,
                ),
            )
        return cursor.rowcount > 0

    def counts(self) -> Dict[str, int]:
        with self.connect() as conn:
            signal_count = int(
                conn.execute("SELECT COUNT(*) FROM alpha_signals").fetchone()[0]
            )
            outcome_count = int(
                conn.execute("SELECT COUNT(*) FROM alpha_outcomes").fetchone()[0]
            )
        return {"signals": signal_count, "outcomes": outcome_count}

    def scorecard(self, *, min_samples: int = 5) -> Dict[str, Any]:
        """Return descriptive shadow performance; never auto-modifies weights."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    s.decision,
                    s.market_regime,
                    o.horizon_days,
                    COUNT(*) AS n,
                    AVG(o.return_pct) AS avg_return_pct,
                    AVG(o.mfe_pct) AS avg_mfe_pct,
                    AVG(o.mae_pct) AS avg_mae_pct,
                    SUM(CASE WHEN o.directional_hit=1 THEN 1 ELSE 0 END) AS hits,
                    SUM(CASE WHEN o.directional_hit IS NOT NULL THEN 1 ELSE 0 END)
                        AS directional_n
                FROM alpha_outcomes o
                JOIN alpha_signals s ON s.id=o.signal_id
                GROUP BY s.decision, s.market_regime, o.horizon_days
                ORDER BY o.horizon_days, s.decision, s.market_regime
                """
            ).fetchall()

        buckets: List[Dict[str, Any]] = []
        for row in rows:
            n = int(row["n"] or 0)
            directional_n = int(row["directional_n"] or 0)
            hit_rate = None
            if directional_n > 0:
                hit_rate = round(
                    100.0 * int(row["hits"] or 0) / directional_n,
                    2,
                )
            buckets.append(
                {
                    "decision": row["decision"],
                    "market_regime": row["market_regime"],
                    "horizon_days": int(row["horizon_days"]),
                    "samples": n,
                    "mature": n >= int(min_samples),
                    "avg_return_pct": (
                        None
                        if row["avg_return_pct"] is None
                        else round(float(row["avg_return_pct"]), 4)
                    ),
                    "avg_mfe_pct": (
                        None
                        if row["avg_mfe_pct"] is None
                        else round(float(row["avg_mfe_pct"]), 4)
                    ),
                    "avg_mae_pct": (
                        None
                        if row["avg_mae_pct"] is None
                        else round(float(row["avg_mae_pct"]), 4)
                    ),
                    "directional_samples": directional_n,
                    "directional_hit_rate_pct": hit_rate,
                }
            )
        return {
            "schema_version": ALPHA_SCHEMA_VERSION,
            "minimum_bucket_samples": int(min_samples),
            "buckets": buckets,
        }


def read_analysis_records(
    stock_db_path: str,
    *,
    limit: int = 1000,
) -> List[Dict[str, Any]]:
    """Read the newest production analyses without starving future records.

    We fetch newest-first so a bounded scan keeps seeing newly-created history
    even after analysis_history exceeds the scan limit, then reverse the selected
    window for deterministic chronological processing.
    """
    path = Path(stock_db_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        quick = conn.execute("PRAGMA quick_check").fetchone()
        if not quick or str(quick[0]).strip().lower() != "ok":
            raise RuntimeError(
                "stock database quick_check failed: "
                f"{quick[0] if quick else 'unknown'}"
            )
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "analysis_history" not in tables:
            raise RuntimeError("analysis_history table missing")
        rows = list(
            conn.execute(
                """
                SELECT id, query_id, code, context_snapshot, raw_result, created_at
                FROM analysis_history
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        )
        rows.reverse()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _parse_datetime_date(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    # SQLite SQLAlchemy DateTime commonly stores `YYYY-MM-DD HH:MM:SS.mmmmmm`.
    return text[:10] if len(text) >= 10 else None


def _daily_bars_after(
    stock_db_path: str,
    *,
    code: str,
    analysis_created_at: Any,
    needed: int,
) -> List[Dict[str, Any]]:
    analysis_date = _parse_datetime_date(analysis_created_at)
    if analysis_date is None:
        return []
    conn = sqlite3.connect(
        f"file:{Path(stock_db_path)}?mode=ro",
        uri=True,
        timeout=30,
    )
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT date, open, high, low, close
            FROM stock_daily
            WHERE code=? AND date>?
            ORDER BY date ASC
            LIMIT ?
            """,
            (str(code), analysis_date, max(1, int(needed))),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def mature_pending_outcomes(
    store: AlphaShadowStore,
    stock_db_path: str,
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
) -> Dict[str, int]:
    """Evaluate by future trading bars, never calendar days/look-ahead data."""
    normalized_horizons = sorted(
        {int(h) for h in horizons if int(h) > 0}
    )
    if not normalized_horizons:
        return {"evaluated": 0, "skipped_not_mature": 0}

    evaluated = 0
    skipped = 0
    max_horizon = max(normalized_horizons)

    for signal in store.pending_signals():
        done = store.evaluated_horizons(int(signal["id"]))
        needed = [h for h in normalized_horizons if h not in done]
        if not needed:
            continue
        bars = _daily_bars_after(
            stock_db_path,
            code=str(signal["code"]),
            analysis_created_at=signal["analysis_created_at"],
            needed=max_horizon,
        )
        start_price = _finite(signal["baseline_price"])
        if start_price is None or start_price <= 0:
            skipped += len(needed)
            continue

        for horizon in needed:
            if len(bars) < horizon:
                skipped += 1
                continue
            window = bars[:horizon]
            end = window[horizon - 1]
            end_price = _finite(end.get("close"))
            if end_price is None or end_price <= 0:
                skipped += 1
                continue
            highs = [_finite(bar.get("high")) for bar in window]
            lows = [_finite(bar.get("low")) for bar in window]
            max_high = max(
                (v for v in highs if v is not None),
                default=None,
            )
            min_low = min(
                (v for v in lows if v is not None),
                default=None,
            )
            if store.save_outcome(
                signal_id=int(signal["id"]),
                horizon_days=horizon,
                end_trade_date=str(end.get("date")),
                start_price=start_price,
                end_price=end_price,
                max_high=max_high,
                min_low=min_low,
                decision=str(signal["decision"]),
            ):
                evaluated += 1

    return {"evaluated": evaluated, "skipped_not_mature": skipped}
