"""V6 Daily Intelligence.

A deterministic, auditable US-stock daily decision layer built on persisted V4
analysis snapshots. V6 does not place orders and does not let LLM prose directly
set numeric scores.
"""

from .engine import V6DailyEngine
from .models import V6Signal
from .store import V6DailyStore

__all__ = ["V6DailyEngine", "V6DailyStore", "V6Signal"]
