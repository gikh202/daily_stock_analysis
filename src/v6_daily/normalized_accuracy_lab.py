from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from .accuracy_lab import (
    ACCURACY_LAB_VERSION,
    DEFAULT_COST_BPS,
    DEFAULT_MAX_HOLDING_BARS,
    DEFAULT_PROMOTION_MIN_SAMPLES,
    _alpha_features,
    _benchmark_return,
    _date_indices,
    _finite,
    _future_bars,
    _group_breakdown,
    _hit_metrics,
    _non_overlapping,
    _parse_plan,
    _research_state,
    _round,
    _shadow_metrics,
    _trade_group_breakdown,
    _trade_metrics,
    _utc_now,
    _variant_identity,
    build_shadow_forecasts,
    execution_policy_key,
    render_accuracy_lab_markdown,
    simulate_long_trade,
    write_accuracy_lab_report,
)


NORMALIZED_ACCURACY_LAB_SCHEMA_VERSION = "v6-normalized-accuracy-lab-v1"
SHADOW_FORECAST_TABLE = "v6_accuracy_shadow_forecasts"
SHADOW_OUTCOME_TABLE = "v6_accuracy_shadow_outcomes"
TRADE_OUTCOME_TABLE = "v6_accuracy_trade_outcomes"


def _connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (str(table),),
        ).fetchone()
        is not None
    )


def _require_normalized_core(conn: sqlite3.Connection) -> None:
    required = {
        "v6_forecast_runs",
        "v6_decision_runs",
        "v6_execution_plans",
        "v6_forecast_outcomes",
    }
    present = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    missing = sorted(required - present)
    if missing:
        raise RuntimeError(
            "normalized Accuracy Lab requires canonical V6 tables: " + ", ".join(missing)
        )


def _migrate_legacy_accuracy_history(conn: sqlite3.Connection) -> Dict[str, int]:
    """Copy historical lab facts without consulting legacy signal/outcome tables.

    Old Accuracy Lab tables key rows by the compatibility ``signal_id``. Stage 8
    already guarantees that value equals ``v6_forecast_runs.source_signal_id``.
    The migration therefore joins the old lab tables directly to normalized
    forecast identity and never needs ``v6_signals`` or ``v6_outcomes``.
    """
    migrated = {
        "shadow_forecasts": 0,
        "shadow_outcomes": 0,
        "trade_outcomes": 0,
    }

    if _table_exists(conn, "v6_shadow_forecasts"):
        cursor = conn.execute(
            f"""
            INSERT OR IGNORE INTO {SHADOW_FORECAST_TABLE}(
                forecast_run_id, source_signal_id, variant, horizon_days,
                generated_at, score, direction, evidence_coverage, profile_json
            )
            SELECT fr.id, fr.source_signal_id, old.variant, old.horizon_days,
                   old.generated_at, old.score, old.direction,
                   old.evidence_coverage, old.profile_json
            FROM v6_shadow_forecasts old
            JOIN v6_forecast_runs fr ON fr.source_signal_id=old.signal_id
            """
        )
        migrated["shadow_forecasts"] = max(0, int(cursor.rowcount))

    if _table_exists(conn, "v6_shadow_outcomes") and _table_exists(
        conn, "v6_shadow_forecasts"
    ):
        cursor = conn.execute(
            f"""
            INSERT OR IGNORE INTO {SHADOW_OUTCOME_TABLE}(
                shadow_forecast_id, evaluated_at, end_trade_date,
                start_price, end_price, return_pct, directional_hit,
                benchmark_spy_return_pct, excess_vs_spy_pct
            )
            SELECT current.id, old_out.evaluated_at, old_out.end_trade_date,
                   old_out.start_price, old_out.end_price, old_out.return_pct,
                   old_out.directional_hit, old_out.benchmark_spy_return_pct,
                   old_out.excess_vs_spy_pct
            FROM v6_shadow_outcomes old_out
            JOIN v6_shadow_forecasts old_forecast
              ON old_forecast.id=old_out.shadow_forecast_id
            JOIN v6_forecast_runs fr
              ON fr.source_signal_id=old_forecast.signal_id
            JOIN {SHADOW_FORECAST_TABLE} current
              ON current.forecast_run_id=fr.id
             AND current.variant=old_forecast.variant
             AND current.horizon_days=old_forecast.horizon_days
            """
        )
        migrated["shadow_outcomes"] = max(0, int(cursor.rowcount))

    if _table_exists(conn, "v6_trade_outcomes"):
        cursor = conn.execute(
            f"""
            INSERT OR IGNORE INTO {TRADE_OUTCOME_TABLE}(
                forecast_run_id, source_signal_id, execution_policy,
                evaluated_at, status, entry_trade_date, exit_trade_date,
                entry_price, exit_price, return_pct, r_multiple, win,
                exit_reason, holding_bars, mfe_pct, mae_pct,
                cost_bps, max_holding_bars
            )
            SELECT fr.id, fr.source_signal_id, old.execution_policy,
                   old.evaluated_at, old.status, old.entry_trade_date,
                   old.exit_trade_date, old.entry_price, old.exit_price,
                   old.return_pct, old.r_multiple, old.win, old.exit_reason,
                   old.holding_bars, old.mfe_pct, old.mae_pct,
                   old.cost_bps, old.max_holding_bars
            FROM v6_trade_outcomes old
            JOIN v6_forecast_runs fr ON fr.source_signal_id=old.signal_id
            """
        )
        migrated["trade_outcomes"] = max(0, int(cursor.rowcount))

    return migrated


def ensure_normalized_accuracy_lab_schema(v6_db_path: str | Path) -> Dict[str, Any]:
    with _connect(v6_db_path) as conn:
        _require_normalized_core(conn)
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {SHADOW_FORECAST_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                forecast_run_id INTEGER NOT NULL,
                source_signal_id INTEGER NOT NULL,
                variant TEXT NOT NULL,
                horizon_days INTEGER NOT NULL,
                generated_at TEXT NOT NULL,
                score REAL,
                direction TEXT NOT NULL,
                evidence_coverage REAL NOT NULL,
                profile_json TEXT NOT NULL,
                UNIQUE(forecast_run_id, variant, horizon_days),
                FOREIGN KEY(forecast_run_id)
                    REFERENCES v6_forecast_runs(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS {SHADOW_OUTCOME_TABLE} (
                shadow_forecast_id INTEGER PRIMARY KEY,
                evaluated_at TEXT NOT NULL,
                end_trade_date TEXT NOT NULL,
                start_price REAL NOT NULL,
                end_price REAL NOT NULL,
                return_pct REAL NOT NULL,
                directional_hit INTEGER,
                benchmark_spy_return_pct REAL,
                excess_vs_spy_pct REAL,
                FOREIGN KEY(shadow_forecast_id)
                    REFERENCES {SHADOW_FORECAST_TABLE}(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS {TRADE_OUTCOME_TABLE} (
                forecast_run_id INTEGER NOT NULL,
                source_signal_id INTEGER NOT NULL,
                execution_policy TEXT NOT NULL,
                evaluated_at TEXT NOT NULL,
                status TEXT NOT NULL,
                entry_trade_date TEXT,
                exit_trade_date TEXT,
                entry_price REAL,
                exit_price REAL,
                return_pct REAL,
                r_multiple REAL,
                win INTEGER,
                exit_reason TEXT,
                holding_bars INTEGER,
                mfe_pct REAL,
                mae_pct REAL,
                cost_bps REAL NOT NULL,
                max_holding_bars INTEGER NOT NULL,
                PRIMARY KEY(forecast_run_id, execution_policy),
                FOREIGN KEY(forecast_run_id)
                    REFERENCES v6_forecast_runs(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS ix_v6_accuracy_shadow_variant_horizon
                ON {SHADOW_FORECAST_TABLE}(variant, horizon_days, forecast_run_id);
            CREATE INDEX IF NOT EXISTS ix_v6_accuracy_shadow_outcomes_eval
                ON {SHADOW_OUTCOME_TABLE}(evaluated_at);
            CREATE INDEX IF NOT EXISTS ix_v6_accuracy_trade_policy
                ON {TRADE_OUTCOME_TABLE}(execution_policy, status, evaluated_at);
            """
        )
        migrated = _migrate_legacy_accuracy_history(conn)
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        quick = str(conn.execute("PRAGMA quick_check").fetchone()[0])
    if fk_errors:
        raise RuntimeError(
            "normalized Accuracy Lab foreign_key_check failed: " + repr(fk_errors[:3])
        )
    if quick.strip().lower() != "ok":
        raise RuntimeError(f"normalized Accuracy Lab quick_check failed: {quick}")
    return {
        "schema_version": NORMALIZED_ACCURACY_LAB_SCHEMA_VERSION,
        "source": "normalized_v6_tables",
        "legacy_signal_reads": 0,
        "legacy_outcome_reads": 0,
        "migration": migrated,
        "quick_check": quick,
        "foreign_key_errors": 0,
    }


def persist_shadow_forecasts(
    v6_db_path: str | Path,
    *,
    active_engine_version: str,
) -> int:
    ensure_normalized_accuracy_lab_schema(v6_db_path)
    inserted = 0
    with _connect(v6_db_path) as conn:
        forecasts = conn.execute(
            """
            SELECT id AS forecast_run_id, source_signal_id,
                   instrument_type, features_json
            FROM v6_forecast_runs
            WHERE engine_version=?
            ORDER BY source_signal_id
            """,
            (str(active_engine_version),),
        ).fetchall()
        for forecast in forecasts:
            features = _alpha_features(str(forecast["features_json"] or "{}"))
            if features is None:
                continue
            variants = build_shadow_forecasts(
                features,
                instrument_type=str(forecast["instrument_type"] or "STOCK"),
            )
            for variant_name, blocks in variants.items():
                for block in blocks.values():
                    weights = block.get("weights") or {}
                    variant = _variant_identity(variant_name, weights)
                    cursor = conn.execute(
                        f"""
                        INSERT OR IGNORE INTO {SHADOW_FORECAST_TABLE}(
                            forecast_run_id, source_signal_id, variant,
                            horizon_days, generated_at, score, direction,
                            evidence_coverage, profile_json
                        ) VALUES (?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            int(forecast["forecast_run_id"]),
                            int(forecast["source_signal_id"]),
                            variant,
                            int(block["horizon_days"]),
                            _utc_now(),
                            _finite(block.get("score")),
                            str(block.get("direction") or "neutral"),
                            float(block.get("evidence_coverage") or 0.0),
                            json.dumps(weights, sort_keys=True, separators=(",", ":")),
                        ),
                    )
                    inserted += max(0, int(cursor.rowcount))
    return inserted


def mature_shadow_outcomes(
    v6_db_path: str | Path,
    stock_db_path: str | Path,
    *,
    active_engine_version: str,
    neutral_band_pct: float = 2.0,
) -> Dict[str, int]:
    ensure_normalized_accuracy_lab_schema(v6_db_path)
    evaluated = 0
    pending = 0
    with _connect(v6_db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT shadow.id, shadow.horizon_days, shadow.direction,
                   fr.symbol AS code, fr.effective_trade_date,
                   fr.analysis_created_at, fr.baseline_price
            FROM {SHADOW_FORECAST_TABLE} shadow
            JOIN v6_forecast_runs fr ON fr.id=shadow.forecast_run_id
            LEFT JOIN {SHADOW_OUTCOME_TABLE} outcome
              ON outcome.shadow_forecast_id=shadow.id
            WHERE fr.engine_version=? AND outcome.shadow_forecast_id IS NULL
            ORDER BY shadow.id
            """,
            (str(active_engine_version),),
        ).fetchall()

        for row in rows:
            horizon = int(row["horizon_days"])
            analysis_date = str(
                row["effective_trade_date"] or row["analysis_created_at"] or ""
            )[:10]
            start = _finite(row["baseline_price"])
            if not analysis_date or start is None or start <= 0:
                pending += 1
                continue
            bars = _future_bars(
                stock_db_path,
                code=str(row["code"]),
                analysis_date=analysis_date,
                needed=horizon,
            )
            if len(bars) < horizon:
                pending += 1
                continue
            end = _finite(bars[-1].get("close"))
            if end is None or end <= 0:
                pending += 1
                continue
            return_pct = (end / start - 1.0) * 100.0
            direction = str(row["direction"] or "neutral").lower()
            if direction == "bullish":
                hit = int(return_pct > 0.0)
            elif direction == "bearish":
                hit = int(return_pct < 0.0)
            else:
                hit = int(abs(return_pct) <= abs(float(neutral_band_pct)))
            spy_return = _benchmark_return(
                stock_db_path,
                code="SPY",
                analysis_date=analysis_date,
                horizon=horizon,
            )
            excess = None if spy_return is None else return_pct - spy_return
            cursor = conn.execute(
                f"""
                INSERT OR IGNORE INTO {SHADOW_OUTCOME_TABLE}(
                    shadow_forecast_id, evaluated_at, end_trade_date,
                    start_price, end_price, return_pct, directional_hit,
                    benchmark_spy_return_pct, excess_vs_spy_pct
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(row["id"]),
                    _utc_now(),
                    str(bars[-1].get("date") or ""),
                    start,
                    end,
                    round(return_pct, 6),
                    hit,
                    _round(spy_return, 6),
                    _round(excess, 6),
                ),
            )
            evaluated += max(0, int(cursor.rowcount))
    return {"evaluated": evaluated, "not_yet_mature": pending}


def mature_trade_outcomes(
    v6_db_path: str | Path,
    stock_db_path: str | Path,
    *,
    active_engine_version: str,
    max_holding_bars: int = DEFAULT_MAX_HOLDING_BARS,
    cost_bps: float = DEFAULT_COST_BPS,
) -> Dict[str, Any]:
    ensure_normalized_accuracy_lab_schema(v6_db_path)
    evaluated = 0
    pending = 0
    max_holding = max(1, int(max_holding_bars))
    cost = max(0.0, float(cost_bps))
    policy = execution_policy_key(cost_bps=cost, max_holding_bars=max_holding)

    with _connect(v6_db_path) as conn:
        forecasts = conn.execute(
            f"""
            SELECT fr.id AS forecast_run_id, fr.source_signal_id,
                   fr.symbol AS code, fr.effective_trade_date,
                   fr.analysis_created_at, plan.plan_json
            FROM v6_forecast_runs fr
            JOIN v6_decision_runs decision ON decision.forecast_run_id=fr.id
            JOIN v6_execution_plans plan ON plan.decision_run_id=decision.id
            LEFT JOIN {TRADE_OUTCOME_TABLE} trade
              ON trade.forecast_run_id=fr.id AND trade.execution_policy=?
            WHERE fr.engine_version=?
              AND decision.deterministic_decision='BUY_SETUP'
              AND trade.forecast_run_id IS NULL
            ORDER BY fr.source_signal_id
            """,
            (policy, str(active_engine_version)),
        ).fetchall()

        for forecast in forecasts:
            analysis_date = str(
                forecast["effective_trade_date"]
                or forecast["analysis_created_at"]
                or ""
            )[:10]
            plan = _parse_plan(str(forecast["plan_json"] or "{}"))
            if not analysis_date or plan is None:
                result = {"status": "invalid_plan"}
            else:
                bars = _future_bars(
                    stock_db_path,
                    code=str(forecast["code"]),
                    analysis_date=analysis_date,
                    needed=max_holding,
                )
                if len(bars) < max_holding:
                    pending += 1
                    continue
                result = simulate_long_trade(bars, plan, cost_bps=cost)

            cursor = conn.execute(
                f"""
                INSERT OR IGNORE INTO {TRADE_OUTCOME_TABLE}(
                    forecast_run_id, source_signal_id, execution_policy,
                    evaluated_at, status, entry_trade_date, exit_trade_date,
                    entry_price, exit_price, return_pct, r_multiple, win,
                    exit_reason, holding_bars, mfe_pct, mae_pct,
                    cost_bps, max_holding_bars
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(forecast["forecast_run_id"]),
                    int(forecast["source_signal_id"]),
                    policy,
                    _utc_now(),
                    str(result.get("status") or "unknown"),
                    result.get("entry_trade_date"),
                    result.get("exit_trade_date"),
                    _finite(result.get("entry_price")),
                    _finite(result.get("exit_price")),
                    _finite(result.get("return_pct")),
                    _finite(result.get("r_multiple")),
                    result.get("win"),
                    result.get("exit_reason"),
                    result.get("holding_bars"),
                    _finite(result.get("mfe_pct")),
                    _finite(result.get("mae_pct")),
                    cost,
                    max_holding,
                ),
            )
            evaluated += max(0, int(cursor.rowcount))

    return {
        "evaluated": evaluated,
        "not_yet_mature": pending,
        "execution_policy": policy,
    }


def _champion_rows(
    v6_db_path: str | Path,
    *,
    active_engine_version: str,
) -> list[Dict[str, Any]]:
    with _connect(v6_db_path) as conn:
        rows = conn.execute(
            """
            SELECT fr.source_signal_id AS signal_id,
                   fr.symbol AS code, fr.effective_trade_date,
                   fr.instrument_type, fr.market_regime, outcome.horizon_days,
                   outcome.return_pct, outcome.directional_hit,
                   outcome.excess_vs_spy_pct
            FROM v6_forecast_outcomes outcome
            JOIN v6_forecast_runs fr ON fr.id=outcome.forecast_run_id
            WHERE fr.engine_version=?
            ORDER BY outcome.horizon_days, fr.symbol,
                     fr.effective_trade_date, fr.source_signal_id
            """,
            (str(active_engine_version),),
        ).fetchall()
    return [dict(row) for row in rows]


def _shadow_rows(
    v6_db_path: str | Path,
    *,
    active_engine_version: str,
) -> list[Dict[str, Any]]:
    with _connect(v6_db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT fr.source_signal_id AS signal_id,
                   fr.symbol AS code, fr.effective_trade_date,
                   fr.instrument_type, fr.market_regime, shadow.variant,
                   shadow.horizon_days, outcome.return_pct,
                   outcome.directional_hit, outcome.excess_vs_spy_pct
            FROM {SHADOW_OUTCOME_TABLE} outcome
            JOIN {SHADOW_FORECAST_TABLE} shadow
              ON shadow.id=outcome.shadow_forecast_id
            JOIN v6_forecast_runs fr ON fr.id=shadow.forecast_run_id
            WHERE fr.engine_version=?
            ORDER BY shadow.variant, shadow.horizon_days,
                     fr.symbol, fr.effective_trade_date, fr.source_signal_id
            """,
            (str(active_engine_version),),
        ).fetchall()
    return [dict(row) for row in rows]


def _champion_metrics(
    rows: Sequence[Mapping[str, Any]],
    *,
    stock_db_path: str | Path,
    min_samples: int,
) -> list[Dict[str, Any]]:
    indices = _date_indices(
        stock_db_path,
        [str(row.get("code") or "") for row in rows],
    )
    result: list[Dict[str, Any]] = []
    horizons = sorted(
        {
            int(row.get("horizon_days") or 0)
            for row in rows
            if int(row.get("horizon_days") or 0) > 0
        }
    )
    for horizon in horizons:
        bucket = [
            row for row in rows if int(row.get("horizon_days") or 0) == horizon
        ]
        independent_rows = _non_overlapping(
            bucket,
            horizon=horizon,
            date_indices=indices,
        )
        independent = _hit_metrics(independent_rows)
        result.append(
            {
                "horizon_days": horizon,
                "raw": _hit_metrics(bucket),
                "non_overlapping": independent,
                "research_state": _research_state(independent, min_samples),
                "by_instrument": _group_breakdown(bucket, "instrument_type"),
                "by_market_regime": _group_breakdown(bucket, "market_regime"),
            }
        )
    return result


def _strategy_metrics(
    v6_db_path: str | Path,
    min_samples: int,
    *,
    active_engine_version: str,
    execution_policy: str,
) -> Dict[str, Any]:
    with _connect(v6_db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT trade.*, fr.symbol AS code,
                   fr.instrument_type, fr.market_regime
            FROM {TRADE_OUTCOME_TABLE} trade
            JOIN v6_forecast_runs fr ON fr.id=trade.forecast_run_id
            WHERE fr.engine_version=? AND trade.execution_policy=?
            ORDER BY COALESCE(trade.entry_trade_date, trade.evaluated_at),
                     fr.source_signal_id
            """,
            (str(active_engine_version), execution_policy),
        ).fetchall()
    items = [dict(row) for row in rows]
    core = _trade_metrics(items)
    core.update(
        {
            "status": (
                "measurable"
                if int(core.get("filled_trades") or 0) >= max(3, int(min_samples))
                else "insufficient_data"
            ),
            "execution_policy": execution_policy,
            "by_instrument": _trade_group_breakdown(items, "instrument_type"),
            "by_market_regime": _trade_group_breakdown(items, "market_regime"),
            "execution_policy_description": (
                "BUY_SETUP only; target deferred on entry bar; stop-first on "
                "later same-bar ambiguity"
            ),
        }
    )
    return core


def build_normalized_accuracy_lab_report(
    v6_db_path: str | Path,
    stock_db_path: str | Path,
    *,
    active_engine_version: str,
    min_samples: int = 50,
    promotion_min_samples: int = DEFAULT_PROMOTION_MIN_SAMPLES,
    cost_bps: float = DEFAULT_COST_BPS,
    max_holding_bars: int = DEFAULT_MAX_HOLDING_BARS,
) -> Dict[str, Any]:
    policy_key = execution_policy_key(
        cost_bps=cost_bps,
        max_holding_bars=max_holding_bars,
    )
    champion = _champion_metrics(
        _champion_rows(v6_db_path, active_engine_version=active_engine_version),
        stock_db_path=stock_db_path,
        min_samples=min_samples,
    )
    shadow = _shadow_metrics(
        _shadow_rows(v6_db_path, active_engine_version=active_engine_version),
        stock_db_path=stock_db_path,
        champion=champion,
        promotion_min_samples=max(int(promotion_min_samples), int(min_samples)),
    )
    candidates = [
        {"variant": item["variant"], "horizon_days": item["horizon_days"]}
        for item in shadow
        if item.get("promotion_candidate")
    ]
    return {
        "version": ACCURACY_LAB_VERSION,
        "schema_version": NORMALIZED_ACCURACY_LAB_SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "status": (
            "measurable"
            if any(
                item.get("research_state") != "insufficient_data"
                for item in champion
            )
            else "insufficient_data"
        ),
        "minimum_samples": max(3, int(min_samples)),
        "promotion_min_samples": max(
            int(promotion_min_samples),
            int(min_samples),
        ),
        "champion": champion,
        "challengers": shadow,
        "promotion_candidates": candidates,
        "strategy": _strategy_metrics(
            v6_db_path,
            min_samples,
            active_engine_version=active_engine_version,
            execution_policy=policy_key,
        ),
        "source": {
            "mode": "normalized_only",
            "engine_version": str(active_engine_version),
            "forecast_source": "v6_forecast_runs",
            "outcome_source": "v6_forecast_outcomes",
            "legacy_signal_reads": 0,
            "legacy_outcome_reads": 0,
        },
        "policy": {
            "auto_promotion": False,
            "auto_weight_tuning": False,
            "non_overlapping_validation": True,
            "confidence_interval": "Wilson 95%",
            "shadow_variant_revision": "v1",
            "execution_policy": policy_key,
            "promotion_rule": (
                "research-only; requires >=2pp non-overlap hit-rate lift, "
                "non-inferior CI lower bound, lower bound >50%, and no worse "
                "SPY excess when available"
            ),
            "transaction_cost_bps_round_trip": max(0.0, float(cost_bps)),
            "trade_max_holding_bars": max(1, int(max_holding_bars)),
        },
    }


def run_normalized_accuracy_lab(
    v6_db_path: str | Path,
    stock_db_path: str | Path,
    *,
    report_dir: str | Path,
    active_engine_version: str,
    min_samples: int = 50,
    promotion_min_samples: int = DEFAULT_PROMOTION_MIN_SAMPLES,
    cost_bps: float = DEFAULT_COST_BPS,
    max_holding_bars: int = DEFAULT_MAX_HOLDING_BARS,
) -> Dict[str, Any]:
    schema = ensure_normalized_accuracy_lab_schema(v6_db_path)
    new_shadow = persist_shadow_forecasts(
        v6_db_path,
        active_engine_version=active_engine_version,
    )
    shadow_maturation = mature_shadow_outcomes(
        v6_db_path,
        stock_db_path,
        active_engine_version=active_engine_version,
    )
    trade_maturation = mature_trade_outcomes(
        v6_db_path,
        stock_db_path,
        active_engine_version=active_engine_version,
        max_holding_bars=max_holding_bars,
        cost_bps=cost_bps,
    )
    payload = build_normalized_accuracy_lab_report(
        v6_db_path,
        stock_db_path,
        active_engine_version=active_engine_version,
        min_samples=min_samples,
        promotion_min_samples=promotion_min_samples,
        cost_bps=cost_bps,
        max_holding_bars=max_holding_bars,
    )
    payload["run"] = {
        "new_shadow_forecasts": new_shadow,
        "new_shadow_outcomes": shadow_maturation["evaluated"],
        "shadow_not_yet_mature": shadow_maturation["not_yet_mature"],
        "new_trade_outcomes": trade_maturation["evaluated"],
        "trade_not_yet_mature": trade_maturation["not_yet_mature"],
        "execution_policy": trade_maturation["execution_policy"],
        "storage_source": "normalized_v6_tables",
        "legacy_consumer": False,
        "schema": schema,
    }
    output = Path(report_dir)
    payload["artifacts"] = {
        "json": str(output / "v6_accuracy_lab.json"),
        "markdown": str(output / "v6_accuracy_lab.md"),
    }
    write_accuracy_lab_report(payload, report_dir)
    return payload


# Compatible callable name for Stage 9 adapters. The active engine is explicit
# so research facts from different engine versions cannot be silently pooled.
def run_accuracy_lab(
    v6_db_path: str | Path,
    stock_db_path: str | Path,
    *,
    report_dir: str | Path,
    active_engine_version: str,
    min_samples: int = 50,
    promotion_min_samples: int = DEFAULT_PROMOTION_MIN_SAMPLES,
    cost_bps: float = DEFAULT_COST_BPS,
    max_holding_bars: int = DEFAULT_MAX_HOLDING_BARS,
) -> Dict[str, Any]:
    return run_normalized_accuracy_lab(
        v6_db_path,
        stock_db_path,
        report_dir=report_dir,
        active_engine_version=active_engine_version,
        min_samples=min_samples,
        promotion_min_samples=promotion_min_samples,
        cost_bps=cost_bps,
        max_holding_bars=max_holding_bars,
    )
