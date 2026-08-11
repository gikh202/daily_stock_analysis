from __future__ import annotations

import json
import sqlite3
import statistics
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .store import V6DailyStore, V6_SCHEMA_VERSION, _finite, _return_hit_rate, _spearman


NORMALIZED_READ_SCHEMA_VERSION = "v6-normalized-read-v1"


def _json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return list(parsed) if isinstance(parsed, list) else []


class NormalizedV6ReadStore:
    """Read-only business adapter backed exclusively by normalized V6 tables.

    ``source_signal_id`` is retained as the compatibility identity so callers
    can compare this view field-for-field with the versioned legacy read path.
    No SELECT in this class references ``v6_signals`` or ``v6_outcomes``.
    """

    def __init__(self, path: str, *, active_engine_version: str) -> None:
        self.path = Path(path)
        self.active_engine_version = str(active_engine_version or "").strip()
        if not self.active_engine_version:
            raise ValueError("active_engine_version is required")

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def quick_check(self) -> str:
        with self.connect() as conn:
            row = conn.execute("PRAGMA quick_check").fetchone()
        return str(row[0]) if row else "unknown"

    def foreign_key_errors(self) -> list[tuple[Any, ...]]:
        with self.connect() as conn:
            return [tuple(row) for row in conn.execute("PRAGMA foreign_key_check").fetchall()]

    def counts(self) -> Dict[str, int]:
        with self.connect() as conn:
            forecasts = int(
                conn.execute(
                    "SELECT COUNT(*) FROM v6_forecast_runs WHERE engine_version=?",
                    (self.active_engine_version,),
                ).fetchone()[0]
            )
            outcomes = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM v6_forecast_outcomes o
                    JOIN v6_forecast_runs f ON f.id=o.forecast_run_id
                    WHERE f.engine_version=?
                    """,
                    (self.active_engine_version,),
                ).fetchone()[0]
            )
        return {"signals": forecasts, "outcomes": outcomes}

    @staticmethod
    def _row_to_board(row: sqlite3.Row) -> Dict[str, Any]:
        item = dict(row)
        packet = _json_object(item.pop("decision_packet_json", "{}"))
        evidence = _json_object(packet.get("evidence"))
        forecast = _json_object(packet.get("forecast"))
        execution = _json_object(item.pop("plan_json", "{}"))
        if "invalidation" not in execution:
            execution["invalidation"] = list(execution.get("invalidations") or [])

        return {
            "id": int(item.get("source_signal_id") or 0),
            "analysis_history_id": int(item.get("analysis_history_id") or 0),
            "query_id": item.get("query_id"),
            "code": str(item.get("symbol") or "").strip().upper(),
            "analysis_created_at": item.get("analysis_created_at"),
            "v6_created_at": item.get("v6_created_at"),
            "engine_version": item.get("engine_version"),
            "direction": item.get("direction"),
            "forecast_score": item.get("forecast_score"),
            "decision": item.get("deterministic_decision"),
            "quality_score": item.get("quality_score"),
            "opportunity_score": item.get("opportunity_score"),
            "risk_score": item.get("risk_score"),
            "evidence_coverage": item.get("evidence_coverage"),
            "baseline_price": item.get("baseline_price"),
            "market_regime": item.get("market_regime"),
            "market_breadth": item.get("market_breadth"),
            "llm_health": item.get("llm_health"),
            "instrument_type": item.get("instrument_type"),
            "effective_trade_date": item.get("effective_trade_date"),
            "features": _json_object(item.get("features_json")),
            "trade_plan": execution,
            "catalysts": _json_list(evidence.get("catalysts")),
            "risks": _json_list(evidence.get("risks")),
            "limitations": _json_list(evidence.get("limitations")),
            "diagnostics": _json_object(item.get("diagnostics_json")),
            "horizon_forecasts": _json_object(forecast.get("horizons")),
            "context_features": _json_object(item.get("context_features_json")),
        }

    def latest_board(self) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT f.source_signal_id, f.analysis_history_id, f.query_id,
                       f.symbol, f.analysis_created_at, f.v6_created_at,
                       f.engine_version, f.direction, f.forecast_score,
                       d.deterministic_decision, d.quality_score,
                       d.opportunity_score, d.risk_score, d.evidence_coverage,
                       f.baseline_price, f.market_regime, f.market_breadth,
                       f.llm_health, f.instrument_type, f.effective_trade_date,
                       f.features_json, f.context_features_json, f.diagnostics_json,
                       d.decision_packet_json, p.plan_json
                FROM v6_forecast_runs f
                JOIN v6_decision_runs d ON d.forecast_run_id=f.id
                JOIN v6_execution_plans p ON p.decision_run_id=d.id
                WHERE f.engine_version=?
                  AND NOT EXISTS (
                    SELECT 1
                    FROM v6_forecast_runs newer
                    WHERE newer.engine_version=f.engine_version
                      AND newer.symbol=f.symbol
                      AND (
                        COALESCE(newer.effective_trade_date, substr(newer.analysis_created_at,1,10), '')
                          > COALESCE(f.effective_trade_date, substr(f.analysis_created_at,1,10), '')
                        OR (
                          COALESCE(newer.effective_trade_date, substr(newer.analysis_created_at,1,10), '')
                            = COALESCE(f.effective_trade_date, substr(f.analysis_created_at,1,10), '')
                          AND newer.analysis_created_at > f.analysis_created_at
                        )
                        OR (
                          COALESCE(newer.effective_trade_date, substr(newer.analysis_created_at,1,10), '')
                            = COALESCE(f.effective_trade_date, substr(f.analysis_created_at,1,10), '')
                          AND newer.analysis_created_at = f.analysis_created_at
                          AND newer.source_signal_id > f.source_signal_id
                        )
                      )
                  )
                ORDER BY
                    CASE d.deterministic_decision
                        WHEN 'BUY_SETUP' THEN 0 WHEN 'WATCH' THEN 1
                        WHEN 'WAIT' THEN 2 WHEN 'AVOID' THEN 3 ELSE 4 END,
                    COALESCE(d.opportunity_score, -1) DESC,
                    COALESCE(d.risk_score, 101) ASC,
                    f.symbol ASC
                """,
                (self.active_engine_version,),
            ).fetchall()
        return [self._row_to_board(row) for row in rows]

    def previous_for_code(self, code: str, latest_id: int) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            current = conn.execute(
                """
                SELECT effective_trade_date, analysis_created_at
                FROM v6_forecast_runs
                WHERE source_signal_id=? AND engine_version=?
                """,
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
                SELECT f.source_signal_id, f.analysis_history_id, f.query_id,
                       f.symbol, f.analysis_created_at, f.v6_created_at,
                       f.engine_version, f.direction, f.forecast_score,
                       d.deterministic_decision, d.quality_score,
                       d.opportunity_score, d.risk_score, d.evidence_coverage,
                       f.baseline_price, f.market_regime, f.market_breadth,
                       f.llm_health, f.instrument_type, f.effective_trade_date,
                       f.features_json, f.context_features_json, f.diagnostics_json,
                       d.decision_packet_json, p.plan_json
                FROM v6_forecast_runs f
                JOIN v6_decision_runs d ON d.forecast_run_id=f.id
                JOIN v6_execution_plans p ON p.decision_run_id=d.id
                WHERE f.symbol=? AND f.engine_version=?
                  AND (
                    COALESCE(f.effective_trade_date, substr(f.analysis_created_at,1,10), '') < ?
                    OR (
                      COALESCE(f.effective_trade_date, substr(f.analysis_created_at,1,10), '') = ?
                      AND f.analysis_created_at < ?
                    )
                    OR (
                      COALESCE(f.effective_trade_date, substr(f.analysis_created_at,1,10), '') = ?
                      AND f.analysis_created_at = ?
                      AND f.source_signal_id < ?
                    )
                  )
                ORDER BY COALESCE(f.effective_trade_date, substr(f.analysis_created_at,1,10), '') DESC,
                         f.analysis_created_at DESC, f.source_signal_id DESC
                LIMIT 1
                """,
                (
                    str(code).strip().upper(), self.active_engine_version,
                    current_date, current_date, str(current["analysis_created_at"] or ""),
                    current_date, str(current["analysis_created_at"] or ""), int(latest_id),
                ),
            ).fetchone()
        return None if row is None else self._row_to_board(row)

    def daily_deltas(self) -> List[Dict[str, Any]]:
        deltas: List[Dict[str, Any]] = []
        for item in self.latest_board():
            previous = self.previous_for_code(item["code"], int(item["id"]))
            if previous is None:
                continue

            def delta(field: str) -> Optional[float]:
                now = _finite(item.get(field))
                before = _finite(previous.get(field))
                return None if now is None or before is None else round(now - before, 2)

            deltas.append(
                {
                    "code": item["code"],
                    "decision_before": previous.get("decision"),
                    "decision_after": item.get("decision"),
                    "direction_before": previous.get("direction"),
                    "direction_after": item.get("direction"),
                    "opportunity_delta": delta("opportunity_score"),
                    "risk_delta": delta("risk_score"),
                    "forecast_delta": delta("forecast_score"),
                }
            )
        return deltas

    def scoreboard(self, *, min_samples: int = 50) -> Dict[str, Any]:
        minimum = max(3, int(min_samples))
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT f.direction AS signal_direction,
                       d.deterministic_decision AS decision,
                       f.market_regime, f.instrument_type, d.opportunity_score,
                       o.horizon_days, o.return_pct, o.mfe_pct, o.mae_pct,
                       o.directional_hit, o.direction_used,
                       COALESCE(o.forecast_score, f.forecast_score) AS outcome_forecast_score,
                       o.excess_vs_spy_pct, o.excess_vs_qqq_pct
                FROM v6_forecast_outcomes o
                JOIN v6_forecast_runs f ON f.id=o.forecast_run_id
                JOIN v6_decision_runs d ON d.forecast_run_id=f.id
                WHERE f.engine_version=?
                ORDER BY o.horizon_days, f.effective_trade_date, f.source_signal_id
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
                        / len(avoided_returns), 2
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
                    "calibration": V6DailyStore._calibration(bucket, minimum),
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
