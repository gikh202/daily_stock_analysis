from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


NORMALIZED_MANIFEST_SCHEMA_VERSION = "v6-normalized-manifest-v1"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _board_signal_ids(payload: Mapping[str, Any]) -> list[int]:
    result: list[int] = []
    for item in payload.get("board") or []:
        if not isinstance(item, Mapping):
            continue
        try:
            signal_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        if signal_id > 0 and signal_id not in result:
            result.append(signal_id)
    return result


def _board_symbols(payload: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    for item in payload.get("board") or []:
        if not isinstance(item, Mapping):
            continue
        code = str(item.get("code") or "").strip().upper()
        if code and code not in result:
            result.append(code)
    return result


class NormalizedV6ManifestStore:
    """Attach LIVE run identity using only normalized canonical facts.

    Stage 5 used a legacy-to-normalized reconciliation pass to build a manifest.
    Stage 9 no longer needs that read dependency: normalized forecast/decision/
    execution/outcome tables are already canonical, so the manifest is derived
    from them directly and only attaches membership to rows that do not yet have
    a manifest identity.
    """

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def persist_snapshot(
        self,
        payload: Mapping[str, Any],
        *,
        source_engine_version: str,
        report_date: str,
        run_mode: str = "LIVE",
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        engine = str(source_engine_version or "").strip()
        if not engine:
            raise ValueError("source_engine_version is required")
        mode = str(run_mode or "LIVE").strip().upper()
        if mode not in {"LIVE", "REPLAY", "SHADOW"}:
            raise ValueError(f"unsupported run_mode: {run_mode!r}")
        report_day = str(report_date or "").strip()[:10]
        if not report_day:
            raise ValueError("report_date is required")

        board_ids = _board_signal_ids(payload)
        board_symbols = _board_symbols(payload)

        with _connect(self.path) as conn:
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            required = {
                "v6_run_manifests",
                "v6_forecast_runs",
                "v6_decision_runs",
                "v6_execution_plans",
                "v6_forecast_outcomes",
            }
            missing = sorted(required - tables)
            if missing:
                raise RuntimeError(
                    "normalized manifest store missing canonical tables: "
                    + ", ".join(missing)
                )

            forecast_rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT source_signal_id, analysis_history_id, symbol,
                           effective_trade_date, analysis_created_at,
                           direction, forecast_score, baseline_price
                    FROM v6_forecast_runs
                    WHERE engine_version=?
                    ORDER BY source_signal_id
                    """,
                    (engine,),
                ).fetchall()
            ]
            outcome_rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT outcome.source_outcome_id, outcome.source_signal_id,
                           outcome.horizon_days, outcome.end_trade_date,
                           outcome.return_pct, outcome.directional_hit,
                           outcome.excess_vs_spy_pct
                    FROM v6_forecast_outcomes outcome
                    JOIN v6_forecast_runs forecast
                      ON forecast.id=outcome.forecast_run_id
                    WHERE forecast.engine_version=?
                    ORDER BY outcome.source_outcome_id
                    """,
                    (engine,),
                ).fetchall()
            ]
            source_snapshot_hash = _stable_hash(
                {
                    "engine_version": engine,
                    "forecasts": forecast_rows,
                    "outcomes": outcome_rows,
                }
            )
            run_key = _stable_hash(
                {
                    "schema_version": NORMALIZED_MANIFEST_SCHEMA_VERSION,
                    "report_date": report_day,
                    "run_mode": mode,
                    "engine_version": engine,
                    "source_snapshot_hash": source_snapshot_hash,
                    "board_ids": board_ids,
                }
            )
            manifest_metadata = {
                **dict(metadata or {}),
                "manifest_source": "normalized_v6_tables",
                "legacy_reference_used": False,
                "board_signal_ids": board_ids,
            }
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO v6_run_manifests(
                    schema_version, run_key, created_at, report_date, run_mode,
                    engine_version, payload_version, source_snapshot_hash,
                    source_signal_count, source_outcome_count,
                    board_symbols_json, metadata_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    NORMALIZED_MANIFEST_SCHEMA_VERSION,
                    run_key,
                    str(payload.get("generated_at") or ""),
                    report_day,
                    mode,
                    engine,
                    str(payload.get("version") or "") or None,
                    source_snapshot_hash,
                    len(forecast_rows),
                    len(outcome_rows),
                    _json(board_symbols),
                    _json(manifest_metadata),
                ),
            )
            manifest_inserted = max(0, int(cursor.rowcount)) > 0
            manifest_row = conn.execute(
                "SELECT id FROM v6_run_manifests WHERE run_key=?",
                (run_key,),
            ).fetchone()
            if manifest_row is None:
                raise RuntimeError("normalized manifest insert/readback failed")
            manifest_id = int(manifest_row["id"])

            if board_ids:
                placeholders = ",".join("?" for _ in board_ids)
                params = (manifest_id, engine, *board_ids)
                conn.execute(
                    f"""
                    UPDATE v6_forecast_runs
                    SET run_manifest_id=?
                    WHERE engine_version=?
                      AND source_signal_id IN ({placeholders})
                      AND run_manifest_id IS NULL
                    """,
                    params,
                )
                conn.execute(
                    f"""
                    UPDATE v6_decision_runs
                    SET run_manifest_id=?
                    WHERE forecast_run_id IN (
                        SELECT id FROM v6_forecast_runs
                        WHERE engine_version=?
                          AND source_signal_id IN ({placeholders})
                    )
                      AND run_manifest_id IS NULL
                    """,
                    params,
                )

            forecast_runs = int(
                conn.execute(
                    "SELECT COUNT(*) FROM v6_forecast_runs WHERE engine_version=?",
                    (engine,),
                ).fetchone()[0]
            )
            decision_runs = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM v6_decision_runs decision
                    JOIN v6_forecast_runs forecast
                      ON forecast.id=decision.forecast_run_id
                    WHERE forecast.engine_version=?
                    """,
                    (engine,),
                ).fetchone()[0]
            )
            execution_plans = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM v6_execution_plans plan
                    JOIN v6_decision_runs decision ON decision.id=plan.decision_run_id
                    JOIN v6_forecast_runs forecast
                      ON forecast.id=decision.forecast_run_id
                    WHERE forecast.engine_version=?
                    """,
                    (engine,),
                ).fetchone()[0]
            )
            forecast_outcomes = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM v6_forecast_outcomes outcome
                    JOIN v6_forecast_runs forecast
                      ON forecast.id=outcome.forecast_run_id
                    WHERE forecast.engine_version=?
                    """,
                    (engine,),
                ).fetchone()[0]
            )
            fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
            quick = str(conn.execute("PRAGMA quick_check").fetchone()[0])

        parity_exact = (
            forecast_runs == decision_runs == execution_plans
            and forecast_outcomes == len(outcome_rows)
            and not fk_errors
            and quick.strip().lower() == "ok"
        )
        if not parity_exact:
            raise RuntimeError(
                "normalized manifest parity failed: "
                f"forecast={forecast_runs} decision={decision_runs} "
                f"plans={execution_plans} outcomes={forecast_outcomes} "
                f"fk={len(fk_errors)} quick={quick!r}"
            )
        return {
            "schema_version": NORMALIZED_MANIFEST_SCHEMA_VERSION,
            "run_manifest_id": manifest_id,
            "manifest_inserted": manifest_inserted,
            "run_mode": mode,
            "engine_version": engine,
            "source_snapshot_hash": source_snapshot_hash,
            "source_signals": forecast_runs,
            "forecast_runs": forecast_runs,
            "decision_runs": decision_runs,
            "execution_plans": execution_plans,
            "source_outcomes": forecast_outcomes,
            "forecast_outcomes": forecast_outcomes,
            "parity": "exact",
            "quick_check": quick,
            "foreign_key_errors": 0,
            "legacy_reference_used": False,
            "source_mode": "normalized_only",
        }
