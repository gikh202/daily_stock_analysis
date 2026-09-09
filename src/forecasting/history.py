from __future__ import annotations

import json
import math
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Sequence


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _valid_date(value: Any) -> Optional[str]:
    text = str(value or "").strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _quantile(values: Sequence[float], q: float) -> Optional[float]:
    clean = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    pos = _clamp(q, 0.0, 1.0) * (len(clean) - 1)
    low = int(math.floor(pos))
    high = int(math.ceil(pos))
    if low == high:
        return clean[low]
    weight = pos - low
    return clean[low] * (1.0 - weight) + clean[high] * weight


def _log_loss(p: float, y: int) -> float:
    clipped = _clamp(p, 1e-6, 1.0 - 1e-6)
    return -(y * math.log(clipped) + (1 - y) * math.log(1.0 - clipped))


@dataclass(frozen=True)
class CalibrationProfile:
    status: str
    samples: int
    regime_samples: int
    probability_up: float
    historical_return_pct: Optional[float]
    historical_alpha_pct: Optional[float]
    historical_mfe_pct: Optional[float]
    historical_mae_pct: Optional[float]
    return_p10_pct: Optional[float]
    return_p50_pct: Optional[float]
    return_p90_pct: Optional[float]
    brier_score: Optional[float]
    log_loss: Optional[float]
    ece: Optional[float]
    source: str
    historical_direction_hit_rate: Optional[float] = None
    calibration_scope: str = "global"
    historical_positive_rate: Optional[float] = None
    historical_majority_baseline_accuracy: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


class ForecastHistory:
    """Read-only, strict as-of learning view over matured forecast outcomes.

    V7.3 uses hierarchical reliability scopes. The most specific sufficiently
    populated scope wins: symbol -> instrument type -> market regime -> global.
    Every scope is strict-as-of, so fallback never admits future outcomes.
    """

    def __init__(
        self,
        db_path: str,
        *,
        minimum_samples: int = 50,
        minimum_regime_samples: int = 15,
        prior_strength: float = 12.0,
    ) -> None:
        self.path = Path(db_path)
        self.minimum_samples = max(5, int(minimum_samples))
        self.minimum_regime_samples = max(5, int(minimum_regime_samples))
        self.prior_strength = max(1.0, float(prior_strength))

    @property
    def available(self) -> bool:
        return self.path.is_file()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
        try:
            return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.Error:
            return set()

    def _rows(
        self,
        *,
        as_of_date: str | None,
        horizon_days: int,
        symbol: str | None = None,
        instrument_type: str | None = None,
    ) -> list[sqlite3.Row]:
        as_of = _valid_date(as_of_date)
        if not self.available or as_of is None:
            return []
        try:
            with self._connect() as conn:
                columns = self._table_columns(conn, "v6_forecast_runs")
                symbol_expr = "f.symbol" if "symbol" in columns else "NULL"
                instrument_expr = (
                    "f.instrument_type" if "instrument_type" in columns else "NULL"
                )
                rows = list(
                    conn.execute(
                        f"""
                        SELECT f.engine_version, f.market_regime,
                               f.effective_trade_date,
                               {symbol_expr} AS symbol,
                               {instrument_expr} AS instrument_type,
                               h.score, h.payload_json, o.end_trade_date,
                               o.return_pct, o.mfe_pct, o.mae_pct,
                               o.excess_vs_spy_pct
                        FROM v6_forecast_outcomes o
                        JOIN v6_forecast_runs f ON f.id=o.forecast_run_id
                        LEFT JOIN v6_horizon_forecasts h
                          ON h.forecast_run_id=f.id AND h.horizon_days=o.horizon_days
                        WHERE o.horizon_days=?
                          AND date(o.end_trade_date) IS NOT NULL
                          AND date(o.end_trade_date) < date(?)
                          AND date(f.effective_trade_date) IS NOT NULL
                          AND date(f.effective_trade_date) < date(?)
                        ORDER BY o.end_trade_date ASC, o.id ASC
                        """,
                        (int(horizon_days), as_of, as_of),
                    ).fetchall()
                )
        except sqlite3.Error:
            return []

        symbol_key = str(symbol or "").strip().upper()
        instrument_key = str(instrument_type or "").strip().upper()
        if symbol_key and any(row["symbol"] is not None for row in rows):
            rows = [
                row
                for row in rows
                if str(row["symbol"] or "").strip().upper() == symbol_key
            ]
        if instrument_key and any(row["instrument_type"] is not None for row in rows):
            rows = [
                row
                for row in rows
                if str(row["instrument_type"] or "").strip().upper() == instrument_key
            ]
        return rows

    @staticmethod
    def _row_probability(
        row: sqlite3.Row, key: str = "probability_up"
    ) -> Optional[float]:
        payload: Dict[str, Any] = {}
        try:
            parsed = json.loads(str(row["payload_json"] or "{}"))
            payload = parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}

        target_model = {
            "probability_up": "calibrated_ensemble",
            "challenger_probability_up": "momentum_challenger",
        }.get(key)
        if target_model is not None:
            champion_model = str(payload.get("champion_model") or "").strip()
            challenger_model = str(payload.get("challenger_model") or "").strip()
            if champion_model or challenger_model:
                if champion_model == target_model:
                    value = _finite(payload.get("probability_up"))
                    return None if value is None else _clamp(value, 0.01, 0.99)
                if challenger_model == target_model:
                    value = _finite(payload.get("challenger_probability_up"))
                    return None if value is None else _clamp(value, 0.01, 0.99)
                return None
            value = _finite(payload.get(key))
            if value is not None:
                return _clamp(value, 0.01, 0.99)
        else:
            value = _finite(payload.get(key))
            if value is not None:
                return _clamp(value, 0.01, 0.99)

        if target_model != "calibrated_ensemble":
            return None
        score = _finite(row["score"])
        return None if score is None else _clamp(score / 100.0, 0.01, 0.99)

    @staticmethod
    def _bucket(probability: float) -> tuple[float, float]:
        p = _clamp(probability, 0.0, 1.0)
        for low, high in (
            (0.0, 0.40),
            (0.40, 0.50),
            (0.50, 0.60),
            (0.60, 0.70),
            (0.70, 1.01),
        ):
            if low <= p < high:
                return low, high
        return 0.0, 1.01

    @staticmethod
    def _matches_symbol(row: sqlite3.Row, symbol: str) -> bool:
        return str(row["symbol"] or "").strip().upper() == symbol

    @staticmethod
    def _matches_instrument(row: sqlite3.Row, instrument_type: str) -> bool:
        return str(row["instrument_type"] or "").strip().upper() == instrument_type

    def _hierarchical_scope(
        self,
        rows: Sequence[sqlite3.Row],
        *,
        symbol: str | None,
        instrument_type: str | None,
        regime: str | None,
    ) -> tuple[list[sqlite3.Row], str, int]:
        all_rows = list(rows)
        if not all_rows:
            return [], "global", 0

        symbol_key = str(symbol or "").strip().upper()
        instrument_key = str(instrument_type or "").strip().upper()
        regime_key = str(regime or "unknown").strip().lower()
        regime_rows = [
            row
            for row in all_rows
            if str(row["market_regime"] or "unknown").strip().lower() == regime_key
        ]

        symbol_floor = max(8, self.minimum_samples // 2)
        if symbol_key and any(row["symbol"] is not None for row in all_rows):
            symbol_rows = [row for row in all_rows if self._matches_symbol(row, symbol_key)]
            if len(symbol_rows) >= symbol_floor:
                symbol_regime = [
                    row
                    for row in symbol_rows
                    if str(row["market_regime"] or "unknown").strip().lower()
                    == regime_key
                ]
                if len(symbol_regime) >= max(5, self.minimum_regime_samples // 2):
                    return symbol_regime, "symbol_regime", len(regime_rows)
                return symbol_rows, "symbol", len(regime_rows)

        if instrument_key and any(
            row["instrument_type"] is not None for row in all_rows
        ):
            instrument_rows = [
                row
                for row in all_rows
                if self._matches_instrument(row, instrument_key)
            ]
            if len(instrument_rows) >= self.minimum_samples:
                instrument_regime = [
                    row
                    for row in instrument_rows
                    if str(row["market_regime"] or "unknown").strip().lower()
                    == regime_key
                ]
                if len(instrument_regime) >= self.minimum_regime_samples:
                    return instrument_regime, "instrument_regime", len(regime_rows)
                return instrument_rows, "instrument_type", len(regime_rows)

        if len(regime_rows) >= self.minimum_regime_samples:
            return regime_rows, "regime", len(regime_rows)
        return all_rows, "global", len(regime_rows)

    def calibration(
        self,
        *,
        as_of_date: str | None,
        horizon_days: int,
        raw_probability_up: float,
        regime: str,
        probability_key: str = "probability_up",
        symbol: str | None = None,
        instrument_type: str | None = None,
    ) -> CalibrationProfile:
        raw = _clamp(float(raw_probability_up), 0.02, 0.98)
        as_of = _valid_date(as_of_date)
        if as_of is None:
            return CalibrationProfile(
                "prior_only", 0, 0, raw, None, None, None, None,
                None, None, None, None, None, None, "missing_as_of",
                None, "missing_as_of",
            )

        rows = self._rows(as_of_date=as_of, horizon_days=horizon_days)
        selected, scope, regime_samples = self._hierarchical_scope(
            rows,
            symbol=symbol,
            instrument_type=instrument_type,
            regime=regime,
        )
        low, high = self._bucket(raw)
        bucket = [
            row
            for row in selected
            if (p := self._row_probability(row, probability_key)) is not None
            and low <= p < high
        ]
        source = scope
        if len(bucket) < max(5, self.minimum_samples // 3):
            bucket = [
                row
                for row in selected
                if self._row_probability(row, probability_key) is not None
            ]
            source += "_all"

        outcomes: list[tuple[float, int, float]] = []
        returns: list[float] = []
        alphas: list[float] = []
        mfes: list[float] = []
        maes: list[float] = []
        for row in bucket:
            ret = _finite(row["return_pct"])
            p = self._row_probability(row, probability_key)
            if ret is None or p is None:
                continue
            outcomes.append((p, int(ret > 0.0), ret))
            returns.append(ret)
            for field, target in (
                ("excess_vs_spy_pct", alphas),
                ("mfe_pct", mfes),
                ("mae_pct", maes),
            ):
                value = _finite(row[field])
                if value is not None:
                    target.append(value)

        n = len(outcomes)
        if n == 0:
            return CalibrationProfile(
                "prior_only", 0, regime_samples, raw, None, None, None, None,
                None, None, None, None, None, None,
                f"{source}:{probability_key}", None, scope,
            )

        positives = sum(y for _, y, _ in outcomes)
        positive_rate = positives / n
        direction_hits = sum(
            int((p >= 0.50) == bool(y))
            for p, y, _ in outcomes
        )
        hit_rate = direction_hits / n
        majority_baseline = max(positive_rate, 1.0 - positive_rate)
        posterior = (
            positives + self.prior_strength * raw
        ) / (n + self.prior_strength)
        brier = statistics.fmean((p - y) ** 2 for p, y, _ in outcomes)
        logloss = statistics.fmean(_log_loss(p, y) for p, y, _ in outcomes)
        bins: Dict[int, list[tuple[float, int]]] = {}
        for p, y, _ in outcomes:
            bins.setdefault(min(9, int(p * 10.0)), []).append((p, y))
        ece = sum(
            len(values) / n
            * abs(
                statistics.fmean(p for p, _ in values)
                - statistics.fmean(y for _, y in values)
            )
            for values in bins.values()
        )
        return CalibrationProfile(
            "mature" if n >= self.minimum_samples else "shrunk",
            n,
            regime_samples,
            _clamp(posterior, 0.02, 0.98),
            statistics.fmean(returns) if returns else None,
            statistics.fmean(alphas) if alphas else None,
            statistics.fmean(mfes) if mfes else None,
            statistics.fmean(maes) if maes else None,
            _quantile(returns, 0.10),
            _quantile(returns, 0.50),
            _quantile(returns, 0.90),
            brier,
            logloss,
            ece,
            f"{source}:{probability_key}",
            hit_rate,
            scope,
            positive_rate,
            majority_baseline,
        )

    def _metric_rows(
        self,
        *,
        as_of_date: str | None,
        horizon_days: int,
        regime: str | None,
        symbol: str | None,
        instrument_type: str | None,
    ) -> list[sqlite3.Row]:
        rows = self._rows(as_of_date=as_of_date, horizon_days=horizon_days)
        selected, _, _ = self._hierarchical_scope(
            rows,
            symbol=symbol,
            instrument_type=instrument_type,
            regime=regime,
        )
        return selected

    def model_metrics(
        self,
        *,
        as_of_date: str | None,
        horizon_days: int,
        probability_key: str,
        regime: str | None = None,
        symbol: str | None = None,
        instrument_type: str | None = None,
    ) -> Dict[str, Any]:
        if _valid_date(as_of_date) is None:
            return {"samples": 0, "brier_score": None, "log_loss": None}
        rows = self._metric_rows(
            as_of_date=as_of_date,
            horizon_days=horizon_days,
            regime=regime,
            symbol=symbol,
            instrument_type=instrument_type,
        )
        samples: list[tuple[float, int]] = []
        for row in rows:
            p = self._row_probability(row, probability_key)
            ret = _finite(row["return_pct"])
            if p is not None and ret is not None:
                samples.append((p, int(ret > 0.0)))
        if not samples:
            return {"samples": 0, "brier_score": None, "log_loss": None}
        return {
            "samples": len(samples),
            "brier_score": statistics.fmean((p - y) ** 2 for p, y in samples),
            "log_loss": statistics.fmean(_log_loss(p, y) for p, y in samples),
        }

    def paired_model_metrics(
        self,
        *,
        as_of_date: str | None,
        horizon_days: int,
        regime: str | None = None,
        symbol: str | None = None,
        instrument_type: str | None = None,
    ) -> Dict[str, Any]:
        """Compare native Champion/Challenger predictions against real baselines.

        Direction accuracy alone is insufficient in a skewed market. The report
        therefore includes the majority-class baseline and a probabilistic
        base-rate baseline, plus inverse-signal diagnostics for research only.
        """
        empty = {
            "samples": 0,
            "scope": "global",
            "positive_rate": None,
            "majority_baseline_accuracy": None,
            "base_rate_brier_score": None,
            "base_rate_log_loss": None,
            "champion_direction_accuracy": None,
            "challenger_direction_accuracy": None,
            "champion_direction_skill": None,
            "challenger_direction_skill": None,
            "champion_inverse_direction_accuracy": None,
            "challenger_inverse_direction_accuracy": None,
            "champion_inverse_direction_skill": None,
            "challenger_inverse_direction_skill": None,
            "champion_signal_samples": 0,
            "challenger_signal_samples": 0,
            "champion_signal_accuracy": None,
            "challenger_signal_accuracy": None,
            "champion_signal_coverage": None,
            "challenger_signal_coverage": None,
            "champion_bullish_samples": 0,
            "challenger_bullish_samples": 0,
            "champion_bullish_positive_alpha_rate": None,
            "challenger_bullish_positive_alpha_rate": None,
            "champion_bullish_mean_alpha_pct": None,
            "challenger_bullish_mean_alpha_pct": None,
            "champion_brier_score": None,
            "challenger_brier_score": None,
            "champion_log_loss": None,
            "challenger_log_loss": None,
        }
        if _valid_date(as_of_date) is None:
            return dict(empty)
        all_rows = self._rows(
            as_of_date=as_of_date,
            horizon_days=horizon_days,
        )
        rows, scope, _ = self._hierarchical_scope(
            all_rows,
            symbol=symbol,
            instrument_type=instrument_type,
            regime=regime,
        )
        paired: list[tuple[float, float, int, float | None]] = []
        for row in rows:
            champion_p = self._row_probability(row, "probability_up")
            challenger_p = self._row_probability(row, "challenger_probability_up")
            ret = _finite(row["return_pct"])
            if champion_p is None or challenger_p is None or ret is None:
                continue
            paired.append(
                (
                    champion_p,
                    challenger_p,
                    int(ret > 0.0),
                    _finite(row["excess_vs_spy_pct"]),
                )
            )
        if not paired:
            return {**empty, "scope": scope}

        n = len(paired)
        positive_rate = sum(y for _, _, y, _ in paired) / n
        majority_baseline = max(positive_rate, 1.0 - positive_rate)
        champion_hits = sum(int((p >= 0.50) == bool(y)) for p, _, y, _ in paired)
        challenger_hits = sum(int((p >= 0.50) == bool(y)) for _, p, y, _ in paired)
        champion_inverse_hits = sum(
            int(((1.0 - p) >= 0.50) == bool(y)) for p, _, y, _ in paired
        )
        challenger_inverse_hits = sum(
            int(((1.0 - p) >= 0.50) == bool(y)) for _, p, y, _ in paired
        )
        champion_accuracy = champion_hits / n
        challenger_accuracy = challenger_hits / n
        champion_inverse_accuracy = champion_inverse_hits / n
        challenger_inverse_accuracy = challenger_inverse_hits / n
        champion_signal_rows = [
            (p, y)
            for p, _, y, _ in paired
            if p >= 0.58 or p <= 0.42
        ]
        challenger_signal_rows = [
            (p, y)
            for _, p, y, _ in paired
            if p >= 0.58 or p <= 0.42
        ]
        champion_signal_accuracy = (
            None
            if not champion_signal_rows
            else statistics.fmean(
                int((p >= 0.50) == bool(y))
                for p, y in champion_signal_rows
            )
        )
        challenger_signal_accuracy = (
            None
            if not challenger_signal_rows
            else statistics.fmean(
                int((p >= 0.50) == bool(y))
                for p, y in challenger_signal_rows
            )
        )
        champion_bullish_alphas = [
            alpha
            for champion_p, _, _, alpha in paired
            if champion_p >= 0.58 and alpha is not None
        ]
        challenger_bullish_alphas = [
            alpha
            for _, challenger_p, _, alpha in paired
            if challenger_p >= 0.58 and alpha is not None
        ]

        return {
            "samples": n,
            "scope": scope,
            "positive_rate": positive_rate,
            "majority_baseline_accuracy": majority_baseline,
            "base_rate_brier_score": statistics.fmean(
                (positive_rate - y) ** 2 for _, _, y, _ in paired
            ),
            "base_rate_log_loss": statistics.fmean(
                _log_loss(positive_rate, y) for _, _, y, _ in paired
            ),
            "champion_direction_accuracy": champion_accuracy,
            "challenger_direction_accuracy": challenger_accuracy,
            "champion_direction_skill": champion_accuracy - majority_baseline,
            "challenger_direction_skill": challenger_accuracy - majority_baseline,
            "champion_inverse_direction_accuracy": champion_inverse_accuracy,
            "challenger_inverse_direction_accuracy": challenger_inverse_accuracy,
            "champion_inverse_direction_skill": (
                champion_inverse_accuracy - majority_baseline
            ),
            "challenger_inverse_direction_skill": (
                challenger_inverse_accuracy - majority_baseline
            ),
            "champion_signal_samples": len(champion_signal_rows),
            "challenger_signal_samples": len(challenger_signal_rows),
            "champion_signal_accuracy": champion_signal_accuracy,
            "challenger_signal_accuracy": challenger_signal_accuracy,
            "champion_signal_coverage": len(champion_signal_rows) / n,
            "challenger_signal_coverage": len(challenger_signal_rows) / n,
            "champion_bullish_samples": len(champion_bullish_alphas),
            "challenger_bullish_samples": len(challenger_bullish_alphas),
            "champion_bullish_positive_alpha_rate": (
                None
                if not champion_bullish_alphas
                else sum(value > 0.0 for value in champion_bullish_alphas)
                / len(champion_bullish_alphas)
            ),
            "challenger_bullish_positive_alpha_rate": (
                None
                if not challenger_bullish_alphas
                else sum(value > 0.0 for value in challenger_bullish_alphas)
                / len(challenger_bullish_alphas)
            ),
            "champion_bullish_mean_alpha_pct": (
                None
                if not champion_bullish_alphas
                else statistics.fmean(champion_bullish_alphas)
            ),
            "challenger_bullish_mean_alpha_pct": (
                None
                if not challenger_bullish_alphas
                else statistics.fmean(challenger_bullish_alphas)
            ),
            "champion_brier_score": statistics.fmean(
                (p - y) ** 2 for p, _, y, _ in paired
            ),
            "challenger_brier_score": statistics.fmean(
                (p - y) ** 2 for _, p, y, _ in paired
            ),
            "champion_log_loss": statistics.fmean(
                _log_loss(p, y) for p, _, y, _ in paired
            ),
            "challenger_log_loss": statistics.fmean(
                _log_loss(p, y) for _, p, y, _ in paired
            ),
        }

    def select_champion(
        self,
        *,
        as_of_date: str | None,
        horizon_days: int,
        regime: str,
        symbol: str | None = None,
        instrument_type: str | None = None,
        min_promotion_samples: int = 200,
        min_brier_improvement: float = 0.01,
        min_log_loss_improvement: float = 0.005,
        min_direction_accuracy: float = 0.52,
        min_direction_skill: float = 0.02,
        min_direction_improvement: float = 0.01,
        min_signal_samples: int | None = None,
        min_signal_accuracy: float = 0.55,
        min_signal_improvement: float = 0.005,
        min_bullish_alpha_samples: int = 30,
        min_bullish_positive_alpha_rate: float = 0.52,
        min_bullish_mean_alpha_pct: float = 0.10,
        min_bullish_alpha_improvement_pct: float = 0.05,
    ) -> Dict[str, Any]:
        paired = self.paired_model_metrics(
            as_of_date=as_of_date,
            horizon_days=horizon_days,
            regime=regime,
            symbol=symbol,
            instrument_type=instrument_type,
        )
        champion = {
            "samples": paired["samples"],
            "direction_accuracy": paired["champion_direction_accuracy"],
            "direction_skill": paired["champion_direction_skill"],
            "signal_samples": paired["champion_signal_samples"],
            "signal_accuracy": paired["champion_signal_accuracy"],
            "signal_coverage": paired["champion_signal_coverage"],
            "brier_score": paired["champion_brier_score"],
            "log_loss": paired["champion_log_loss"],
            "bullish_samples": paired["champion_bullish_samples"],
            "bullish_positive_alpha_rate": paired[
                "champion_bullish_positive_alpha_rate"
            ],
            "bullish_mean_alpha_pct": paired["champion_bullish_mean_alpha_pct"],
        }
        challenger = {
            "samples": paired["samples"],
            "direction_accuracy": paired["challenger_direction_accuracy"],
            "direction_skill": paired["challenger_direction_skill"],
            "signal_samples": paired["challenger_signal_samples"],
            "signal_accuracy": paired["challenger_signal_accuracy"],
            "signal_coverage": paired["challenger_signal_coverage"],
            "brier_score": paired["challenger_brier_score"],
            "log_loss": paired["challenger_log_loss"],
            "bullish_samples": paired["challenger_bullish_samples"],
            "bullish_positive_alpha_rate": paired[
                "challenger_bullish_positive_alpha_rate"
            ],
            "bullish_mean_alpha_pct": paired["challenger_bullish_mean_alpha_pct"],
        }
        baseline = {
            "positive_rate": paired["positive_rate"],
            "majority_accuracy": paired["majority_baseline_accuracy"],
            "brier_score": paired["base_rate_brier_score"],
            "log_loss": paired["base_rate_log_loss"],
        }

        signal_floor = (
            max(5, min(50, int(min_promotion_samples) // 4))
            if min_signal_samples is None
            else max(1, int(min_signal_samples))
        )
        symbol_specific_scope = bool(
            not str(symbol or "").strip()
            or paired.get("scope") in {"symbol", "symbol_regime"}
        )
        gates = {
            "symbol_specific_scope": symbol_specific_scope,
            "enough_samples": paired["samples"] >= int(min_promotion_samples),
            "direction_accuracy_floor": bool(
                challenger["direction_accuracy"] is not None
                and challenger["direction_accuracy"] >= float(min_direction_accuracy)
            ),
            "beats_majority_baseline": bool(
                challenger["direction_skill"] is not None
                and challenger["direction_skill"] >= float(min_direction_skill)
            ),
            "beats_champion_direction": bool(
                challenger["direction_accuracy"] is not None
                and champion["direction_accuracy"] is not None
                and challenger["direction_accuracy"]
                >= champion["direction_accuracy"] + float(min_direction_improvement)
            ),
            "beats_inverse_signal": bool(
                challenger["direction_accuracy"] is not None
                and paired.get("challenger_inverse_direction_accuracy") is not None
                and challenger["direction_accuracy"]
                > paired["challenger_inverse_direction_accuracy"]
            ),
            "signal_sample_floor": (
                int(challenger["signal_samples"] or 0) >= signal_floor
            ),
            "signal_accuracy_floor": bool(
                challenger["signal_accuracy"] is not None
                and challenger["signal_accuracy"] >= float(min_signal_accuracy)
            ),
            "beats_champion_signal": bool(
                challenger["signal_accuracy"] is not None
                and (
                    champion["signal_accuracy"] is None
                    or challenger["signal_accuracy"]
                    >= champion["signal_accuracy"] + float(min_signal_improvement)
                )
            ),
            "beats_champion_brier": bool(
                challenger["brier_score"] is not None
                and champion["brier_score"] is not None
                and challenger["brier_score"]
                <= champion["brier_score"] - float(min_brier_improvement)
            ),
            "beats_base_rate_brier": bool(
                challenger["brier_score"] is not None
                and baseline["brier_score"] is not None
                and challenger["brier_score"]
                <= baseline["brier_score"] - float(min_brier_improvement)
            ),
            "beats_champion_log_loss": bool(
                challenger["log_loss"] is not None
                and champion["log_loss"] is not None
                and challenger["log_loss"]
                <= champion["log_loss"] - float(min_log_loss_improvement)
            ),
            "beats_base_rate_log_loss": bool(
                challenger["log_loss"] is not None
                and baseline["log_loss"] is not None
                and challenger["log_loss"]
                <= baseline["log_loss"] - float(min_log_loss_improvement)
            ),
            "bullish_alpha_sample_floor": (
                challenger["bullish_samples"] >= int(min_bullish_alpha_samples)
            ),
            "bullish_positive_alpha_rate": bool(
                challenger["bullish_positive_alpha_rate"] is not None
                and challenger["bullish_positive_alpha_rate"]
                >= float(min_bullish_positive_alpha_rate)
            ),
            "bullish_mean_alpha_positive": bool(
                challenger["bullish_mean_alpha_pct"] is not None
                and challenger["bullish_mean_alpha_pct"]
                >= float(min_bullish_mean_alpha_pct)
            ),
            "beats_champion_bullish_alpha": bool(
                challenger["bullish_mean_alpha_pct"] is not None
                and (
                    champion["bullish_mean_alpha_pct"] is None
                    or challenger["bullish_mean_alpha_pct"]
                    >= champion["bullish_mean_alpha_pct"]
                    + float(min_bullish_alpha_improvement_pct)
                )
            ),
        }
        promote = bool(gates and all(gates.values()))
        blocked_reasons = [name for name, passed in gates.items() if not passed]

        inverse_skill = paired.get("challenger_inverse_direction_skill")
        normal_skill = paired.get("challenger_direction_skill")
        reverse_signal_warning = bool(
            inverse_skill is not None
            and inverse_skill >= float(min_direction_skill)
            and (
                normal_skill is None
                or inverse_skill >= normal_skill + 0.05
            )
        )

        return {
            "champion_model": (
                "momentum_challenger" if promote else "calibrated_ensemble"
            ),
            "challenger_model": (
                "calibrated_ensemble" if promote else "momentum_challenger"
            ),
            "status": (
                "promoted"
                if promote
                else ("observing" if paired["samples"] else "cold_start")
            ),
            "promotion_min_samples": int(min_promotion_samples),
            "min_brier_improvement": float(min_brier_improvement),
            "min_log_loss_improvement": float(min_log_loss_improvement),
            "min_direction_accuracy": float(min_direction_accuracy),
            "min_direction_skill": float(min_direction_skill),
            "min_direction_improvement": float(min_direction_improvement),
            "min_signal_samples": signal_floor,
            "min_signal_accuracy": float(min_signal_accuracy),
            "min_signal_improvement": float(min_signal_improvement),
            "min_bullish_alpha_samples": int(min_bullish_alpha_samples),
            "min_bullish_positive_alpha_rate": float(
                min_bullish_positive_alpha_rate
            ),
            "min_bullish_mean_alpha_pct": float(min_bullish_mean_alpha_pct),
            "min_bullish_alpha_improvement_pct": float(
                min_bullish_alpha_improvement_pct
            ),
            "evaluation_basis": (
                "paired_forward_only_vs_champion_majority_and_base_rate"
            ),
            "promotion_gate_version": "v8-symbol-horizon-strong-signal.1",
            "scope_policy": "symbol_to_instrument_type_to_regime_to_global",
            "promotion_scope_policy": (
                "symbol_or_symbol_regime_only_for_symbol_specific_promotion"
            ),
            "evaluation_scope": paired.get("scope"),
            "paired_samples": paired["samples"],
            "baseline_metrics": baseline,
            "champion_metrics": champion,
            "challenger_metrics": challenger,
            "promotion_gates": gates,
            "blocked_reasons": blocked_reasons,
            "promotion_failures": blocked_reasons,
            "reverse_signal_shadow": {
                "production_enabled": False,
                "challenger_inverse_direction_accuracy": paired.get(
                    "challenger_inverse_direction_accuracy"
                ),
                "challenger_inverse_direction_skill": inverse_skill,
                "warning": reverse_signal_warning,
            },
        }
