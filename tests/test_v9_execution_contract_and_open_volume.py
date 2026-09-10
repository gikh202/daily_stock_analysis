from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from scripts.run_us_open_confirmation import (
    LiveSnapshot,
    _matched_opening_volume_stats,
)
from scripts.run_us_open_confirmation_v2 import (
    _execution_contract,
    classify_confirmation_v2,
)

NY = ZoneInfo("America/New_York")


def _snapshot() -> LiveSnapshot:
    return LiveSnapshot(
        symbol="TEST",
        current_price=100.0,
        session_open=99.5,
        session_high=101.0,
        session_low=99.0,
        opening_15m_high=101.0,
        opening_15m_low=99.0,
        return_from_open_pct=0.5,
        opening_15m_volume=300.0,
        recent_opening_volume_median=200.0,
        volume_ratio=1.5,
        bar_count=3,
        last_bar_time="2026-09-09T09:32:00-04:00",
        opening_volume_elapsed_bars=3,
    )


def _packet(status: str) -> dict:
    return {
        "identity": {"symbol": "TEST", "effective_trade_date": "2026-09-08"},
        "assessment": {
            "execution_status": status,
            "reject_reason_code": "RISK_SCORE_GATE" if status == "HARD_REJECTED" else None,
            "verdict": "watch",
            "worth_buying": None,
            "execution_authorized": False,
        },
        "execution": {
            "entry_zone": [99.0, 101.0],
            "stop_loss": 95.0,
            "targets": [108.0],
            "max_position_pct": 0.15,
            "has_active_plan": True,
            "confirmations": [],
        },
    }


def test_hard_rejected_close_contract_cannot_be_upgraded_by_open_strength() -> None:
    packet = _packet("HARD_REJECTED")
    contract = _execution_contract(packet)
    assert contract["hard_block"] is True

    decision = classify_confirmation_v2(
        packet,
        _snapshot(),
        evaluated_at=datetime(2026, 9, 9, 9, 32, tzinfo=NY),
    )
    assert decision.status == "NO_BUY"
    assert decision.starter_position_pct == 0.0
    assert "HARD_REJECTED" in decision.reason


def test_legacy_rejected_is_migrated_to_unresolved_not_hard_block() -> None:
    contract = _execution_contract(_packet("REJECTED"))
    assert contract["status"] == "UNRESOLVED"
    assert contract["hard_block"] is False


def test_opening_volume_ratio_matches_same_elapsed_bar_count() -> None:
    frames = []
    for session_date, volumes in (
        ("2026-09-04", [60, 70, 70, 999, 999]),
        ("2026-09-08", [70, 60, 70, 999, 999]),
        ("2026-09-09", [100, 100, 100]),
    ):
        index = pd.date_range(
            f"{session_date} 09:30",
            periods=len(volumes),
            freq="1min",
            tz=NY,
        )
        frames.append(
            pd.DataFrame(
                {
                    "Open": [100.0] * len(volumes),
                    "High": [101.0] * len(volumes),
                    "Low": [99.0] * len(volumes),
                    "Close": [100.0] * len(volumes),
                    "Volume": volumes,
                },
                index=index,
            )
        )
    frame = pd.concat(frames)
    current, historical, ratio, elapsed = _matched_opening_volume_stats(
        frame, datetime(2026, 9, 9, tzinfo=NY).date()
    )
    assert elapsed == 3
    assert current == 300.0
    assert historical == 200.0
    assert ratio == 1.5
