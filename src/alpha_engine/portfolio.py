from __future__ import annotations

from dataclasses import replace
from typing import Iterable, Mapping, Optional

from .models import AlphaDecision, TradePlan


class PortfolioRiskOverlay:
    """Portfolio-aware sizing gate applied after single-name Alpha Engine.

    It can only reduce risk.  It never upgrades WAIT/AVOID to an actionable
    signal and never increases the Alpha Engine position cap.
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

        gross = 0.0
        sector_exposure = 0.0
        symbol_exposure = 0.0
        target_symbol = decision.symbol.upper()
        target_sector_key = str(target_sector or "").strip().lower()

        for raw in positions or ():
            weight = self._safe_fraction(raw.get("weight", raw.get("weight_pct", 0.0)))
            if weight > 1.0:
                weight /= 100.0
            gross += weight

            symbol = str(raw.get("symbol") or raw.get("code") or "").strip().upper()
            if symbol == target_symbol:
                symbol_exposure += weight

            sector = str(raw.get("sector") or "").strip().lower()
            if target_sector_key and sector == target_sector_key:
                sector_exposure += weight

        caps = [
            plan.max_position_pct,
            max(0.0, self.max_single_name_pct - symbol_exposure),
            max(0.0, self.max_gross_pct - gross),
        ]
        limitations = list(decision.limitations)

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
            limitations.append("portfolio hard drawdown gate active")
        elif dd >= self.drawdown_soft_limit_pct:
            caps.append(plan.max_position_pct * 0.5)
            limitations.append("portfolio soft drawdown de-risking active")

        final_cap = round(max(0.0, min(caps)), 4)
        if final_cap < plan.max_position_pct:
            limitations.append(
                f"portfolio gate reduced max position {plan.max_position_pct:.2%}->{final_cap:.2%}"
            )

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
