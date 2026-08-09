"""Deterministic V5 Alpha decision layer.

The package deliberately does not call an LLM. It converts already-observed,
structured market evidence into auditable scores, bounded trade plans and
shadow outcomes. The existing LLM remains a research/explanation layer.
"""

from .engine import AlphaDecisionEngine
from .features import AdaptedAlphaInput, AlphaFeatureAdapter
from .models import AlphaDecision, AlphaFeatures, TradePlan
from .portfolio import PortfolioRiskOverlay
from .shadow_store import AlphaShadowStore

__all__ = [
    "AdaptedAlphaInput",
    "AlphaDecision",
    "AlphaDecisionEngine",
    "AlphaFeatureAdapter",
    "AlphaFeatures",
    "AlphaShadowStore",
    "PortfolioRiskOverlay",
    "TradePlan",
]
