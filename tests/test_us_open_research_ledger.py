from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from scripts.capture_us_open_research import reconstruct_snapshot_from_frame
from scripts.us_open_research_ledger import (
    compute_outcome,
    connect,
    record_signal,
    settle_pending,
    summary,
)

NY = ZoneInfo("America/New_York")


def _packet(stop=98.0, target=104.0):
    return {
        "identity": {
            "symbol": "TEST",
            "effective_trade_date": "2026-08-13",
        },
        "assessment": {
            "verdict": "conditional_buy",
            "worth_buying": True,
        },
        "execution": {
            "entry_zone": [99.0, 101.0],
            "stop_loss": stop,
            "targets": [target],
            "max_position_pct": 0.2,
            "has_active_plan": True,
        },
    }


def _decision(status="BUY_NOW"):
    return {
        "symbol": "TEST",
        "action": status,
        "current_price": 100.0,
        "source_last_bar_time": "2026-08-14T09:44:00-04:00",
        "return_from_open_pct": 0.4,
        "volume_ratio": 1.1,
        "better_entry_score": 0.7,
        "better_entry_probability": 0.7,
        "expected_better_price": 99.5,
    }


def _snapshot():
    return {
        "symbol": "TEST",
        "current_price": 100.0,
        "session_open": 99.6,
        "session_high": 100.2,
        "session_low": 99.4,
        "opening_15m_high": 100.2,
        "opening_15m_low": 99.4,
        "return_from_open_pct": 0.4,
        "opening_15m_volume": 15000,
        "recent_opening_volume_median": 14000,
        "volume_ratio": 1.1,
        "bar_count": 15,
        "last_bar_time": "2026-08-14T09:44:00-04:00",
    }


def _frame(rows):
    index = pd.DatetimeIndex(
        [pd.Timestamp(ts, tz="America/New_York") for ts, *_ in rows]
    )
    return pd.DataFrame(
        {
            "Open": [item[1] for item in rows],
            "High": [item[2] for item in rows],
            "Low": [item[3] for item in rows],
            "Close": [item[4] for item in rows],
            "Volume": [item[5] for item in rows],
        },
        index=index,
    )


def test_record_signal_is_idempotent(tmp_path):
    db = tmp_path / "research.db"
    kwargs = dict(
        packet=_packet(),
        snapshot=_snapshot(),
        decision=_decision(),
        evaluated_at=datetime(2026, 8, 14, 9, 45, tzinfo=NY),
        policy_version="us-open-confirmation-v2",
        source_run_id="123",
    )
    assert record_signal(db, **kwargs) is True
    assert record_signal(db, **kwargs) is False
    assert summary(db)["signals"] == 1


def test_compute_outcome_uses_first_touch_not_daily_extremes():
    row = {
        "signal_bar_time": "2026-08-14T09:44:00-04:00",
        "signal_price": 100.0,
        "packet_json": json.dumps(_packet(stop=98.0, target=104.0)),
    }
    frame = _frame(
        [
            ("2026-08-14 09:44", 100.0, 100.1, 99.9, 100.0, 1000),
            ("2026-08-14 09:45", 100.0, 104.2, 99.8, 104.0, 1200),
            ("2026-08-14 09:46", 104.0, 104.1, 97.5, 98.0, 1500),
            ("2026-08-14 16:00", 101.0, 101.2, 100.8, 101.0, 1000),
        ]
    )
    outcome = compute_outcome(row, frame)
    assert outcome is not None
    assert outcome["target1_hit"] is True
    assert outcome["stop_hit"] is False
    assert outcome["first_touch"] == "target1"
    assert outcome["modeled_exit_return_pct"] == pytest.approx(4.0)


def test_compute_outcome_marks_same_bar_stop_target_ambiguous():
    row = {
        "signal_bar_time": "2026-08-14T09:44:00-04:00",
        "signal_price": 100.0,
        "packet_json": json.dumps(_packet(stop=98.0, target=104.0)),
    }
    frame = _frame(
        [
            ("2026-08-14 09:44", 100.0, 100.1, 99.9, 100.0, 1000),
            ("2026-08-14 09:45", 100.0, 104.2, 97.8, 101.0, 2000),
            ("2026-08-14 16:00", 101.0, 101.1, 100.9, 101.0, 1000),
        ]
    )
    outcome = compute_outcome(row, frame)
    assert outcome is not None
    assert outcome["stop_hit"] is True
    assert outcome["target1_hit"] is True
    assert outcome["first_touch"] == "ambiguous_stop_target_same_bar"
    assert outcome["modeled_exit_return_pct"] is None


def test_settle_pending_updates_persisted_signal(tmp_path):
    db = tmp_path / "research.db"
    record_signal(
        db,
        packet=_packet(stop=98.0, target=104.0),
        snapshot=_snapshot(),
        decision=_decision(),
        evaluated_at=datetime(2026, 8, 14, 9, 45, tzinfo=NY),
        policy_version="us-open-confirmation-v2",
        source_run_id="123",
    )
    frame = _frame(
        [
            ("2026-08-14 09:44", 100.0, 100.1, 99.9, 100.0, 1000),
            ("2026-08-14 09:45", 100.0, 101.0, 99.5, 100.5, 1200),
            ("2026-08-14 10:44", 100.5, 101.5, 100.0, 101.0, 900),
            ("2026-08-14 16:00", 102.0, 102.2, 101.8, 102.0, 1000),
        ]
    )

    result = settle_pending(
        db,
        as_of_date=date(2026, 8, 15),
        history_fetcher=lambda symbol, session_date: frame,
    )
    assert result == {"settled": 1, "failed": 0, "pending_scanned": 1}
    payload = summary(db)
    assert payload["settled"] == 1
    assert payload["settled_buy_now"] == 1
    assert payload["buy_avg_close_return_pct"] == pytest.approx(2.0)


def test_reconstruct_snapshot_does_not_peek_after_signal_bar():
    rows = []
    for minute in range(15):
        hhmm = f"09:{30 + minute:02d}"
        rows.append((f"2026-08-14 {hhmm}", 100.0, 100.2, 99.8, 100.0, 1000))
    rows.append(("2026-08-14 09:45", 100.0, 120.0, 80.0, 110.0, 999999))
    frame = _frame(rows)
    snapshot = reconstruct_snapshot_from_frame(
        symbol="TEST",
        decision=_decision(),
        frame=frame,
    )
    assert snapshot["last_bar_time"] == "2026-08-14T09:44:00-04:00"
    assert snapshot["session_high"] == pytest.approx(100.2)
    assert snapshot["session_low"] == pytest.approx(99.8)
    assert snapshot["bar_count"] == 15


def test_record_signal_keeps_multiple_intraday_observations(tmp_path):
    db = tmp_path / "research.db"
    first_snapshot = _snapshot()
    first_decision = _decision("WAIT_BETTER_ENTRY")
    second_snapshot = dict(
        first_snapshot,
        current_price=99.8,
        last_bar_time="2026-08-14T10:00:00-04:00",
    )
    second_decision = dict(
        first_decision,
        current_price=99.8,
        source_last_bar_time="2026-08-14T10:00:00-04:00",
    )
    common = dict(
        packet=_packet(),
        evaluated_at=datetime(2026, 8, 14, 10, 1, tzinfo=NY),
        policy_version="us-open-timing-v7.1",
        source_run_id="123",
    )
    assert record_signal(
        db, snapshot=first_snapshot, decision=first_decision, **common
    ) is True
    assert record_signal(
        db, snapshot=first_snapshot, decision=first_decision, **common
    ) is False
    assert record_signal(
        db, snapshot=second_snapshot, decision=second_decision, **common
    ) is True
    assert summary(db)["signals"] == 2


def test_wait_better_entry_outcome_learns_reference_price_hit():
    row = {
        "signal_bar_time": "2026-08-14T09:44:00-04:00",
        "signal_price": 100.0,
        "packet_json": json.dumps(_packet(stop=98.0, target=104.0)),
        "decision_json": json.dumps(_decision("WAIT_BETTER_ENTRY")),
    }
    frame = _frame(
        [
            ("2026-08-14 09:44", 100.0, 100.1, 99.9, 100.0, 1000),
            ("2026-08-14 09:50", 100.0, 100.2, 99.4, 99.7, 1200),
            ("2026-08-14 16:00", 101.0, 101.2, 100.8, 101.0, 1000),
        ]
    )
    outcome = compute_outcome(row, frame)
    assert outcome is not None
    assert outcome["better_entry_hit"] is True
    assert outcome["best_future_improvement_pct"] == pytest.approx(0.6)
    assert outcome["minutes_to_reference_better_price"] == pytest.approx(6.0)
