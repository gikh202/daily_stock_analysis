from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.calibrate_us_open_timing import calibrate
from scripts.promote_us_open_policy import build_promotion
from src.forecasting.timing import IntradayTimingModel
from src.forecasting.timing_policy import TimingPolicy, load_timing_policy


def _synthetic_db(path: Path) -> Path:
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE us_open_signals (
            id INTEGER PRIMARY KEY,
            session_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            signal_price REAL NOT NULL,
            decision_status TEXT NOT NULL,
            decision_json TEXT NOT NULL,
            settled_at TEXT,
            close_return_pct REAL,
            mfe_pct REAL,
            better_entry_hit INTEGER
        )
        """
    )
    start = date(2026, 6, 1)
    symbols = ("MSFT", "GOOGL", "VOO", "QQQM")
    for index in range(120):
        day = start + timedelta(days=index // 3)
        payload = {
            "better_entry_score": 0.60,
            "better_entry_probability": 0.60,
            "expected_improvement_pct": 0.25,
            "expected_better_price": 99.50,
        }
        conn.execute(
            """
            INSERT INTO us_open_signals(
                id, session_date, symbol, signal_price, decision_status,
                decision_json, settled_at, close_return_pct, mfe_pct, better_entry_hit
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                index + 1,
                day.isoformat(),
                symbols[index % len(symbols)],
                100.0,
                "BUY_NOW",
                json.dumps(payload),
                f"{day.isoformat()}T20:00:00+00:00",
                0.0,
                0.20,
                1,
            ),
        )
    conn.commit()
    conn.close()
    return path


def _registry(active: TimingPolicy) -> dict:
    return {
        "schema_version": "us-open-policy-registry-v1",
        "active": active.version,
        "previous": None,
        "challenger": None,
        "promotion_mode": "pull_request",
        "score_model_version": active.score_model_version,
        "policies": {active.version: {"status": "ACTIVE"}},
    }


def test_v73_baseline_policy_matches_v72_wait_contract() -> None:
    policy = load_timing_policy("config/us_open_timing_policy.json")
    assert policy.version == "v7.3-policy-001"
    assert policy.wait_threshold == pytest.approx(0.62)
    assert policy.min_expected_improvement_pct == pytest.approx(0.20)
    decision = IntradayTimingModel(policy).assess(
        base_status="BUY_NOW",
        current_price=105.0,
        entry_low=100.0,
        entry_high=105.0,
        stop_loss=96.0,
        session_low=101.0,
        session_high=105.2,
        session_vwap=103.5,
        last_5m_return_pct=-0.25,
        intraday_volatility_pct=1.2,
        minutes_since_open=25,
        probability_up_1d=0.54,
        probability_up_5d=0.61,
    )
    assert decision.action == "WAIT_BETTER_ENTRY"
    assert decision.better_entry_probability >= policy.wait_threshold


def test_policy_rejects_unsafe_threshold() -> None:
    with pytest.raises(ValueError, match="wait_threshold"):
        replace(TimingPolicy(), wait_threshold=0.95).validate()


def test_calibration_builds_eligible_challenger_on_oos_alpha(tmp_path: Path) -> None:
    db = _synthetic_db(tmp_path / "research.db")
    active = TimingPolicy().validate()
    report, challenger = calibrate(
        db,
        active_policy=active,
        generated_at=datetime(2026, 8, 22, 3, 30, tzinfo=timezone.utc),
    )
    assert challenger is not None
    assert challenger.wait_threshold < active.wait_threshold
    assert report["promotion"]["eligible"] is True
    assert report["challenger_oos"]["avg_return_pct"] > report["champion_oos"]["avg_return_pct"]
    assert report["promotion"]["bootstrap_95_lower_timing_alpha_pct"] >= 0.0


def test_promotion_accepts_only_tunable_changes(tmp_path: Path) -> None:
    db = _synthetic_db(tmp_path / "research.db")
    active = TimingPolicy().validate()
    report, challenger = calibrate(
        db,
        active_policy=active,
        generated_at=datetime(2026, 8, 22, 3, 30, tzinfo=timezone.utc),
    )
    assert challenger is not None
    promoted, registry = build_promotion(active, challenger, report, _registry(active))
    assert promoted.version == challenger.version
    assert registry["active"] == challenger.version
    assert registry["previous"] == active.version
    assert registry["promotion_mode"] == "pull_request"

    unsafe = replace(challenger, base_score=challenger.base_score + 0.01)
    with pytest.raises(ValueError, match="non-tunable"):
        build_promotion(active, unsafe, report, _registry(active))


def test_ineligible_calibration_cannot_promote() -> None:
    active = TimingPolicy().validate()
    challenger = active.with_tunables(
        wait_threshold=0.58,
        min_expected_improvement_pct=0.20,
        version="v7.3-challenger-test",
    )
    report = {
        "version": "us-open-policy-calibration-v1",
        "active_policy_version": active.version,
        "challenger_policy_version": challenger.version,
        "score_model_version": active.score_model_version,
        "promotion": {"eligible": False},
    }
    with pytest.raises(ValueError, match="not eligible"):
        build_promotion(active, challenger, report, _registry(active))
