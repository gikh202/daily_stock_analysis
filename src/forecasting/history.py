from __future__ import annotations

import json
import math
import sqlite3
import statistics
from dataclasses import dataclass
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

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


class ForecastHistory:
    """Read-only, strict as-of learning view over matured forecast outcomes."""

    def __init__(self, db_path: str, *, minimum_samples: int = 30, minimum_regime_samples: int = 15, prior_strength: float = 12.0) -> None:
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

    def _rows(self, *, as_of_date: str, horizon_days: int) -> list[sqlite3.Row]:
        if not self.available:
            return []
        try:
            with self._connect() as conn:
                return list(conn.execute(
                    """
                    SELECT f.engine_version, f.market_regime, f.effective_trade_date,
                           h.score, h.payload_json, o.end_trade_date, o.return_pct,
                           o.mfe_pct, o.mae_pct, o.excess_vs_spy_pct
                    FROM v6_forecast_outcomes o
                    JOIN v6_forecast_runs f ON f.id=o.forecast_run_id
                    LEFT JOIN v6_horizon_forecasts h
                      ON h.forecast_run_id=f.id AND h.horizon_days=o.horizon_days
                    WHERE o.horizon_days=? AND o.end_trade_date < ?
                      AND COALESCE(f.effective_trade_date, '') < ?
                    ORDER BY o.end_trade_date ASC, o.id ASC
                    """,
                    (int(horizon_days), str(as_of_date), str(as_of_date)),
                ).fetchall())
        except sqlite3.Error:
            return []

    @staticmethod
    def _row_probability(row: sqlite3.Row, key: str = "probability_up") -> Optional[float]:
        payload: Dict[str, Any] = {}
        try:
            parsed = json.loads(str(row["payload_json"] or "{}"))
            payload = parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        value = _finite(payload.get(key))
        if value is not None:
            return _clamp(value, 0.01, 0.99)
        score = _finite(row["score"])
        return None if score is None else _clamp(score / 100.0, 0.01, 0.99)

    @staticmethod
    def _bucket(probability: float) -> tuple[float, float]:
        p = _clamp(probability, 0.0, 1.0)
        for low, high in ((0.0, 0.40), (0.40, 0.50), (0.50, 0.60), (0.60, 0.70), (0.70, 1.01)):
            if low <= p < high:
                return low, high
        return 0.0, 1.01

    def calibration(self, *, as_of_date: str, horizon_days: int, raw_probability_up: float, regime: str) -> CalibrationProfile:
        raw = _clamp(float(raw_probability_up), 0.02, 0.98)
        rows = self._rows(as_of_date=as_of_date, horizon_days=horizon_days)
        regime_key = str(regime or "unknown").strip().lower()
        regime_rows = [row for row in rows if str(row["market_regime"] or "unknown").strip().lower() == regime_key]
        use_regime = len(regime_rows) >= self.minimum_regime_samples
        selected = regime_rows if use_regime else rows
        source = "regime" if use_regime else "horizon"
        low, high = self._bucket(raw)
        bucket = [row for row in selected if (p := self._row_probability(row)) is not None and low <= p < high]
        if len(bucket) < max(5, self.minimum_samples // 3):
            bucket = selected
            source += "_all"

        outcomes: list[tuple[float, int, float]] = []
        returns: list[float] = []
        alphas: list[float] = []
        mfes: list[float] = []
        maes: list[float] = []
        for row in bucket:
            ret = _finite(row["return_pct"])
            p = self._row_probability(row)
            if ret is None or p is None:
                continue
            outcomes.append((p, int(ret > 0.0), ret))
            returns.append(ret)
            for field, target in (("excess_vs_spy_pct", alphas), ("mfe_pct", mfes), ("mae_pct", maes)):
                value = _finite(row[field])
                if value is not None:
                    target.append(value)

        n = len(outcomes)
        if n == 0:
            return CalibrationProfile("prior_only", 0, len(regime_rows), raw, None, None, None, None, None, None, None, None, None, None, "prior")

        hits = sum(y for _, y, _ in outcomes)
        posterior = (hits + self.prior_strength * raw) / (n + self.prior_strength)
        brier = statistics.fmean((p - y) ** 2 for p, y, _ in outcomes)
        logloss = statistics.fmean(_log_loss(p, y) for p, y, _ in outcomes)
        bins: Dict[int, list[tuple[float, int]]] = {}
        for p, y, _ in outcomes:
            bins.setdefault(min(9, int(p * 10.0)), []).append((p, y))
        ece = sum(len(values) / n * abs(statistics.fmean(p for p, _ in values) - statistics.fmean(y for _, y in values)) for values in bins.values())
        return CalibrationProfile(
            "mature" if n >= self.minimum_samples else "shrunk", n, len(regime_rows),
            _clamp(posterior, 0.02, 0.98),
            statistics.fmean(returns) if returns else None,
            statistics.fmean(alphas) if alphas else None,
            statistics.fmean(mfes) if mfes else None,
            statistics.fmean(maes) if maes else None,
            _quantile(returns, 0.10), _quantile(returns, 0.50), _quantile(returns, 0.90),
            brier, logloss, ece, source,
        )

    def model_metrics(self, *, as_of_date: str, horizon_days: int, probability_key: str, regime: str | None = None) -> Dict[str, Any]:
        rows = self._rows(as_of_date=as_of_date, horizon_days=horizon_days)
        if regime:
            key = str(regime).strip().lower()
            rows = [row for row in rows if str(row["market_regime"] or "unknown").strip().lower() == key]
        samples: list[tuple[float, int]] = []
        for row in rows:
            p = self._row_probability(row, probability_key)
            ret = _finite(row["return_pct"])
            if p is not None and ret is not None:
                samples.append((p, int(ret > 0.0)))
        if not samples:
            return {"samples": 0, "brier_score": None, "log_loss": None}
        return {"samples": len(samples), "brier_score": statistics.fmean((p-y)**2 for p,y in samples), "log_loss": statistics.fmean(_log_loss(p,y) for p,y in samples)}

    def select_champion(self, *, as_of_date: str, horizon_days: int, regime: str, min_promotion_samples: int = 200, min_brier_improvement: float = 0.01) -> Dict[str, Any]:
        champion = self.model_metrics(as_of_date=as_of_date, horizon_days=horizon_days, probability_key="probability_up", regime=regime)
        challenger = self.model_metrics(as_of_date=as_of_date, horizon_days=horizon_days, probability_key="challenger_probability_up", regime=regime)
        promote = bool(challenger["samples"] >= int(min_promotion_samples) and champion["brier_score"] is not None and challenger["brier_score"] is not None and challenger["brier_score"] <= champion["brier_score"] - float(min_brier_improvement))
        return {
            "champion_model": "momentum_challenger" if promote else "calibrated_ensemble",
            "challenger_model": "calibrated_ensemble" if promote else "momentum_challenger",
            "status": "promoted" if promote else ("observing" if challenger["samples"] else "cold_start"),
            "promotion_min_samples": int(min_promotion_samples),
            "min_brier_improvement": float(min_brier_improvement),
            "champion_metrics": champion,
            "challenger_metrics": challenger,
        }
