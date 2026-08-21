from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Sequence

from src.alpha_engine.models import AlphaFeatures

from .history import ForecastHistory
from .models import ForecastBundle, ForecastHorizon


V7_FORECAST_VERSION = "v7.1-forecast.1"
FORECAST_HORIZONS = (1, 5, 10, 20)


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _logistic(value: float) -> float:
    if value >= 0:
        exp = math.exp(-min(value, 60.0))
        return 1.0 / (1.0 + exp)
    exp = math.exp(max(value, -60.0))
    return exp / (1.0 + exp)


def _find_mapping(root: Mapping[str, Any], names: Sequence[str]) -> Dict[str, Any]:
    wanted = set(names)
    queue: list[Mapping[str, Any]] = [root]
    seen: set[int] = set()
    while queue:
        current = queue.pop(0)
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        for key, value in current.items():
            if key in wanted and isinstance(value, dict):
                return dict(value)
            if isinstance(value, dict):
                queue.append(value)
    return {}


def _first_number(mapping: Mapping[str, Any], names: Sequence[str]) -> Optional[float]:
    for name in names:
        value = _finite(mapping.get(name))
        if value is not None:
            return value
    return None


def _horizon_block(context: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    prediction = _find_mapping(context, ("prediction_context",))
    horizons = prediction.get("horizons")
    if not isinstance(horizons, dict):
        return {}
    value = horizons.get(name)
    return value if isinstance(value, dict) else {}


def _raw_return_targets(context: Mapping[str, Any]) -> Dict[int, Optional[float]]:
    h5 = _horizon_block(context, "5d")
    h20 = _horizon_block(context, "20d")
    h60 = _horizon_block(context, "60d")
    r5 = _first_number(h5, ("target_return_pct", "expected_return_pct"))
    r20 = _first_number(h20, ("target_return_pct", "expected_return_pct"))
    r60 = _first_number(h60, ("target_return_pct", "expected_return_pct"))
    return {
        1: None if r5 is None else r5 / 3.0,
        5: r5 if r5 is not None else (None if r20 is None else r20 * 0.30),
        10: None if r20 is None else r20 * 0.55,
        20: r20 if r20 is not None else (None if r60 is None else r60 * 0.40),
    }


def _raw_alpha_targets(context: Mapping[str, Any]) -> Dict[int, Optional[float]]:
    result: Dict[int, Optional[float]] = {}
    for horizon, (name, scale) in {
        1: ("5d", 1 / 3),
        5: ("5d", 1.0),
        10: ("20d", 0.55),
        20: ("20d", 1.0),
    }.items():
        block = _horizon_block(context, name)
        values = [
            value
            for value in (
                _first_number(block, ("excess_vs_spy_pct",)),
                _first_number(block, ("excess_vs_qqq_pct",)),
            )
            if value is not None
        ]
        result[horizon] = None if not values else sum(values) / len(values) * scale
    return result


def _realized_volatility(context: Mapping[str, Any]) -> Optional[float]:
    prediction = _find_mapping(context, ("prediction_context",))
    return _first_number(
        prediction,
        ("realized_vol_20d_pct", "realized_volatility_20d_pct"),
    )


def _feature_probability(
    features: AlphaFeatures,
    horizon: int,
    *,
    challenger: bool,
) -> tuple[float, float]:
    if challenger:
        weights = {
            "trend": 0.18,
            "momentum": 0.30,
            "relative_strength": 0.20,
            "sector_relative_strength": 0.08,
            "volume_confirmation": 0.16,
            "market_regime": 0.08,
        }
    elif horizon <= 5:
        weights = {
            "trend": 0.20,
            "momentum": 0.24,
            "relative_strength": 0.20,
            "sector_relative_strength": 0.08,
            "volume_confirmation": 0.16,
            "market_regime": 0.12,
        }
    elif horizon <= 10:
        weights = {
            "trend": 0.26,
            "momentum": 0.18,
            "relative_strength": 0.20,
            "sector_relative_strength": 0.08,
            "volume_confirmation": 0.08,
            "fundamental_quality": 0.08,
            "market_regime": 0.12,
        }
    else:
        weights = {
            "trend": 0.22,
            "momentum": 0.10,
            "relative_strength": 0.16,
            "sector_relative_strength": 0.10,
            "fundamental_quality": 0.18,
            "catalyst": 0.08,
            "market_regime": 0.16,
        }

    numerator = 0.0
    observed = 0.0
    total = sum(weights.values())
    for name, weight in weights.items():
        value = _finite(getattr(features, name, None))
        if value is None:
            continue
        numerator += (_clamp(value, 0, 100) - 50.0) / 50.0 * weight
        observed += weight
    if observed <= 0:
        return 0.5, 0.0
    multiplier = 2.2 if challenger else 2.0
    return _logistic(numerator / observed * multiplier), observed / total


def _return_probability(expected: Optional[float], sigma: float) -> Optional[float]:
    if expected is None:
        return None
    return _logistic(expected / max(0.35, sigma * 0.80))


def _regime_adjustment(regime: str, horizon: int) -> float:
    key = str(regime or "").strip().lower()
    if key == "risk_on":
        return 0.025 if horizon <= 5 else 0.035
    if key == "risk_off":
        return -0.045 if horizon <= 5 else -0.055
    return 0.0


def _blend_optional(
    primary: Optional[float],
    history: Optional[float],
    history_weight: float,
) -> float:
    if primary is None and history is None:
        return 0.0
    if primary is None:
        return float(history)
    if history is None:
        return float(primary)
    weight = _clamp(history_weight, 0.0, 0.65)
    return float(primary) * (1 - weight) + float(history) * weight


class V7ForecastEngine:
    """Calibrated, regime-aware forecast layer with strict no-lookahead history."""

    version = V7_FORECAST_VERSION

    def __init__(
        self,
        history: ForecastHistory | None = None,
        *,
        history_db_path: str | None = None,
    ) -> None:
        # Production passes the same normalized V6/V7 database used by its
        # write/read stores. Keeping calibration on that exact path avoids a
        # hidden second database drifting from the production decision history.
        self.history = history or ForecastHistory(history_db_path or "v6_data/v6_daily.db")

    def forecast(
        self,
        *,
        symbol: str,
        instrument_type: str,
        effective_trade_date: str | None,
        context: Mapping[str, Any],
        features: AlphaFeatures,
        market_regime: str | None,
        atr: float | None,
        current_price: float | None,
    ) -> ForecastBundle:
        # Unknown/malformed dates are deliberately passed through as missing.
        # ForecastHistory then returns prior-only calibration. Never substitute a
        # far-future sentinel, because that would admit future outcomes in replay.
        as_of = str(effective_trade_date or "").strip()[:10] or None
        regime = str(market_regime or "unknown").strip().lower() or "unknown"
        raw_returns = _raw_return_targets(context)
        raw_alphas = _raw_alpha_targets(context)
        realized_vol = _realized_volatility(context)
        atr_pct = (
            abs(float(atr)) / float(current_price) * 100.0
            if _finite(atr) is not None
            and _finite(current_price) is not None
            and float(current_price) > 0
            else None
        )
        daily_vol = (
            float(realized_vol) / math.sqrt(252.0)
            if realized_vol is not None and realized_vol > 0
            else (atr_pct if atr_pct is not None and atr_pct > 0 else 1.5)
        )

        selections: Dict[int, Dict[str, Any]] = {}
        horizons: Dict[str, ForecastHorizon] = {}
        for horizon in FORECAST_HORIZONS:
            selection = self.history.select_champion(
                as_of_date=as_of,
                horizon_days=horizon,
                regime=regime,
            )
            selections[horizon] = selection
            selected_champion = str(selection["champion_model"])
            selected_challenger = str(selection["challenger_model"])
            challenger_active = selected_champion == "momentum_challenger"
            feature_p, feature_coverage = _feature_probability(
                features,
                horizon,
                challenger=False,
            )
            challenger_p, challenger_coverage = _feature_probability(
                features,
                horizon,
                challenger=True,
            )
            sigma = max(0.35, daily_vol * math.sqrt(float(horizon)))
            return_p = _return_probability(raw_returns.get(horizon), sigma)
            components = [(feature_p, 0.58)] + (
                [] if return_p is None else [(return_p, 0.42)]
            )
            raw_probability = sum(value * weight for value, weight in components) / sum(
                weight for _, weight in components
            )
            raw_probability = _clamp(
                raw_probability + _regime_adjustment(regime, horizon),
                0.03,
                0.97,
            )
            challenger_raw = _clamp(
                challenger_p
                + 0.20 * ((return_p or 0.5) - 0.5)
                + _regime_adjustment(regime, horizon),
                0.03,
                0.97,
            )

            calibration = self.history.calibration(
                as_of_date=as_of,
                horizon_days=horizon,
                raw_probability_up=raw_probability,
                regime=regime,
                probability_key="probability_up",
            )
            challenger_calibration = self.history.calibration(
                as_of_date=as_of,
                horizon_days=horizon,
                raw_probability_up=challenger_raw,
                regime=regime,
                probability_key="challenger_probability_up",
            )

            active_raw = challenger_raw if challenger_active else raw_probability
            active_calibration = (
                challenger_calibration if challenger_active else calibration
            )
            shadow_calibration = calibration if challenger_active else challenger_calibration
            probability = active_calibration.probability_up
            shadow = shadow_calibration.probability_up

            history_weight = min(0.55, active_calibration.samples / 200.0)
            expected_return = _blend_optional(
                raw_returns.get(horizon),
                active_calibration.historical_return_pct,
                history_weight,
            )
            expected_alpha = _blend_optional(
                raw_alphas.get(horizon),
                active_calibration.historical_alpha_pct,
                history_weight,
            )
            p10 = (
                active_calibration.return_p10_pct
                if active_calibration.samples >= 10
                and active_calibration.return_p10_pct is not None
                else expected_return - 1.2816 * sigma
            )
            p50 = (
                active_calibration.return_p50_pct
                if active_calibration.samples >= 10
                and active_calibration.return_p50_pct is not None
                else expected_return
            )
            p90 = (
                active_calibration.return_p90_pct
                if active_calibration.samples >= 10
                and active_calibration.return_p90_pct is not None
                else expected_return + 1.2816 * sigma
            )
            expected_mfe = (
                active_calibration.historical_mfe_pct
                if active_calibration.historical_mfe_pct is not None
                else max(0.0, expected_return) + 0.80 * sigma
            )
            expected_mae = (
                active_calibration.historical_mae_pct
                if active_calibration.historical_mae_pct is not None
                else min(0.0, expected_return) - 0.80 * sigma
            )
            reliability = min(1.0, math.sqrt(active_calibration.samples / 100.0))
            agreement = 1.0 - min(
                1.0,
                abs(feature_p - (return_p or feature_p)) * 2.0,
            )
            evidence = _clamp(
                0.70 * feature_coverage
                + 0.30 * (1.0 if raw_returns.get(horizon) is not None else 0.0),
                0.0,
                1.0,
            )
            confidence = _clamp(
                0.45 * evidence + 0.35 * reliability + 0.20 * agreement,
                0.0,
                1.0,
            )
            direction = (
                "bullish"
                if probability >= 0.58
                else "bearish"
                if probability <= 0.42
                else "neutral"
            )

            horizons[f"{horizon}d"] = ForecastHorizon(
                horizon,
                round(active_raw, 4),
                round(probability, 4),
                round(expected_return, 4),
                round(expected_alpha, 4),
                round(p10, 4),
                round(p50, 4),
                round(p90, 4),
                round(expected_mfe, 4),
                round(expected_mae, 4),
                round(evidence, 4),
                round(confidence, 4),
                active_calibration.samples,
                active_calibration.status,
                regime,
                selected_champion,
                selected_challenger,
                round(shadow, 4),
                direction,
                round(probability * 100.0, 2),
                {
                    "feature_probability": round(feature_p, 4),
                    "return_probability": None
                    if return_p is None
                    else round(return_p, 4),
                    "sigma_pct": round(sigma, 4),
                    "raw_expected_return_pct": raw_returns.get(horizon),
                    "raw_expected_alpha_pct": raw_alphas.get(horizon),
                    "calibration": calibration.to_dict(),
                    "challenger_calibration": challenger_calibration.to_dict(),
                    "challenger_evidence_coverage": round(challenger_coverage, 4),
                },
            )

        coverage = sum(item.evidence_coverage for item in horizons.values()) / len(horizons)
        primary_selection = selections[5]
        return ForecastBundle(
            str(symbol or "").strip().upper(),
            str(instrument_type or "STOCK").strip().upper(),
            effective_trade_date,
            regime,
            self.version,
            horizons,
            "5d",
            str(primary_selection["champion_model"]),
            str(primary_selection["challenger_model"]),
            str(primary_selection["status"]),
            round(coverage, 4),
            {
                "history_available": self.history.available,
                "realized_vol_20d_pct": realized_vol,
                "daily_volatility_pct": round(daily_vol, 4),
                "champion_selection_by_horizon": {
                    f"{horizon}d": selections[horizon]
                    for horizon in FORECAST_HORIZONS
                },
                "numeric_llm_influence": "none",
                "as_of_policy": (
                    "outcome_end_trade_date_strictly_before_effective_trade_date; "
                    "missing_effective_date_uses_prior_only"
                ),
            },
        )
