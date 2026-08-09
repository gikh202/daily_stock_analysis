from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class V6Signal:
    analysis_history_id: int
    query_id: Optional[str]
    code: str
    analysis_created_at: str
    baseline_price: float
    direction: str
    forecast_score: Optional[float]
    decision: str
    quality_score: Optional[float]
    opportunity_score: Optional[float]
    risk_score: Optional[float]
    evidence_coverage: float
    market_regime: Optional[str]
    market_breadth: Optional[str]
    model_used: Optional[str]
    llm_health: str
    features: Dict[str, Optional[float]] = field(default_factory=dict)
    trade_plan: Dict[str, Any] = field(default_factory=dict)
    catalysts: Tuple[str, ...] = ()
    risks: Tuple[str, ...] = ()
    limitations: Tuple[str, ...] = ()
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    instrument_type: str = "STOCK"
    effective_trade_date: Optional[str] = None
    horizon_forecasts: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    context_features: Dict[str, Any] = field(default_factory=dict)

    @property
    def actionable(self) -> bool:
        return self.decision in {"BUY_SETUP", "WATCH"}

    def horizon(self, days: int) -> Dict[str, Any]:
        return dict(self.horizon_forecasts.get(f"{int(days)}d") or {})
