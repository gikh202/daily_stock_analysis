from __future__ import annotations

import json
import math
import sqlite3
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import V6Signal


V6_SCHEMA_VERSION = "v6.1-accuracy.1"
DEFAULT_HORIZONS = (5, 10, 20)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_date(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else None


def _average_ranks(values: Sequence[float]) -> List[float]:
    indexed = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(indexed):
        end = cursor + 1
        while end < len(indexed) and indexed[end][1] == indexed[cursor][1]:
            end += 1
        rank = (cursor + 1 + end) / 2.0
        for position in range(cursor, end):
            ranks[indexed[position][0]] = rank
        cursor = end
    return ranks


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    mx = statistics.fmean(xs)
    my = statistics.fmean(ys)
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    denom = math.sqrt(sum(x * x for x in dx) * sum(y * y for y in dy))
    if denom <= 0:
        return None
    return sum(x * y for x, y in zip(dx, dy)) / denom


def _spearman(pairs: Iterable[Tuple[Any, Any]]) -> Tuple[Optional[float], int]:
    clean: List[Tuple[float, float]] = []
    for left, right in pairs:
        x = _finite(left)
        y = _finite(right)
        if x is not None and y is not None:
            clean.append((x, y))
    if len(clean) < 3:
        return None, len(clean)
    xs = [x for x, _ in clean]
    ys = [y for _, y in clean]
    return _pearson(_average_ranks(xs), _average_ranks(ys)), len(clean)


def _return_hit_rate(rows: Sequence[sqlite3.Row], *, positive: bool) -> Optional[float]:
    returns = [value for value in (_finite(row["return_pct"]) for row in rows) if value is not None]
    if not returns:
        return None
    hits = sum(1 for value in returns if (value > 0.0 if positive else value <= 0.0))
    return round(100.0 * hits / len(returns), 2)


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column_if_missing(conn: sqlite3.Connection, table: str, name: str, definition: str) -> None:
    if name not in _column_names(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


class V6DailyStore:
    """Independent V6 persistence with migration-safe V6.1 accuracy fields."""

    def __init__(self, path: str = "v6_data/v6_daily.db") -> None:
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
                CREATE TABLE IF NOT EXISTS v6_schema_meta (
                    version TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS v6_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    analysis_history_id INTEGER NOT NULL UNIQUE,
                    query_id TEXT,
                    code TEXT NOT NULL,
                    analysis_created_at TEXT NOT NULL,
                    v6_created_at TEXT NOT NULL,
                    engine_version TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    forecast_score REAL,
                    decision TEXT NOT NULL,
                    quality_score REAL,
                    opportunity_score REAL,
                    risk_score REAL,
                    evidence_coverage REAL NOT NULL,
                    baseline_price REAL NOT NULL,
                    market_regime TEXT,
                    market_breadth TEXT,
                    model_used TEXT,
                    llm_health TEXT NOT NULL,
                    features_json TEXT NOT NULL,
                    trade_plan_json TEXT NOT NULL,
                    catalysts_json TEXT NOT NULL,
                    risks_json TEXT NOT NULL,
                    limitations_json TEXT NOT NULL,
                    diagnostics_json TEXT NOT NULL,
                    instrument_type TEXT DEFAULT 'STOCK',
                    effective_trade_date TEXT,
                    signal_key TEXT,
                    horizon_forecasts_json TEXT DEFAULT '{}',
                    context_features_json TEXT DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS v6_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id INTEGER NOT NULL,
                    horizon_days INTEGER NOT NULL,
                    evaluated_at TEXT NOT NULL,
                    end_trade_date TEXT NOT NULL,
                    start_price REAL NOT NULL,
                    end_price REAL NOT NULL,
                    return_pct REAL NOT NULL,
                    mfe_pct REAL,
                    mae_pct REAL,
                    directional_hit INTEGER,
                    forecast_score REAL,
                    direction_used TEXT,
                    benchmark_spy_return_pct REAL,
                    benchmark_qqq_return_pct REAL,
                    excess_vs_spy_pct REAL,
                    excess_vs_qqq_pct REAL,
                    UNIQUE(signal_id, horizon_days),
                    FOREIGN KEY(signal_id) REFERENCES v6_signals(id) ON DELETE CASCADE
                );
                """
            )
            for name, definition in (
                ("instrument_type", "TEXT DEFAULT 'STOCK'"),
                ("effective_trade_date", "TEXT"),
                ("signal_key", "TEXT"),
                ("horizon_forecasts_json", "TEXT DEFAULT '{}'"),
                ("context_features_json", "TEXT DEFAULT '{}'"),
            ):
                _add_column_if_missing(conn, "v6_signals", name, definition)
            for name, definition in (
                ("forecast_score", "REAL"),
                ("direction_used", "TEXT"),
                ("benchmark_spy_return_pct", "REAL"),
                ("benchmark_qqq_return_pct", "REAL"),
                ("excess_vs_spy_pct", "REAL"),
                ("excess_vs_qqq_pct", "REAL"),
            ):
                _add_column_if_missing(conn, "v6_outcomes", name, definition)

            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS ix_v6_signals_code_time
                    ON v6_signals(code, analysis_created_at);
                CREATE INDEX IF NOT EXISTS ix_v6_signals_decision_time
                    ON v6_signals(decision, analysis_created_at);
                CREATE UNIQUE INDEX IF NOT EXISTS ux_v6_signals_signal_key
                    ON v6_signals(signal_key) WHERE signal_key IS NOT NULL;
                CREATE INDEX IF NOT EXISTS ix_v6_outcomes_horizon
                    ON v6_outcomes(horizon_days, evaluated_at);
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO v6_schema_meta(version, applied_at) VALUES (?, ?)",
                (V6_SCHEMA_VERSION, _utc_now()),
            )

    def quick_check(self) -> str:
        with self.connect() as conn:
            row = conn.execute("PRAGMA quick_check").fetchone()
        return str(row[0]) if row else "unknown"

    def counts(self) -> Dict[str, int]:
        with self.connect() as conn:
            signals = int(conn.execute("SELECT COUNT(*) FROM v6_signals").fetchone()[0])
            outcomes = int(conn.execute("SELECT COUNT(*) FROM v6_outcomes").fetchone()[0])
        return {"signals": signals, "outcomes": outcomes}

    @staticmethod
    def signal_key(code: str, effective_trade_date: Any, engine_version: str) -> str:
        date = _parse_date(effective_trade_date) or "unknown-date"
        return f"{str(code or '').strip().upper()}|{date}|{str(engine_version or '').strip()}"

    def has_analysis_history_id(self, history_id: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM v6_signals WHERE analysis_history_id=? LIMIT 1",
                (int(history_id),),
            ).fetchone()
        return row is not None

    def has_signal_key(self, code: str, effective_trade_date: Any, engine_version: str) -> bool:
        key = self.signal_key(code, effective_trade_date, engine_version)
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM v6_signals WHERE signal_key=? LIMIT 1", (key,)).fetchone()
        return row is not None

    def save_signal(self, signal: V6Signal, *, engine_version: str) -> bool:
        effective = signal.effective_trade_date or _parse_date(signal.analysis_created_at)
        key = self.signal_key(signal.code, effective, engine_version)
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO v6_signals(
                    analysis_history_id, query_id, code, analysis_created_at,
                    v6_created_at, engine_version, direction, forecast_score,
                    decision, quality_score, opportunity_score, risk_score,
                    evidence_coverage, baseline_price, market_regime, market_breadth,
                    model_used, llm_health, features_json, trade_plan_json,
                    catalysts_json, risks_json, limitations_json, diagnostics_json,
                    instrument_type, effective_trade_date, signal_key,
                    horizon_forecasts_json, context_features_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(signal.analysis_history_id), signal.query_id, signal.code,
                    signal.analysis_created_at, _utc_now(), engine_version,
                    signal.direction, _finite(signal.forecast_score), signal.decision,
                    _finite(signal.quality_score), _finite(signal.opportunity_score),
                    _finite(signal.risk_score), float(signal.evidence_coverage),
                    float(signal.baseline_price), signal.market_regime, signal.market_breadth,
                    signal.model_used, signal.llm_health, _json(signal.features),
                    _json(signal.trade_plan), _json(list(signal.catalysts)),
                    _json(list(signal.risks)), _json(list(signal.limitations)),
                    _json(signal.diagnostics), str(signal.instrument_type or "STOCK").upper(),
                    effective, key, _json(signal.horizon_forecasts),
                    _json(signal.context_features),
                ),
            )
        return cursor.rowcount > 0

    def evaluated_horizons(self, signal_id: int) -> set[int]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT horizon_days FROM v6_outcomes WHERE signal_id=?",
                (int(signal_id),),
            ).fetchall()
        return {int(row[0]) for row in rows}

    def all_signals(self) -> List[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute("SELECT * FROM v6_signals ORDER BY analysis_created_at ASC, id ASC").fetchall())

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
        direction: str,
        neutral_band_pct: float = 2.0,
        forecast_score: Optional[float] = None,
        benchmark_spy_return_pct: Optional[float] = None,
        benchmark_qqq_return_pct: Optional[float] = None,
    ) -> bool:
        return_pct = (end_price / start_price - 1.0) * 100.0
        mfe = None if max_high is None else (max_high / start_price - 1.0) * 100.0
        mae = None if min_low is None else (min_low / start_price - 1.0) * 100.0
        normalized = str(direction or "").strip().lower()
        if normalized == "bullish":
            hit = int(return_pct > 0.0)
        elif normalized == "bearish":
            hit = int(return_pct < 0.0)
        elif normalized == "neutral":
            hit = int(abs(return_pct) <= abs(float(neutral_band_pct)))
        else:
            hit = None
        excess_spy = None if benchmark_spy_return_pct is None else return_pct - benchmark_spy_return_pct
        excess_qqq = None if benchmark_qqq_return_pct is None else return_pct - benchmark_qqq_return_pct

        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO v6_outcomes(
                    signal_id, horizon_days, evaluated_at, end_trade_date,
                    start_price, end_price, return_pct, mfe_pct, mae_pct,
                    directional_hit, forecast_score, direction_used,
                    benchmark_spy_return_pct, benchmark_qqq_return_pct,
                    excess_vs_spy_pct, excess_vs_qqq_pct
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    int(signal_id), int(horizon_days), _utc_now(), end_trade_date,
                    float(start_price), float(end_price), round(return_pct, 6),
                    None if mfe is None else round(mfe, 6),
                    None if mae is None else round(mae, 6), hit,
                    _finite(forecast_score), normalized,
                    _finite(benchmark_spy_return_pct), _finite(benchmark_qqq_return_pct),
                    None if excess_spy is None else round(excess_spy, 6),
                    None if excess_qqq is None else round(excess_qqq, 6),
                ),
            )
        return cursor.rowcount > 0

    def latest_board(self) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT s.* FROM v6_signals s
                JOIN (SELECT code, MAX(id) AS max_id FROM v6_signals GROUP BY code) latest
                  ON latest.max_id=s.id
                ORDER BY
                    CASE s.decision WHEN 'BUY_SETUP' THEN 0 WHEN 'WATCH' THEN 1
                        WHEN 'WAIT' THEN 2 WHEN 'AVOID' THEN 3 ELSE 4 END,
                    COALESCE(s.opportunity_score, -1) DESC,
                    COALESCE(s.risk_score, 101) ASC, s.code ASC
                """
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def previous_for_code(self, code: str, latest_id: int) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM v6_signals WHERE code=? AND id<? ORDER BY id DESC LIMIT 1",
                (str(code), int(latest_id)),
            ).fetchone()
        return None if row is None else self._row_to_dict(row)

    def daily_deltas(self) -> List[Dict[str, Any]]:
        deltas: List[Dict[str, Any]] = []
        for item in self.latest_board():
            previous = self.previous_for_code(item["code"], int(item["id"]))
            if previous is None:
                continue
            def delta(field: str) -> Optional[float]:
                now, before = _finite(item.get(field)), _finite(previous.get(field))
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

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        item = dict(row)
        mapping = {
            "features_json": "features",
            "trade_plan_json": "trade_plan",
            "catalysts_json": "catalysts",
            "risks_json": "risks",
            "limitations_json": "limitations",
            "diagnostics_json": "diagnostics",
            "horizon_forecasts_json": "horizon_forecasts",
            "context_features_json": "context_features",
        }
        for field, target in mapping.items():
            try:
                item[target] = json.loads(item.get(field) or "null")
            except (TypeError, ValueError, json.JSONDecodeError):
                item[target] = None
        return item

    @staticmethod
    def _calibration(bucket: Sequence[sqlite3.Row], minimum: int) -> Dict[str, Any]:
        ranges = ((0, 40), (40, 50), (50, 60), (60, 70), (70, 101))
        result = []
        for low, high in ranges:
            rows = [row for row in bucket if _finite(row["outcome_forecast_score"]) is not None and low <= float(row["outcome_forecast_score"]) < high]
            returns = [float(row["return_pct"]) for row in rows if row["return_pct"] is not None]
            if not rows:
                continue
            result.append(
                {
                    "score_range": f"{low}-{high if high <= 100 else 100}",
                    "samples": len(rows),
                    "mature": len(rows) >= minimum,
                    "positive_return_rate_pct": None if not returns else round(100.0 * sum(1 for value in returns if value > 0) / len(returns), 2),
                    "avg_return_pct": None if not returns else round(statistics.fmean(returns), 4),
                }
            )
        return {"status": "measurable" if any(item["mature"] for item in result) else "insufficient_data", "buckets": result}

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
                FROM v6_outcomes o JOIN v6_signals s ON s.id=o.signal_id
                ORDER BY o.horizon_days, s.effective_trade_date, s.id
                """
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
            score_ic, score_ic_n = _spearman((row["outcome_forecast_score"], row["return_pct"]) for row in bucket)
            score_excess_ic, score_excess_ic_n = _spearman((row["outcome_forecast_score"], row["excess_vs_spy_pct"]) for row in bucket)
            opp_ic, opp_ic_n = _spearman((row["opportunity_score"], row["return_pct"]) for row in bucket)

            regime_breakdown = []
            for regime in sorted({str(row["market_regime"] or "unknown") for row in bucket}):
                group = [row for row in bucket if str(row["market_regime"] or "unknown") == regime]
                group_hits = [int(row["directional_hit"]) for row in group if row["directional_hit"] is not None]
                regime_breakdown.append({
                    "regime": regime,
                    "samples": len(group),
                    "directional_hit_rate_pct": None if not group_hits else round(100.0 * sum(group_hits) / len(group_hits), 2),
                })

            horizons.append(
                {
                    "horizon_days": horizon,
                    "samples": len(bucket),
                    "mature": len(bucket) >= minimum,
                    "directional_samples": len(hits),
                    "directional_hit_rate_pct": None if not hits else round(100.0 * sum(hits) / len(hits), 2),
                    "avg_return_pct": None if not returns else round(statistics.fmean(returns), 4),
                    "avg_excess_vs_spy_pct": None if not excess_spy else round(statistics.fmean(excess_spy), 4),
                    "avg_excess_vs_qqq_pct": None if not excess_qqq else round(statistics.fmean(excess_qqq), 4),
                    "buy_setup_samples": len(buy_rows),
                    "buy_setup_hit_rate_pct": _return_hit_rate(buy_rows, positive=True),
                    "avoidance_samples": len(avoid_rows),
                    "avoidance_hit_rate_pct": _return_hit_rate(avoid_rows, positive=False),
                    "false_avoid_rate_pct": None if not avoided_returns else round(100.0 * sum(1 for value in avoided_returns if value > 0.0) / len(avoided_returns), 2),
                    "avg_avoided_return_pct": None if not avoided_returns else round(statistics.fmean(avoided_returns), 4),
                    "forecast_score_ic_spearman": None if score_ic is None else round(score_ic, 4),
                    "forecast_score_ic_samples": score_ic_n,
                    "forecast_excess_spy_ic_spearman": None if score_excess_ic is None else round(score_excess_ic, 4),
                    "forecast_excess_spy_ic_samples": score_excess_ic_n,
                    "opportunity_ic_spearman": None if opp_ic is None else round(opp_ic, 4),
                    "opportunity_ic_samples": opp_ic_n,
                    "regime_breakdown": regime_breakdown,
                    "calibration": self._calibration(bucket, minimum),
                }
            )

        return {
            "schema_version": V6_SCHEMA_VERSION,
            "minimum_samples": minimum,
            "status": "measurable" if any(item["mature"] for item in horizons) else "insufficient_data",
            "horizons": horizons,
        }


def _future_bars(stock_db_path: str, *, code: str, analysis_date: str, needed: int) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(f"file:{Path(stock_db_path)}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT date, high, low, close FROM stock_daily WHERE code=? AND date>? ORDER BY date ASC LIMIT ?",
            (str(code), analysis_date, max(1, int(needed))),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _benchmark_return(stock_db_path: str, *, code: str, analysis_date: str, horizon: int) -> Optional[float]:
    conn = sqlite3.connect(f"file:{Path(stock_db_path)}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        start_row = conn.execute(
            "SELECT close FROM stock_daily WHERE code=? AND date<=? ORDER BY date DESC LIMIT 1",
            (code, analysis_date),
        ).fetchone()
        future = conn.execute(
            "SELECT close FROM stock_daily WHERE code=? AND date>? ORDER BY date ASC LIMIT ?",
            (code, analysis_date, int(horizon)),
        ).fetchall()
    finally:
        conn.close()
    if start_row is None or len(future) < horizon:
        return None
    start = _finite(start_row["close"])
    end = _finite(future[-1]["close"])
    if start is None or end is None or start <= 0:
        return None
    return round((end / start - 1.0) * 100.0, 6)


def mature_outcomes(
    store: V6DailyStore,
    stock_db_path: str,
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    neutral_band_pct: float = 2.0,
) -> Dict[str, int]:
    normalized = sorted({int(value) for value in horizons if int(value) > 0})
    if not normalized:
        return {"evaluated": 0, "not_yet_mature": 0}

    evaluated = 0
    pending = 0
    max_horizon = max(normalized)
    for signal in store.all_signals():
        done = store.evaluated_horizons(int(signal["id"]))
        needed = [h for h in normalized if h not in done]
        if not needed:
            continue
        analysis_date = _parse_date(signal["effective_trade_date"]) or _parse_date(signal["analysis_created_at"])
        if analysis_date is None:
            pending += len(needed)
            continue
        bars = _future_bars(stock_db_path, code=str(signal["code"]), analysis_date=analysis_date, needed=max_horizon)
        start = _finite(signal["baseline_price"])
        if start is None or start <= 0:
            pending += len(needed)
            continue
        try:
            forecasts = json.loads(signal["horizon_forecasts_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            forecasts = {}

        for horizon in needed:
            if len(bars) < horizon:
                pending += 1
                continue
            window = bars[:horizon]
            end = _finite(window[-1].get("close"))
            highs = [value for value in (_finite(row.get("high")) for row in window) if value is not None]
            lows = [value for value in (_finite(row.get("low")) for row in window) if value is not None]
            if end is None or end <= 0:
                pending += 1
                continue
            block = forecasts.get(f"{horizon}d") if isinstance(forecasts, dict) else None
            direction = str(block.get("direction") if isinstance(block, dict) else signal["direction"])
            forecast_score = _finite(block.get("score")) if isinstance(block, dict) else _finite(signal["forecast_score"])
            spy_return = _benchmark_return(stock_db_path, code="SPY", analysis_date=analysis_date, horizon=horizon)
            qqq_return = _benchmark_return(stock_db_path, code="QQQ", analysis_date=analysis_date, horizon=horizon)
            if store.save_outcome(
                signal_id=int(signal["id"]), horizon_days=horizon,
                end_trade_date=str(window[-1].get("date") or ""), start_price=start,
                end_price=end, max_high=max(highs) if highs else None,
                min_low=min(lows) if lows else None, direction=direction,
                neutral_band_pct=neutral_band_pct, forecast_score=forecast_score,
                benchmark_spy_return_pct=spy_return, benchmark_qqq_return_pct=qqq_return,
            ):
                evaluated += 1

    return {"evaluated": evaluated, "not_yet_mature": pending}
