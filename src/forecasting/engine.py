from __future__ import annotations

import math
import os
from typing import Any, Dict, Mapping, Optional, Sequence

from src.alpha_engine.models import AlphaFeatures

from .history import ForecastHistory
from .models import ForecastBundle, ForecastHorizon


V7_FORECAST_VERSION = "v7.0-forecast.1"
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
    h5, h20, h60 = _horizon_block(context,"5d"), _horizon_block(context,"20d"), _horizon_block(context,"60d")
    r5 = _first_number(h5,("target_return_pct","expected_return_pct"))
    r20 = _first_number(h20,("target_return_pct","expected_return_pct"))
    r60 = _first_number(h60,("target_return_pct","expected_return_pct"))
    return {1: None if r5 is None else r5/3.0, 5: r5 if r5 is not None else (None if r20 is None else r20*0.30), 10: None if r20 is None else r20*0.55, 20: r20 if r20 is not None else (None if r60 is None else r60*0.40)}


def _raw_alpha_targets(context: Mapping[str, Any]) -> Dict[int, Optional[float]]:
    result: Dict[int, Optional[float]] = {}
    for horizon,(name,scale) in {1:("5d",1/3),5:("5d",1.0),10:("20d",0.55),20:("20d",1.0)}.items():
        block = _horizon_block(context,name)
        values = [v for v in (_first_number(block,("excess_vs_spy_pct",)),_first_number(block,("excess_vs_qqq_pct",))) if v is not None]
        result[horizon] = None if not values else sum(values)/len(values)*scale
    return result


def _realized_volatility(context: Mapping[str, Any]) -> Optional[float]:
    prediction = _find_mapping(context,("prediction_context",))
    return _first_number(prediction,("realized_vol_20d_pct","realized_volatility_20d_pct"))


def _feature_probability(features: AlphaFeatures, horizon: int, *, challenger: bool) -> tuple[float,float]:
    if challenger:
        weights={"trend":.18,"momentum":.30,"relative_strength":.20,"sector_relative_strength":.08,"volume_confirmation":.16,"market_regime":.08}
    elif horizon<=5:
        weights={"trend":.20,"momentum":.24,"relative_strength":.20,"sector_relative_strength":.08,"volume_confirmation":.16,"market_regime":.12}
    elif horizon<=10:
        weights={"trend":.26,"momentum":.18,"relative_strength":.20,"sector_relative_strength":.08,"volume_confirmation":.08,"fundamental_quality":.08,"market_regime":.12}
    else:
        weights={"trend":.22,"momentum":.10,"relative_strength":.16,"sector_relative_strength":.10,"fundamental_quality":.18,"catalyst":.08,"market_regime":.16}
    numerator=observed=0.0
    total=sum(weights.values())
    for name,weight in weights.items():
        value=_finite(getattr(features,name,None))
        if value is None: continue
        numerator += (_clamp(value,0,100)-50.0)/50.0*weight
        observed += weight
    if observed<=0: return .5,0.0
    return _logistic(numerator/observed*(2.2 if challenger else 2.0)), observed/total


def _return_probability(expected: Optional[float], sigma: float) -> Optional[float]:
    return None if expected is None else _logistic(expected/max(.35,sigma*.80))


def _regime_adjustment(regime: str, horizon: int) -> float:
    key=str(regime or "").strip().lower()
    if key=="risk_on": return .025 if horizon<=5 else .035
    if key=="risk_off": return -.045 if horizon<=5 else -.055
    return 0.0


def _blend_optional(primary: Optional[float], history: Optional[float], history_weight: float) -> float:
    if primary is None and history is None: return 0.0
    if primary is None: return float(history)
    if history is None: return float(primary)
    weight=_clamp(history_weight,0.0,.65)
    return float(primary)*(1-weight)+float(history)*weight


class V7ForecastEngine:
    """Calibrated, regime-aware forecast layer with strict no-lookahead history."""
    version=V7_FORECAST_VERSION

    def __init__(self, history: ForecastHistory | None=None) -> None:
        if history is not None:
            self.history=history
        else:
            self.history=ForecastHistory(os.getenv("V7_FORECAST_HISTORY_DB") or os.getenv("V6_DAILY_DB_PATH") or "v6_data/v6_daily.db")

    def forecast(self, *, symbol: str, instrument_type: str, effective_trade_date: str | None, context: Mapping[str,Any], features: AlphaFeatures, market_regime: str | None, atr: float | None, current_price: float | None) -> ForecastBundle:
        as_of=str(effective_trade_date or "9999-12-31")[:10]
        regime=str(market_regime or "unknown").strip().lower() or "unknown"
        raw_returns,raw_alphas=_raw_return_targets(context),_raw_alpha_targets(context)
        realized_vol=_realized_volatility(context)
        atr_pct=abs(float(atr))/float(current_price)*100.0 if _finite(atr) is not None and _finite(current_price) is not None and float(current_price)>0 else None
        daily_vol=float(realized_vol)/math.sqrt(252.0) if realized_vol is not None and realized_vol>0 else (atr_pct if atr_pct is not None and atr_pct>0 else 1.5)
        selection=self.history.select_champion(as_of_date=as_of,horizon_days=5,regime=regime)
        selected_champion,selected_challenger=str(selection["champion_model"]),str(selection["challenger_model"])
        horizons: Dict[str,ForecastHorizon]={}
        for horizon in FORECAST_HORIZONS:
            feature_p,feature_coverage=_feature_probability(features,horizon,challenger=False)
            challenger_p,challenger_coverage=_feature_probability(features,horizon,challenger=True)
            sigma=max(.35,daily_vol*math.sqrt(float(horizon)))
            return_p=_return_probability(raw_returns.get(horizon),sigma)
            components=[(feature_p,.58)] + ([] if return_p is None else [(return_p,.42)])
            raw_probability=sum(v*w for v,w in components)/sum(w for _,w in components)
            raw_probability=_clamp(raw_probability+_regime_adjustment(regime,horizon),.03,.97)
            challenger_raw=_clamp(challenger_p+.20*((return_p or .5)-.5)+_regime_adjustment(regime,horizon),.03,.97)
            calibration=self.history.calibration(as_of_date=as_of,horizon_days=horizon,raw_probability_up=raw_probability,regime=regime)
            challenger_calibration=self.history.calibration(as_of_date=as_of,horizon_days=horizon,raw_probability_up=challenger_raw,regime=regime)
            probability,shadow=(challenger_calibration.probability_up,calibration.probability_up) if selected_champion=="momentum_challenger" else (calibration.probability_up,challenger_calibration.probability_up)
            history_weight=min(.55,calibration.samples/200.0)
            expected_return=_blend_optional(raw_returns.get(horizon),calibration.historical_return_pct,history_weight)
            expected_alpha=_blend_optional(raw_alphas.get(horizon),calibration.historical_alpha_pct,history_weight)
            p10=calibration.return_p10_pct if calibration.samples>=10 and calibration.return_p10_pct is not None else expected_return-1.2816*sigma
            p50=calibration.return_p50_pct if calibration.samples>=10 and calibration.return_p50_pct is not None else expected_return
            p90=calibration.return_p90_pct if calibration.samples>=10 and calibration.return_p90_pct is not None else expected_return+1.2816*sigma
            expected_mfe=calibration.historical_mfe_pct if calibration.historical_mfe_pct is not None else max(0.0,expected_return)+.80*sigma
            expected_mae=calibration.historical_mae_pct if calibration.historical_mae_pct is not None else min(0.0,expected_return)-.80*sigma
            reliability=min(1.0,math.sqrt(calibration.samples/100.0))
            agreement=1.0-min(1.0,abs(feature_p-(return_p or feature_p))*2.0)
            evidence=_clamp(.70*feature_coverage+.30*(1.0 if raw_returns.get(horizon) is not None else 0.0),0.0,1.0)
            confidence=_clamp(.45*evidence+.35*reliability+.20*agreement,0.0,1.0)
            direction="bullish" if probability>=.58 else "bearish" if probability<=.42 else "neutral"
            horizons[f"{horizon}d"]=ForecastHorizon(horizon,round(raw_probability,4),round(probability,4),round(expected_return,4),round(expected_alpha,4),round(p10,4),round(p50,4),round(p90,4),round(expected_mfe,4),round(expected_mae,4),round(evidence,4),round(confidence,4),calibration.samples,calibration.status,regime,selected_champion,selected_challenger,round(shadow,4),direction,round(probability*100.0,2),{"feature_probability":round(feature_p,4),"return_probability":None if return_p is None else round(return_p,4),"sigma_pct":round(sigma,4),"raw_expected_return_pct":raw_returns.get(horizon),"raw_expected_alpha_pct":raw_alphas.get(horizon),"calibration":calibration.to_dict(),"challenger_calibration":challenger_calibration.to_dict(),"challenger_evidence_coverage":round(challenger_coverage,4)})
        coverage=sum(x.evidence_coverage for x in horizons.values())/len(horizons)
        return ForecastBundle(str(symbol or "").strip().upper(),str(instrument_type or "STOCK").strip().upper(),effective_trade_date,regime,self.version,horizons,"5d",selected_champion,selected_challenger,str(selection["status"]),round(coverage,4),{"history_available":self.history.available,"realized_vol_20d_pct":realized_vol,"daily_volatility_pct":round(daily_vol,4),"champion_selection":selection,"numeric_llm_influence":"none","as_of_policy":"outcome_end_trade_date_strictly_before_effective_trade_date"})
