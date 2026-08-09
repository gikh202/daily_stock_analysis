from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from scripts.run_v6_daily import (
    _canonicalize_provisional,
    _is_current_external_context_safe,
)
from src.v6_daily.engine import V6DailyEngine


def _signal(history_id: int, created_at: str):
    base = V6DailyEngine().from_analysis_record(
        {
            "id": history_id,
            "query_id": f"q-{history_id}",
            "code": "MSFT",
            "created_at": created_at,
            "context_snapshot": (
                '{"trend_analysis":{"signal_score":70,"current_price":100},'
                '"prediction_context":{"horizons":{"20d":{"target_return_pct":4},'
                '"60d":{"target_return_pct":7}},"realized_vol_20d_pct":18},'
                '"market_regime":{"regime":"neutral"},'
                '"effective_daily_bar_date":"2026-08-07"}'
            ),
            "raw_result": '{"success":true,"model_used":"test"}',
        }
    )
    assert base is not None
    return replace(base, effective_trade_date="2026-08-07")


def test_canonicalize_same_trade_date_keeps_latest_analysis_record() -> None:
    first = _signal(10, "2026-08-09 08:00:00")
    latest = _signal(11, "2026-08-09 12:00:00")
    rows, duplicates = _canonicalize_provisional(
        [({"id": 10}, first), ({"id": 11}, latest)]
    )
    assert duplicates == 1
    assert len(rows) == 1
    assert rows[0][1].analysis_history_id == 11


def test_current_external_context_requires_recent_analysis_record_too() -> None:
    recent_trade = (date.today() - timedelta(days=2)).isoformat()
    current_analysis = date.today().isoformat() + " 08:00:00"
    stale_analysis = (date.today() - timedelta(days=7)).isoformat() + " 08:00:00"

    assert _is_current_external_context_safe(recent_trade, current_analysis) is True
    assert _is_current_external_context_safe(recent_trade, stale_analysis) is False
