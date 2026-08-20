from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class ForecastHorizon:
    horizon_days: int
    raw_probability_up: float
    probability_up: float
    expected_return_pct: float
    expected_alpha_vs_spy_pct: float
    p10_return_pct: float
    p50_return_pct: float
    p90_return_pct: float
    expected_mfe_pct: float
    expected_mae_pct: float
    evidence_coverage: float
    forecast_confidence: float
    calibration_samples: int
    calibration_status: str
    regime: str
    champion_model: str
    challenger_model: str
    challenger_probability_up: float
    direction: str
    score: float
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ForecastBundle:
    symbol: str
    instrument_type: str
    effective_trade_date: Optional[str]
    regime: str
    model_version: str
    horizons: Dict[str, ForecastHorizon]
    primary_horizon: str
    champion_model: str
    challenger_model: str
    promotion_status: str
    evidence_coverage: float
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    @property
    def primary(self) -> ForecastHorizon:
        return self.horizons[self.primary_horizon]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "instrument_type": self.instrument_type,
            "effective_trade_date": self.effective_trade_date,
            "regime": self.regime,
            "model_version": self.model_version,
            "primary_horizon": self.primary_horizon,
            "champion_model": self.champion_model,
            "challenger_model": self.challenger_model,
            "promotion_status": self.promotion_status,
            "evidence_coverage": self.evidence_coverage,
            "horizons": {key: value.to_dict() for key, value in self.horizons.items()},
            "diagnostics": dict(self.diagnostics),
        }


@dataclass(frozen=True)
class ForecastDecision:
    decision: str
    rationale: str
    forecast_confidence: float
    expected_edge_pct: float
    downside_pct: float
    max_position_fraction: float
    gates: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TimingAssessment:
    action: str
    better_entry_probability: float
    expected_better_price: Optional[float]
    expected_improvement_pct: float
    recheck_minutes: int
    rationale: str
    terminal: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
