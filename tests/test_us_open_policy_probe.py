from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from scripts.backtest_us_open_confirmation import Observation
from scripts.probe_us_open_v2 import evaluate_production_v2
from scripts.run_us_open_confirmation import LiveSnapshot
from scripts.run_us_open_confirmation_v2 import classify_confirmation_v2

NY = ZoneInfo("America/New_York")


def _packet():
    return {
        "identity": {
            "symbol": "TEST",
            "instrument_type": "STOCK",
            "effective_trade_date": "2026-08-13",
        },
        "assessment": {
            "verdict": "conditional_buy",
            "worth_buying": True,
            "execution_authorized": False,
        },
        "execution": {
            "entry_zone": [100.0, 105.0],
            "stop_loss": 96.0,
            "targets": [112.0, 120.0],
            "max_position_pct": 0.2,
            "confirmations": [],
            "has_active_plan": True,
        },
    }


def test_probe_uses_same_production_v2_decision():
    snapshot = LiveSnapshot(
        symbol="TEST",
        current_price=103.0,
        session_open=102.0,
        session_high=103.2,
        session_low=101.5,
        opening_15m_high=103.2,
        opening_15m_low=101.5,
        return_from_open_pct=(103.0 / 102.0 - 1.0) * 100.0,
        opening_15m_volume=1_000_000,
        recent_opening_volume_median=900_000,
        volume_ratio=1.11,
        bar_count=15,
        last_bar_time="2026-08-14T09:44:00-04:00",
    )
    row = Observation(
        source="final_fusion",
        symbol="TEST",
        plan_date="2026-08-13",
        session_date="2026-08-14",
        created_at="2026-08-13T18:00:00-04:00",
        snapshot=snapshot,
        packet=_packet(),
        close_return_pct=1.0,
        return_60m_pct=0.5,
        mfe_pct=1.5,
        mae_pct=-0.2,
        stop_hit=False,
        target1_hit=False,
        opening_range_position=(103.0 - 101.5) / (103.2 - 101.5),
    )
    direct = classify_confirmation_v2(
        row.packet,
        row.snapshot,
        evaluated_at=datetime(2026, 8, 14, 9, 45, tzinfo=NY),
    )
    probe = evaluate_production_v2([row])
    assert direct.status == "BUY_NOW"
    assert probe["buy_count"] == 1
    assert probe["status_counts"] == {direct.status: 1}
