from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class AlphaFeatures:
    """Normalized 0..100 features. None means unavailable, never neutral-by-fiat."""

    trend: Optional[float] = None
    momentum: Optional[float] = None
    relative_strength: Optional[float] = None
    volume_confirmation: Optional[float] = None
    fundamental_quality: Optional[float] = None
    catalyst: Optional[float] = None
    market_regime: Optional[float] = None
    volatility_risk: Optional[float] = None
    event_risk: Optional[float] = None
    data_quality: Optional[float] = None


@dataclass(frozen=True)
class TradePlan:
    action: str
    entry_zone: Optional[Tuple[float, float]] = None
    stop_loss: Optional[float] = None
    targets: Tuple[float, ...] = ()
    max_position_pct: float = 0.0
    risk_reward: Optional[float] = None
    invalidation: Tuple[str, ...] = ()
    confirmations: Tuple[str, ...] = ()


@dataclass(frozen=True)
class AlphaDecision:
    symbol: str
    quality_score: Optional[float]
    opportunity_score: Optional[float]
    risk_score: Optional[float]
    # Compatibility field retained in the V5 shadow schema. Semantically this
    # is evidence coverage, not a calibrated probability that the forecast wins.
    confidence: float
    decision: str
    features: AlphaFeatures
    trade_plan: TradePlan
    reasons: Tuple[str, ...] = ()
    limitations: Tuple[str, ...] = ()
    diagnostics: Dict[str, float] = field(default_factory=dict)

    @property
    def evidence_coverage(self) -> float:
        """Observed evidence coverage in [0, 1], never a win probability."""
        return float(self.confidence)
