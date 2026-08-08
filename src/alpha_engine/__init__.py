"""Deterministic Alpha decision layer.

This package deliberately does not call an LLM. It converts already-observed
market/technical/fundamental evidence into auditable scores and a bounded trade
plan. The existing LLM remains an explanation/research layer.
"""

from .engine import AlphaDecisionEngine
from .models import AlphaDecision, AlphaFeatures, TradePlan

__all__ = ["AlphaDecisionEngine", "AlphaDecision", "AlphaFeatures", "TradePlan"]
