from __future__ import annotations

from dataclasses import replace
from typing import Iterable, Mapping, Optional

from .models import AlphaDecision


class PortfolioRiskOverlay:
    """Portfolio-aware sizing gate that is monotonic in risk.

    The same cap function is reusable by the V6/V9 forecast execution layer so
    portfolio limits cannot be lost when a later model rebuilds the single-name
    trade plan. It can only reduce a proposed position cap; it never upgrades a
    WAIT/AVOID signal or increases risk.
    """

    def __init__(
        self,
        *,
        max_single_name_pct: float = 0.15,
        max_sector_pct: float = 0.40,
        max_gross_pct: float = 1.00,
        drawdown_soft_limit_pct: float = 8.0,
        drawdown_hard_limit_pct: float = 15.0,
    ) -> None:
        self.max_single_name_pct = max(0.0, min(float(max_single_name_pct), 1.0))
        self.max_sector_pct = max(0.0, min(float(max_sector_pct), 1.0))
        self.max_gross_pct = max(0.0, min(float(max_gross_pct), 2.0))
        self.drawdown_soft_limit_pct = max(0.0, float(drawdown_soft_limit_pct))
        self.drawdown_hard_limit_pct = max(
            self.drawdown_soft_limit_pct,
            float(drawdown_hard_limit_pct),
        )

    @staticmethod
    def _safe_fraction(value: object) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, number)

    def position_cap(
        self,
        *,
        symbol: str,
        proposed_max_position_pct: float,
        positions: Optional[Iterable[Mapping[str, object]]] = None,
        target_sector: Optional[str] = None,
        portfolio_drawdown_pct: Optional[float] = None,
    ) -> tuple[float, tuple[str, ...]]:
        """Return the maximum allowed *new total* position fraction and reasons."""
        proposed = max(0.0, self._safe_fraction(proposed_max_position_pct))
        if proposed <= 0.0:
            return 0.0, ()

        gross = 0.0
        sector_exposure = 0.0
        symbol_exposure = 0.0
        target_symbol = str(symbol or "").strip().upper()
        target_sector_key = str(target_sector or "").strip().lower()

        for raw in positions or ():
            weight = self._safe_fraction(raw.get("weight", raw.get("weight_pct", 0.0)))
            if weight > 1.0:
                weight /= 100.0
            gross += weight

            existing_symbol = str(raw.get("symbol") or raw.get("code") or "").strip().upper()
            if existing_symbol == target_symbol:
                symbol_exposure += weight

            sector = str(raw.get("sector") or "").strip().lower()
            if target_sector_key and sector == target_sector_key:
                sector_exposure += weight

        caps = [
            proposed,
            max(0.0, self.max_single_name_pct - symbol_exposure),
            max(0.0, self.max_gross_pct - gross),
        ]
        reasons: list[str] = []
        if target_sector_key:
            caps.append(max(0.0, self.max_sector_pct - sector_exposure))

        dd = 0.0
        if portfolio_drawdown_pct is not None:
            try:
                dd = max(0.0, float(portfolio_drawdown_pct))
            except (TypeError, ValueError):
                dd = 0.0

        if dd >= self.drawdown_hard_limit_pct:
            caps.append(0.0)
            reasons.append("portfolio hard drawdown gate active")
        elif dd >= self.drawdown_soft_limit_pct:
            caps.append(proposed * 0.5)
            reasons.append("portfolio soft drawdown de-risking active")

        final_cap = round(max(0.0, min(caps)), 4)
        if final_cap < proposed:
            reasons.append(
                f"portfolio gate reduced max position {proposed:.2%}->{final_cap:.2%}"
            )
        if self.max_gross_pct - gross <= 0:
            reasons.append("portfolio gross exposure limit reached")
        if self.max_single_name_pct - symbol_exposure <= 0:
            reasons.append("single-name exposure limit reached")
        if target_sector_key and self.max_sector_pct - sector_exposure <= 0:
            reasons.append("sector exposure limit reached")
        return final_cap, tuple(dict.fromkeys(reasons))

    def apply(
        self,
        decision: AlphaDecision,
        *,
        positions: Optional[Iterable[Mapping[str, object]]] = None,
        target_sector: Optional[str] = None,
        portfolio_drawdown_pct: Optional[float] = None,
    ) -> AlphaDecision:
        plan = decision.trade_plan
        if decision.decision not in {"BUY_SETUP", "WATCH"} or plan.max_position_pct <= 0:
            return decision

        final_cap, cap_reasons = self.position_cap(
            symbol=decision.symbol,
            proposed_max_position_pct=plan.max_position_pct,
            positions=positions,
            target_sector=target_sector,
            portfolio_drawdown_pct=portfolio_drawdown_pct,
        )
        limitations = list(decision.limitations)
        limitations.extend(cap_reasons)

        action = plan.action
        decision_name = decision.decision
        if final_cap <= 0.0:
            action = "WAIT"
            decision_name = "WAIT"

        new_plan = replace(plan, action=action, max_position_pct=final_cap)
        return replace(
            decision,
            decision=decision_name,
            trade_plan=new_plan,
            limitations=tuple(dict.fromkeys(limitations)),
        )
