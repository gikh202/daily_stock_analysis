from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from .engine import V7ForecastEngine as _BaseForecastEngine
from .engine import _find_mapping
from .strong_signal import scope_reliability_haircut, strong_signal_skill


V9_FORECAST_VERSION = "v9.0-strong-signal-oos.1"
_FUTURE_RETURN_SEMANTICS = {
    "forecast",
    "future_forecast",
    "expected_future_return",
    "model_forecast",
}


def _normalized_prediction_context(
    context: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, float | None]]]:
    """Stop observed trailing return from masquerading as a future return target."""
    prediction = _find_mapping(context, ("prediction_context",))
    horizons = prediction.get("horizons") if isinstance(prediction, Mapping) else None
    normalized_horizons: dict[str, dict[str, Any]] = {}
    audit: dict[str, dict[str, float | None]] = {}
    if isinstance(horizons, Mapping):
        for name, raw in horizons.items():
            if not isinstance(raw, Mapping):
                continue
            block = dict(raw)
            legacy_trailing = block.get("trailing_return_pct")
            if legacy_trailing is None:
                legacy_trailing = block.get("target_return_pct")
            semantics = str(block.get("return_semantics") or "").strip().lower()
            if legacy_trailing is not None:
                block["trailing_return_pct"] = legacy_trailing
                if semantics not in _FUTURE_RETURN_SEMANTICS:
                    # The legacy field was created from already-observed N-day
                    # price change. Momentum is already represented in features;
                    # do not feed it a second time as future expected return.
                    block.pop("target_return_pct", None)
                    block["return_semantics"] = "trailing_observed_not_forecast"
            qqq_alpha = block.get("excess_vs_qqq_pct")
            # The legacy forecast engine averaged SPY and QQQ excess but exposed
            # the result as expected_alpha_vs_spy_pct. Keep QQQ for diagnostics,
            # while the actual SPY-alpha field now uses SPY only.
            block.pop("excess_vs_qqq_pct", None)
            normalized_horizons[str(name)] = block
            audit[str(name)] = {
                "trailing_return_pct": (
                    float(legacy_trailing) if isinstance(legacy_trailing, (int, float)) else None
                ),
                "observed_excess_vs_qqq_pct": (
                    float(qqq_alpha) if isinstance(qqq_alpha, (int, float)) else None
                ),
            }

    normalized_prediction = dict(prediction)
    normalized_prediction["horizons"] = normalized_horizons
    normalized_prediction["return_semantics"] = (
        "trailing_returns_are_observed_momentum; expected_return_is_model_output"
    )
    normalized_context = dict(context)
    # Put the normalized block at the root so the breadth-first contract lookup
    # finds it before any nested legacy copy.
    normalized_context["prediction_context"] = normalized_prediction
    return normalized_context, audit


class V7ForecastEngine(_BaseForecastEngine):
    """Production V9 wrapper around the calibrated V7 forecast core.

    The underlying probability calibration remains unchanged and strict-as-of.
    V9 changes only production semantics/governance:
    1. observed trailing returns are not reused as future return forecasts;
    2. expected_alpha_vs_spy_pct is actually SPY-relative;
    3. execution reliability must also pass a decisive-signal OOS subset;
    4. broad fallback scopes receive an evidence haircut.
    """

    version = V9_FORECAST_VERSION

    def forecast(
        self,
        *,
        symbol: str,
        instrument_type: str,
        effective_trade_date: str | None,
        context: Mapping[str, Any],
        features: Any,
        market_regime: str | None,
        atr: float | None,
        current_price: float | None,
    ):
        normalized_context, semantic_audit = _normalized_prediction_context(context)
        bundle = super().forecast(
            symbol=symbol,
            instrument_type=instrument_type,
            effective_trade_date=effective_trade_date,
            context=normalized_context,
            features=features,
            market_regime=market_regime,
            atr=atr,
            current_price=current_price,
        )

        horizons = {}
        strong_by_horizon: dict[str, Any] = {}
        for key, horizon in bundle.horizons.items():
            probability_key = (
                "challenger_probability_up"
                if str(horizon.champion_model) == "momentum_challenger"
                else "probability_up"
            )
            strong = strong_signal_skill(
                self.history,
                as_of_date=effective_trade_date,
                horizon_days=horizon.horizon_days,
                regime=market_regime,
                symbol=symbol,
                instrument_type=instrument_type,
                probability_key=probability_key,
            )
            haircut = scope_reliability_haircut(strong.get("scope"))
            strong_samples = max(0, int(strong.get("samples") or 0))
            effective_samples = int(strong_samples * haircut)
            strong_hit = strong.get("hit_rate")
            strong_baseline = strong.get("majority_baseline_accuracy")
            strong_skill = strong.get("skill")
            # A decisive subset must have real skill, not merely pass the older
            # 52% overall threshold. If it does not, downstream reliability sees
            # no tradable hit rate and therefore assigns zero trading weight.
            strong_pass = bool(
                strong_samples >= 20
                and strong_hit is not None
                and float(strong_hit) >= 0.55
                and (
                    strong_baseline is None
                    or float(strong_hit) >= float(strong_baseline) + 0.02
                )
            )
            diagnostics = dict(horizon.diagnostics)
            diagnostics.update(
                {
                    "probability_calibration_samples": horizon.calibration_samples,
                    "overall_historical_direction_hit_rate": horizon.historical_direction_hit_rate,
                    "overall_historical_majority_baseline_accuracy": (
                        horizon.historical_majority_baseline_accuracy
                    ),
                    "strong_signal_samples": strong_samples,
                    "strong_signal_effective_samples": effective_samples,
                    "strong_signal_direction_hit_rate": strong_hit,
                    "strong_signal_majority_baseline_accuracy": strong_baseline,
                    "strong_signal_direction_skill": strong_skill,
                    "strong_signal_pass": strong_pass,
                    "strong_signal_scope": strong.get("scope"),
                    "scope_reliability_haircut": haircut,
                    "production_reliability_sample_semantics": (
                        "strong_signal_samples_after_scope_haircut"
                    ),
                    "observed_trailing_return_pct": semantic_audit.get(key, {}).get(
                        "trailing_return_pct"
                    ),
                    "observed_excess_vs_qqq_pct": semantic_audit.get(key, {}).get(
                        "observed_excess_vs_qqq_pct"
                    ),
                    "expected_return_semantics": (
                        "future_model_distribution; observed trailing return excluded from return prior"
                    ),
                    "expected_alpha_vs_spy_semantics": "SPY_relative_only",
                    "correlated_momentum_policy": (
                        "trailing_return_used_in_momentum_features_only_not_reused_as_future_return"
                    ),
                }
            )
            diagnostics["historical_direction_hit_rate"] = (
                float(strong_hit) if strong_pass else None
            )
            diagnostics["historical_majority_baseline_accuracy"] = (
                float(strong_baseline)
                if strong_pass and strong_baseline is not None
                else None
            )
            horizons[key] = replace(
                horizon,
                calibration_samples=effective_samples,
                diagnostics=diagnostics,
            )
            strong_by_horizon[key] = {
                **strong,
                "effective_samples": effective_samples,
                "scope_haircut": haircut,
                "production_pass": strong_pass,
            }

        diagnostics = dict(bundle.diagnostics)
        diagnostics.update(
            {
                "production_forecast_version": self.version,
                "return_semantics_policy": (
                    "observed_trailing_return_is_momentum_not_future_expected_return"
                ),
                "alpha_benchmark_policy": "SPY field is SPY-only; QQQ excess is diagnostic",
                "strong_signal_reliability": strong_by_horizon,
                "strong_signal_thresholds": {"bearish_max": 0.42, "bullish_min": 0.58},
                "minimum_strong_signal_hit_rate": 0.55,
                "minimum_strong_signal_samples_before_scope_haircut": 20,
            }
        )
        return replace(
            bundle,
            model_version=self.version,
            horizons=horizons,
            diagnostics=diagnostics,
        )
