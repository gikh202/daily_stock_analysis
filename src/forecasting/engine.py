from __future__ import annotations

import math
from statistics import NormalDist
from typing import Any, Dict, Mapping, Optional, Sequence

from src.alpha_engine.models import AlphaFeatures

from .history import ForecastHistory
from .models import ForecastBundle, ForecastHorizon


V7_FORECAST_VERSION = "v7.3-forecast-reliability.1"
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


def _prediction_horizons(context: Mapping[str, Any]) -> Mapping[str, Any]:
    prediction = _find_mapping(context, ("prediction_context",))
    horizons = prediction.get("horizons")
    return horizons if isinstance(horizons, dict) else {}


def _horizon_block(context: Mapping[str, Any], horizon: int) -> Mapping[str, Any]:
    horizons = _prediction_horizons(context)
    value = horizons.get(f"{int(horizon)}d")
    return value if isinstance(value, dict) else {}


def _native_return_target(context: Mapping[str, Any], horizon: int) -> Optional[float]:
    """Read only the matching horizon. V7.3 never creates 1D/10D by scaling 5D/20D."""
    block = _horizon_block(context, horizon)
    return _first_number(
        block,
        ("target_return_pct", "expected_return_pct", "return_target_pct"),
    )


def _native_alpha_target(context: Mapping[str, Any], horizon: int) -> Optional[float]:
    block = _horizon_block(context, horizon)
    values = [
        value
        for value in (
            _first_number(block, ("excess_vs_spy_pct",)),
            _first_number(block, ("excess_vs_qqq_pct",)),
        )
        if value is not None
    ]
    return None if not values else sum(values) / len(values)


def _realized_volatility(context: Mapping[str, Any]) -> Optional[float]:
    prediction = _find_mapping(context, ("prediction_context",))
    return _first_number(
        prediction,
        ("realized_vol_20d_pct", "realized_volatility_20d_pct"),
    )


def _feature_weights(horizon: int, *, challenger: bool) -> dict[str, float]:
    if challenger:
        return {
            "trend": 0.18,
            "momentum": 0.30,
            "relative_strength": 0.20,
            "sector_relative_strength": 0.08,
            "volume_confirmation": 0.16,
            "market_regime": 0.08,
        }
    if horizon <= 1:
        return {
            "trend": 0.14,
            "momentum": 0.34,
            "relative_strength": 0.16,
            "sector_relative_strength": 0.06,
            "volume_confirmation": 0.20,
            "market_regime": 0.10,
        }
    if horizon <= 5:
        return {
            "trend": 0.20,
            "momentum": 0.24,
            "relative_strength": 0.20,
            "sector_relative_strength": 0.08,
            "volume_confirmation": 0.16,
            "market_regime": 0.12,
        }
    if horizon <= 10:
        return {
            "trend": 0.26,
            "momentum": 0.18,
            "relative_strength": 0.20,
            "sector_relative_strength": 0.08,
            "volume_confirmation": 0.08,
            "fundamental_quality": 0.08,
            "market_regime": 0.12,
        }
    return {
        "trend": 0.22,
        "momentum": 0.10,
        "relative_strength": 0.16,
        "sector_relative_strength": 0.10,
        "fundamental_quality": 0.18,
        "catalyst": 0.08,
        "market_regime": 0.16,
    }


def _feature_probability(
    features: AlphaFeatures,
    horizon: int,
    *,
    challenger: bool,
) -> tuple[float, float]:
    weights = _feature_weights(horizon, challenger=challenger)
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


def _feature_return_target(
    feature_probability: float,
    sigma: float,
    *,
    regime: str,
    horizon: int,
) -> float:
    """Independent horizon return prior derived from that horizon's factors."""
    p = _clamp(
        feature_probability + _regime_adjustment(regime, horizon), 0.03, 0.97
    )
    implied = sigma * NormalDist().inv_cdf(p)
    return _clamp(implied * 0.65, -2.5 * sigma, 2.5 * sigma)


def _joint_expected_return(
    base_return: float,
    probability_up: float,
    sigma: float,
    *,
    calibration_samples: int,
) -> tuple[float, float, float]:
    """Reconcile probability and expected return through one return distribution."""
    p = _clamp(probability_up, 0.02, 0.98)
    probability_implied = sigma * NormalDist().inv_cdf(p)
    reliability = min(1.0, math.sqrt(max(0, calibration_samples) / 50.0))
    coherence_weight = 0.50 + 0.25 * reliability
    reconciled = (
        (1.0 - coherence_weight) * base_return
        + coherence_weight * probability_implied
    )
    return reconciled, probability_implied, coherence_weight


def _decision_weight(horizon: int, status: str, samples: int) -> float:
    """Reliability weight exposed to downstream fusion; 10D is quarantined until mature."""
    if status == "prior_only" or samples <= 0:
        return 0.0
    if horizon == 10 and samples < 50:
        return 0.0
    if status != "mature":
        return 0.10 if horizon != 10 else 0.0
    return min(1.0, samples / 100.0) * (0.35 if horizon == 10 else 1.0)


class V7ForecastEngine:
    """Hierarchically calibrated, horizon-independent forecast layer."""

    version = V7_FORECAST_VERSION

    def __init__(
        self,
        history: ForecastHistory | None = None,
        *,
        history_db_path: str | None = None,
    ) -> None:
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
        as_of = str(effective_trade_date or "").strip()[:10] or None
        symbol_key = str(symbol or "").strip().upper()
        instrument_key = str(instrument_type or "STOCK").strip().upper()
        regime = str(market_regime or "unknown").strip().lower() or "unknown"
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
                symbol=symbol_key,
                instrument_type=instrument_key,
            )
            selections[horizon] = selection
            selected_champion = str(selection["champion_model"])
            selected_challenger = str(selection["challenger_model"])
            challenger_active = selected_champion == "momentum_challenger"

            feature_p, feature_coverage = _feature_probability(
                features, horizon, challenger=False
            )
            challenger_p, challenger_coverage = _feature_probability(
                features, horizon, challenger=True
            )
            sigma = max(0.35, daily_vol * math.sqrt(float(horizon)))
            native_return = _native_return_target(context, horizon)
            factor_return = _feature_return_target(
                feature_p, sigma, regime=regime, horizon=horizon
            )
            raw_return = (
                factor_return
                if native_return is None
                else 0.65 * native_return + 0.35 * factor_return
            )
            native_alpha = _native_alpha_target(context, horizon)
            return_p = _return_probability(raw_return, sigma)

            components = [(feature_p, 0.58), (return_p, 0.42)]
            raw_probability = sum(value * weight for value, weight in components) / sum(
                weight for _, weight in components
            )
            raw_probability = _clamp(
                raw_probability + _regime_adjustment(regime, horizon), 0.03, 0.97
            )
            challenger_raw = _clamp(
                challenger_p
                + 0.20 * (return_p - 0.5)
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
                symbol=symbol_key,
                instrument_type=instrument_key,
            )
            challenger_calibration = self.history.calibration(
                as_of_date=as_of,
                horizon_days=horizon,
                raw_probability_up=challenger_raw,
                regime=regime,
                probability_key="challenger_probability_up",
                symbol=symbol_key,
                instrument_type=instrument_key,
            )
            active_calibration = (
                challenger_calibration if challenger_active else calibration
            )
            shadow_calibration = (
                calibration if challenger_active else challenger_calibration
            )
            active_raw = challenger_raw if challenger_active else raw_probability
            probability = active_calibration.probability_up
            shadow = shadow_calibration.probability_up

            history_weight = min(0.55, active_calibration.samples / 200.0)
            base_expected_return = _blend_optional(
                raw_return,
                active_calibration.historical_return_pct,
                history_weight,
            )
            (
                expected_return,
                probability_implied_return,
                coherence_weight,
            ) = _joint_expected_return(
                base_expected_return,
                probability,
                sigma,
                calibration_samples=active_calibration.samples,
            )
            expected_alpha = _blend_optional(
                native_alpha,
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
            agreement = 1.0 - min(1.0, abs(feature_p - return_p) * 2.0)
            evidence = _clamp(
                0.70 * feature_coverage
                + 0.30 * (1.0 if native_return is not None else 0.65),
                0.0,
                1.0,
            )
            evidence_confidence = _clamp(
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
            probability_semantics = (
                "uncalibrated_model_tendency"
                if active_calibration.status == "prior_only"
                else "historically_calibrated_probability"
            )
            decision_weight = _decision_weight(
                horizon, active_calibration.status, active_calibration.samples
            )

            horizons[f"{horizon}d"] = ForecastHorizon(
                horizon_days=horizon,
                raw_probability_up=round(active_raw, 4),
                probability_up=round(probability, 4),
                expected_return_pct=round(expected_return, 4),
                expected_alpha_vs_spy_pct=round(expected_alpha, 4),
                p10_return_pct=round(p10, 4),
                p50_return_pct=round(p50, 4),
                p90_return_pct=round(p90, 4),
                expected_mfe_pct=round(expected_mfe, 4),
                expected_mae_pct=round(expected_mae, 4),
                evidence_coverage=round(evidence, 4),
                forecast_confidence=round(evidence_confidence, 4),
                calibration_samples=active_calibration.samples,
                calibration_status=active_calibration.status,
                regime=regime,
                champion_model=selected_champion,
                challenger_model=selected_challenger,
                challenger_probability_up=round(shadow, 4),
                direction=direction,
                score=round(probability * 100.0, 2),
                diagnostics={
                    "feature_probability": round(feature_p, 4),
                    "return_probability": round(return_p, 4),
                    "sigma_pct": round(sigma, 4),
                    "native_expected_return_pct": native_return,
                    "factor_expected_return_pct": round(factor_return, 4),
                    "raw_expected_return_pct": round(raw_return, 4),
                    "pre_coherence_expected_return_pct": round(
                        base_expected_return, 4
                    ),
                    "probability_implied_return_pct": round(
                        probability_implied_return, 4
                    ),
                    "coherence_weight": round(coherence_weight, 4),
                    "native_expected_alpha_pct": native_alpha,
                    "calibration": calibration.to_dict(),
                    "challenger_calibration": challenger_calibration.to_dict(),
                    "calibration_scope": active_calibration.calibration_scope,
                    "historical_direction_hit_rate": (
                        active_calibration.historical_direction_hit_rate
                    ),
                    "probability_semantics": probability_semantics,
                    "decision_weight": round(decision_weight, 4),
                    "evidence_confidence_semantics": (
                        "evidence_and_reliability_score_not_win_rate"
                    ),
                    "challenger_evidence_coverage": round(
                        challenger_coverage, 4
                    ),
                },
            )

        coverage = sum(item.evidence_coverage for item in horizons.values()) / len(
            horizons
        )
        primary_selection = selections[5]
        return ForecastBundle(
            symbol_key,
            instrument_key,
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
                "horizon_return_policy": (
                    "native_horizon_or_horizon_specific_factor_prior; "
                    "no_cross_horizon_scaling"
                ),
                "calibration_policy": (
                    "symbol_to_instrument_type_to_regime_to_global; strict_as_of"
                ),
                "probability_return_policy": (
                    "joint_distribution_coherence_reconciliation"
                ),
                "reliability_policy": (
                    "10d decision weight is zero until >=50 mature samples"
                ),
                "as_of_policy": (
                    "outcome_end_trade_date_strictly_before_effective_trade_date; "
                    "missing_effective_date_uses_prior_only"
                ),
            },
        )
