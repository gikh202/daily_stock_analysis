from __future__ import annotations

import sqlite3
import statistics
from typing import Any, Dict, List, Optional, Sequence

from .store import (
    V6DailyStore,
    V6_SCHEMA_VERSION,
    _finite,
    _return_hit_rate,
    _spearman,
)


VERSIONED_SIGNAL_SCHEMA_VERSION = "v6.3-versioned-signal-identity.1"


def _quote(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _unique_index_columns(conn: sqlite3.Connection, table: str) -> list[list[str]]:
    result: list[list[str]] = []
    for row in conn.execute(f"PRAGMA index_list({_quote(table)})").fetchall():
        if not int(row[2]):
            continue
        index_name = str(row[1])
        columns = [
            str(item[2])
            for item in conn.execute(f"PRAGMA index_info({_quote(index_name)})").fetchall()
        ]
        result.append(columns)
    return result


def ensure_versioned_signal_identity(path: str) -> bool:
    """Allow one V4 analysis input to coexist across multiple engine versions.

    Legacy V6 stored ``analysis_history_id`` with a column-level UNIQUE
    constraint. That prevents Champion/Challenger or replay versions from
    coexisting against the same immutable V4 input. SQLite cannot drop that
    constraint in place, so this performs a one-time table rebuild while
    preserving row ids, data, explicit indexes and the v6_outcomes foreign-key
    relationship.
    """
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='v6_signals'"
        ).fetchone()
        if table is None:
            return False

        needs_rebuild = ["analysis_history_id"] in _unique_index_columns(conn, "v6_signals")
        if not needs_rebuild:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_v6_signals_history_engine "
                "ON v6_signals(analysis_history_id, engine_version)"
            )
            conn.execute(
                "INSERT OR IGNORE INTO v6_schema_meta(version, applied_at) "
                "VALUES (?, datetime('now'))",
                (VERSIONED_SIGNAL_SCHEMA_VERSION,),
            )
            conn.commit()
            return False

        columns = conn.execute("PRAGMA table_info(v6_signals)").fetchall()
        if not columns:
            raise RuntimeError("v6_signals exists but has no columns")
        explicit_indexes = [
            str(row[0])
            for row in conn.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='index' AND tbl_name='v6_signals' AND sql IS NOT NULL"
            ).fetchall()
            if row[0]
        ]

        definitions: list[str] = []
        names: list[str] = []
        for _, name, type_name, not_null, default_value, primary_key in columns:
            name = str(name)
            names.append(name)
            if name == "id" and int(primary_key):
                definitions.append(f"{_quote(name)} INTEGER PRIMARY KEY AUTOINCREMENT")
                continue
            definition = f"{_quote(name)} {str(type_name or 'TEXT')}"
            if int(not_null):
                definition += " NOT NULL"
            if default_value is not None:
                definition += f" DEFAULT {default_value}"
            if int(primary_key):
                definition += " PRIMARY KEY"
            definitions.append(definition)

        quoted_names = ", ".join(_quote(name) for name in names)
        conn.commit()
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DROP TABLE IF EXISTS v6_signals_versioned_new")
            conn.execute(
                "CREATE TABLE v6_signals_versioned_new (" + ", ".join(definitions) + ")"
            )
            conn.execute(
                f"INSERT INTO v6_signals_versioned_new ({quoted_names}) "
                f"SELECT {quoted_names} FROM v6_signals"
            )
            conn.execute("DROP TABLE v6_signals")
            conn.execute("ALTER TABLE v6_signals_versioned_new RENAME TO v6_signals")
            for sql in explicit_indexes:
                conn.execute(sql)
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_v6_signals_history_engine "
                "ON v6_signals(analysis_history_id, engine_version)"
            )
            conn.execute(
                "INSERT OR IGNORE INTO v6_schema_meta(version, applied_at) "
                "VALUES (?, datetime('now'))",
                (VERSIONED_SIGNAL_SCHEMA_VERSION,),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute("PRAGMA foreign_keys=ON")

        foreign_key_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise RuntimeError(f"foreign_key_check failed after V6 signal migration: {foreign_key_errors[:3]}")
        quick = str(conn.execute("PRAGMA quick_check").fetchone()[0]).strip().lower()
        if quick != "ok":
            raise RuntimeError(f"quick_check failed after V6 signal migration: {quick!r}")
        return True
    finally:
        conn.close()


class VersionedV6DailyStore(V6DailyStore):
    """Production V6 store scoped to one explicit engine version.

    The underlying SQLite database may contain several engine versions, but
    live board, deltas, maturation and scoreboard queries from this object only
    see ``active_engine_version``. This prevents a replay/challenger insert with
    a larger row id from becoming the production truth source.
    """

    def __init__(self, path: str, *, active_engine_version: str) -> None:
        self.active_engine_version = str(active_engine_version or "").strip()
        if not self.active_engine_version:
            raise ValueError("active_engine_version is required")
        super().__init__(path)
        self.signal_identity_migrated = ensure_versioned_signal_identity(str(self.path))

    def has_analysis_history_version(self, history_id: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM v6_signals "
                "WHERE analysis_history_id=? AND engine_version=? LIMIT 1",
                (int(history_id), self.active_engine_version),
            ).fetchone()
        return row is not None

    def counts(self) -> Dict[str, int]:
        with self.connect() as conn:
            signals = int(
                conn.execute(
                    "SELECT COUNT(*) FROM v6_signals WHERE engine_version=?",
                    (self.active_engine_version,),
                ).fetchone()[0]
            )
            outcomes = int(
                conn.execute(
                    "SELECT COUNT(*) FROM v6_outcomes o "
                    "JOIN v6_signals s ON s.id=o.signal_id "
                    "WHERE s.engine_version=?",
                    (self.active_engine_version,),
                ).fetchone()[0]
            )
        return {"signals": signals, "outcomes": outcomes}

    def all_signals(self) -> List[sqlite3.Row]:
        with self.connect() as conn:
            return list(
                conn.execute(
                    "SELECT * FROM v6_signals WHERE engine_version=? "
                    "ORDER BY analysis_created_at ASC, id ASC",
                    (self.active_engine_version,),
                ).fetchall()
            )

    def latest_board(self) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT s.*
                FROM v6_signals s
                WHERE s.engine_version=?
                  AND NOT EXISTS (
                    SELECT 1
                    FROM v6_signals newer
                    WHERE newer.engine_version=s.engine_version
                      AND newer.code=s.code
                      AND (
                        COALESCE(newer.effective_trade_date, substr(newer.analysis_created_at,1,10), '')
                          > COALESCE(s.effective_trade_date, substr(s.analysis_created_at,1,10), '')
                        OR (
                          COALESCE(newer.effective_trade_date, substr(newer.analysis_created_at,1,10), '')
                            = COALESCE(s.effective_trade_date, substr(s.analysis_created_at,1,10), '')
                          AND newer.analysis_created_at > s.analysis_created_at
                        )
                        OR (
                          COALESCE(newer.effective_trade_date, substr(newer.analysis_created_at,1,10), '')
                            = COALESCE(s.effective_trade_date, substr(s.analysis_created_at,1,10), '')
                          AND newer.analysis_created_at = s.analysis_created_at
                          AND newer.id > s.id
                        )
                      )
                  )
                ORDER BY
                    CASE s.decision WHEN 'BUY_SETUP' THEN 0 WHEN 'WATCH' THEN 1
                        WHEN 'WAIT' THEN 2 WHEN 'AVOID' THEN 3 ELSE 4 END,
                    COALESCE(s.opportunity_score, -1) DESC,
                    COALESCE(s.risk_score, 101) ASC, s.code ASC
                """,
                (self.active_engine_version,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def previous_for_code(self, code: str, latest_id: int) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            current = conn.execute(
                "SELECT * FROM v6_signals WHERE id=? AND engine_version=?",
                (int(latest_id), self.active_engine_version),
            ).fetchone()
            if current is None:
                return None
            current_date = str(
                current["effective_trade_date"]
                or str(current["analysis_created_at"] or "")[:10]
                or ""
            )
            row = conn.execute(
                """
                SELECT * FROM v6_signals
                WHERE code=? AND engine_version=?
                  AND (
                    COALESCE(effective_trade_date, substr(analysis_created_at,1,10), '') < ?
                    OR (
                      COALESCE(effective_trade_date, substr(analysis_created_at,1,10), '') = ?
                      AND analysis_created_at < ?
                    )
                    OR (
                      COALESCE(effective_trade_date, substr(analysis_created_at,1,10), '') = ?
                      AND analysis_created_at = ?
                      AND id < ?
                    )
                  )
                ORDER BY COALESCE(effective_trade_date, substr(analysis_created_at,1,10), '') DESC,
                         analysis_created_at DESC, id DESC
                LIMIT 1
                """,
                (
                    str(code),
                    self.active_engine_version,
                    current_date,
                    current_date,
                    str(current["analysis_created_at"] or ""),
                    current_date,
                    str(current["analysis_created_at"] or ""),
                    int(latest_id),
                ),
            ).fetchone()
        return None if row is None else self._row_to_dict(row)

    def scoreboard(self, *, min_samples: int = 50) -> Dict[str, Any]:
        minimum = max(3, int(min_samples))
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT s.direction AS signal_direction, s.decision, s.market_regime,
                       s.instrument_type, s.opportunity_score,
                       o.horizon_days, o.return_pct, o.mfe_pct, o.mae_pct,
                       o.directional_hit, o.direction_used,
                       COALESCE(o.forecast_score, s.forecast_score) AS outcome_forecast_score,
                       o.excess_vs_spy_pct, o.excess_vs_qqq_pct
                FROM v6_outcomes o
                JOIN v6_signals s ON s.id=o.signal_id
                WHERE s.engine_version=?
                ORDER BY o.horizon_days, s.effective_trade_date, s.id
                """,
                (self.active_engine_version,),
            ).fetchall()

        horizons: List[Dict[str, Any]] = []
        for horizon in sorted({int(row["horizon_days"]) for row in rows}):
            bucket = [row for row in rows if int(row["horizon_days"]) == horizon]
            hits = [int(row["directional_hit"]) for row in bucket if row["directional_hit"] is not None]
            returns = [float(row["return_pct"]) for row in bucket if row["return_pct"] is not None]
            buy_rows = [row for row in bucket if row["decision"] == "BUY_SETUP"]
            avoid_rows = [row for row in bucket if row["decision"] == "AVOID"]
            avoided_returns = [float(row["return_pct"]) for row in avoid_rows if row["return_pct"] is not None]
            excess_spy = [float(row["excess_vs_spy_pct"]) for row in bucket if row["excess_vs_spy_pct"] is not None]
            excess_qqq = [float(row["excess_vs_qqq_pct"]) for row in bucket if row["excess_vs_qqq_pct"] is not None]
            score_ic, score_ic_n = _spearman(
                (row["outcome_forecast_score"], row["return_pct"]) for row in bucket
            )
            score_excess_ic, score_excess_ic_n = _spearman(
                (row["outcome_forecast_score"], row["excess_vs_spy_pct"]) for row in bucket
            )
            opp_ic, opp_ic_n = _spearman(
                (row["opportunity_score"], row["return_pct"]) for row in bucket
            )

            regime_breakdown = []
            for regime in sorted({str(row["market_regime"] or "unknown") for row in bucket}):
                group = [row for row in bucket if str(row["market_regime"] or "unknown") == regime]
                group_hits = [int(row["directional_hit"]) for row in group if row["directional_hit"] is not None]
                regime_breakdown.append(
                    {
                        "regime": regime,
                        "samples": len(group),
                        "directional_hit_rate_pct": None
                        if not group_hits
                        else round(100.0 * sum(group_hits) / len(group_hits), 2),
                    }
                )

            horizons.append(
                {
                    "horizon_days": horizon,
                    "samples": len(bucket),
                    "mature": len(bucket) >= minimum,
                    "directional_samples": len(hits),
                    "directional_hit_rate_pct": None
                    if not hits
                    else round(100.0 * sum(hits) / len(hits), 2),
                    "avg_return_pct": None if not returns else round(statistics.fmean(returns), 4),
                    "avg_excess_vs_spy_pct": None
                    if not excess_spy
                    else round(statistics.fmean(excess_spy), 4),
                    "avg_excess_vs_qqq_pct": None
                    if not excess_qqq
                    else round(statistics.fmean(excess_qqq), 4),
                    "buy_setup_samples": len(buy_rows),
                    "buy_setup_hit_rate_pct": _return_hit_rate(buy_rows, positive=True),
                    "avoidance_samples": len(avoid_rows),
                    "avoidance_hit_rate_pct": _return_hit_rate(avoid_rows, positive=False),
                    "false_avoid_rate_pct": None
                    if not avoided_returns
                    else round(
                        100.0 * sum(1 for value in avoided_returns if value > 0.0)
                        / len(avoided_returns),
                        2,
                    ),
                    "avg_avoided_return_pct": None
                    if not avoided_returns
                    else round(statistics.fmean(avoided_returns), 4),
                    "forecast_score_ic_spearman": None if score_ic is None else round(score_ic, 4),
                    "forecast_score_ic_samples": score_ic_n,
                    "forecast_excess_spy_ic_spearman": None
                    if score_excess_ic is None
                    else round(score_excess_ic, 4),
                    "forecast_excess_spy_ic_samples": score_excess_ic_n,
                    "opportunity_ic_spearman": None if opp_ic is None else round(opp_ic, 4),
                    "opportunity_ic_samples": opp_ic_n,
                    "regime_breakdown": regime_breakdown,
                    "calibration": self._calibration(bucket, minimum),
                }
            )

        return {
            "schema_version": V6_SCHEMA_VERSION,
            "engine_version": self.active_engine_version,
            "minimum_samples": minimum,
            "status": "measurable"
            if any(item["mature"] for item in horizons)
            else "insufficient_data",
            "horizons": horizons,
        }
