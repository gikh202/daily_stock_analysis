from src.alpha_engine import AlphaDecisionEngine, AlphaFeatures, PortfolioRiskOverlay


def _buy_setup():
    return AlphaDecisionEngine().evaluate(
        "MSFT",
        AlphaFeatures(
            trend=90,
            momentum=85,
            relative_strength=88,
            volume_confirmation=80,
            fundamental_quality=90,
            catalyst=80,
            market_regime=85,
            volatility_risk=20,
            event_risk=20,
            data_quality=95,
        ),
        current_price=420,
        support=410,
        resistance=455,
        atr=6,
    )


def test_portfolio_overlay_only_reduces_position_size():
    original = _buy_setup()
    assert original.decision == "BUY_SETUP"
    overlay = PortfolioRiskOverlay(max_sector_pct=0.40, max_gross_pct=1.0)
    adjusted = overlay.apply(
        original,
        positions=(
            {"symbol": "GOOGL", "sector": "technology", "weight": 0.22},
            {"symbol": "NVDA", "sector": "technology", "weight": 0.14},
            {"symbol": "VOO", "sector": "index", "weight": 0.45},
        ),
        target_sector="technology",
    )

    assert adjusted.trade_plan.max_position_pct <= original.trade_plan.max_position_pct
    assert adjusted.trade_plan.max_position_pct <= 0.04 + 1e-9
    assert adjusted.decision in {"BUY_SETUP", "WAIT"}


def test_hard_drawdown_gate_never_upgrades_action():
    original = _buy_setup()
    overlay = PortfolioRiskOverlay(drawdown_hard_limit_pct=15)
    adjusted = overlay.apply(original, portfolio_drawdown_pct=18)

    assert adjusted.decision == "WAIT"
    assert adjusted.trade_plan.max_position_pct == 0
    assert any("hard drawdown" in item for item in adjusted.limitations)
