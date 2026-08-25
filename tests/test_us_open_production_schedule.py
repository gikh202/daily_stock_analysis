from pathlib import Path

from scripts.capture_us_open_research import _has_observable_wait_window
from src.forecasting.timing import (
    PRODUCTION_RECHECK_MINUTES_SINCE_OPEN,
    IntradayTimingModel,
    production_recheck_delay,
)


ROOT_DIR = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT_DIR / ".github/workflows/01-us-open-confirmation.yml"


def test_production_recheck_delay_matches_nominal_et_candidates() -> None:
    assert PRODUCTION_RECHECK_MINUTES_SINCE_OPEN == (5, 15, 30, 60, 90, 150)
    assert production_recheck_delay(0) == 5       # 09:30 -> 09:35
    assert production_recheck_delay(4) == 1       # delayed 09:30 runner -> 09:35
    assert production_recheck_delay(5) == 10      # 09:35 -> 09:45
    assert production_recheck_delay(15) == 15     # 09:45 -> 10:00
    assert production_recheck_delay(30) == 30     # 10:00 -> 10:30
    assert production_recheck_delay(60) == 30     # 10:30 -> 11:00
    assert production_recheck_delay(90) == 60     # 11:00 -> 12:00
    assert production_recheck_delay(149) == 1
    assert production_recheck_delay(150) == 0     # 12:00 is the last candidate


def test_workflow_crons_stay_aligned_with_timing_contract() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    for cron in (
        "30 13 * * 1-5", "35 13 * * 1-5", "45 13 * * 1-5",
        "0 14 * * 1-5", "30 14 * * 1-5", "0 15 * * 1-5", "0 16 * * 1-5",
        "35 14 * * 1-5", "45 14 * * 1-5", "30 15 * * 1-5", "0 17 * * 1-5",
    ):
        assert f"cron: '{cron}'" in text
    assert "09:30 / 09:35 / 09:45 / 10:00 / 10:30 / 11:00 / 12:00 ET" in text


def _wait_pullback(minutes_since_open: int):
    return IntradayTimingModel().assess(
        base_status="WAIT_PULLBACK",
        current_price=105.0,
        entry_low=100.0,
        entry_high=103.0,
        stop_loss=96.0,
        session_low=101.0,
        session_high=105.2,
        session_vwap=103.5,
        last_5m_return_pct=-0.20,
        intraday_volatility_pct=1.2,
        minutes_since_open=minutes_since_open,
        probability_up_1d=0.54,
        probability_up_5d=0.61,
    )


def test_wait_payload_uses_real_next_production_interval() -> None:
    decision = _wait_pullback(30)
    assert decision.action == "WAIT_BETTER_ENTRY"
    assert decision.recheck_minutes == 30
    assert decision.terminal is False
    assert "next automated production recheck is in 30 minutes" in decision.rationale


def test_final_candidate_closes_automatic_wait_window() -> None:
    decision = _wait_pullback(150)
    assert decision.action == "WAIT_BETTER_ENTRY"
    assert decision.recheck_minutes == 0
    assert decision.terminal is True
    assert "automated production recheck window is over for today" in decision.rationale


def test_terminal_wait_without_future_recheck_is_not_ledger_observation() -> None:
    assert _has_observable_wait_window(
        {
            "action": "WAIT_BETTER_ENTRY",
            "terminal": True,
            "expected_wait_minutes": 0,
            "recheck_minutes": 0,
        }
    ) is False
    assert _has_observable_wait_window(
        {
            "action": "WAIT_BETTER_ENTRY",
            "terminal": False,
            "expected_wait_minutes": 30,
            "recheck_minutes": 30,
        }
    ) is True
