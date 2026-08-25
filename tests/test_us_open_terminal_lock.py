from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import scripts.us_open_terminal_lock as lock_module
from scripts.us_open_terminal_lock import (
    _filtered_payload,
    run_with_terminal_lock,
    terminal_locks,
)


NY = ZoneInfo("America/New_York")


def _decision(
    symbol: str,
    *,
    action: str,
    terminal: bool,
    current_price: float,
) -> dict:
    return {
        "symbol": symbol,
        "action": action,
        "label": action,
        "reason": f"{symbol} reason",
        "current_price": current_price,
        "entry_low": current_price * 0.98,
        "entry_high": current_price * 1.01,
        "stop_loss": current_price * 0.95,
        "targets": [current_price * 1.05],
        "starter_position_pct": 10.0 if action == "BUY_NOW" else 0.0,
        "max_position_pct": 20.0,
        "return_from_open_pct": 0.3,
        "volume_ratio": 1.0,
        "probability_up_1d": 0.60,
        "probability_up_5d": 0.62,
        "probability_up_20d": 0.58,
        "expected_return_5d_pct": 1.2,
        "expected_alpha_5d_pct": 0.4,
        "forecast_confidence": 0.7,
        "better_entry_score": 0.35,
        "better_entry_probability": 0.35,
        "expected_better_price": current_price * 0.995,
        "expected_improvement_pct": 0.5,
        "recheck_minutes": 0 if terminal else 15,
        "terminal": terminal,
        "source_trade_date": "2026-08-24",
        "source_last_bar_time": "2026-08-25T09:35:00-04:00",
        "execution_status": "FULL_APPROVED",
        "conditional_entry_price": None,
        "conditional_entry_reason": None,
    }


def _previous(now: datetime, *, source_run_id: str = "close-123") -> dict:
    return {
        "generated_at": now.isoformat(),
        "source_run_id": source_run_id,
        "state_signature": "previous",
        "decisions": [
            _decision("MSFT", action="BUY_NOW", terminal=True, current_price=500.0),
            _decision(
                "GOOGL",
                action="WAIT_BETTER_ENTRY",
                terminal=False,
                current_price=300.0,
            ),
        ],
    }


def _payload(path: Path) -> Path:
    payload = {
        "final_decisions": {
            "packets": [
                {"identity": {"symbol": "MSFT"}},
                {"identity": {"symbol": "GOOGL"}},
            ]
        },
        "board": [
            {"code": "MSFT", "context_features": {}},
            {"code": "GOOGL", "context_features": {}},
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_terminal_locks_require_same_session_and_source_and_skip_waits() -> None:
    now = datetime(2026, 8, 25, 10, 0, tzinfo=NY)
    previous = _previous(now)

    locks = terminal_locks(
        previous,
        source_run_id="close-123",
        evaluated_at=now,
    )
    assert set(locks) == {"MSFT"}
    assert locks["MSFT"]["action"] == "BUY_NOW"

    assert terminal_locks(
        previous,
        source_run_id="different-close-run",
        evaluated_at=now,
    ) == {}
    assert terminal_locks(
        previous,
        source_run_id="close-123",
        evaluated_at=now,
        force_recompute=True,
    ) == {}

    previous_day = dict(previous)
    previous_day["generated_at"] = (now - timedelta(days=1)).isoformat()
    assert terminal_locks(
        previous_day,
        source_run_id="close-123",
        evaluated_at=now,
    ) == {}


def test_filtered_payload_removes_only_locked_symbols(tmp_path: Path) -> None:
    path = _payload(tmp_path / "v6.json")
    filtered, order = _filtered_payload(path, locked_symbols={"MSFT"})

    assert order == ["MSFT", "GOOGL"]
    packets = filtered["final_decisions"]["packets"]
    assert [item["identity"]["symbol"] for item in packets] == ["GOOGL"]
    assert [item["code"] for item in filtered["board"]] == ["GOOGL"]


def test_partial_terminal_lock_recalculates_only_waiting_symbol(
    tmp_path: Path, monkeypatch
) -> None:
    now = datetime.now(NY)
    previous_path = tmp_path / "previous.json"
    previous_path.write_text(json.dumps(_previous(now)), encoding="utf-8")
    payload_path = _payload(tmp_path / "v6.json")
    output_dir = tmp_path / "out"
    observed_symbols: list[str] = []

    def fake_run_timing(**kwargs):
        filtered = json.loads(Path(kwargs["v6_payload_path"]).read_text(encoding="utf-8"))
        symbols = [
            item["identity"]["symbol"]
            for item in filtered["final_decisions"]["packets"]
        ]
        observed_symbols.extend(symbols)
        assert symbols == ["GOOGL"]
        out = Path(kwargs["output_dir"])
        out.mkdir(parents=True, exist_ok=True)
        fresh = _decision(
            "GOOGL",
            action="WAIT_CONFIRMATION",
            terminal=False,
            current_price=299.0,
        )
        (out / "us_open_confirmation_latest.json").write_text(
            json.dumps({"decisions": [fresh]}), encoding="utf-8"
        )
        return {"live_success": 1}

    monkeypatch.setattr(lock_module, "run_timing", fake_run_timing)

    result = run_with_terminal_lock(
        v6_payload_path=payload_path,
        output_dir=output_dir,
        source_run_id="close-123",
        notify=False,
        previous_state_path=previous_path,
        force_notify=False,
        allow_all_unavailable=False,
    )

    final = json.loads(
        (output_dir / "us_open_confirmation_latest.json").read_text(encoding="utf-8")
    )
    assert observed_symbols == ["GOOGL"]
    assert [item["symbol"] for item in final["decisions"]] == ["MSFT", "GOOGL"]
    msft = final["decisions"][0]
    googl = final["decisions"][1]
    assert msft["action"] == "BUY_NOW"
    assert msft["current_price"] == 500.0
    assert msft["terminal_locked"] is True
    assert googl["action"] == "WAIT_CONFIRMATION"
    assert result["terminal_locked"] == 1
    assert result["follow_up_needed"] is True


def test_all_terminal_symbols_skip_live_timing_entirely(tmp_path: Path, monkeypatch) -> None:
    now = datetime.now(NY)
    previous = {
        "generated_at": now.isoformat(),
        "source_run_id": "close-123",
        "state_signature": "previous",
        "decisions": [
            _decision("MSFT", action="BUY_NOW", terminal=True, current_price=500.0),
            _decision("GOOGL", action="NO_BUY", terminal=True, current_price=300.0),
        ],
    }
    previous_path = tmp_path / "previous.json"
    previous_path.write_text(json.dumps(previous), encoding="utf-8")
    payload_path = _payload(tmp_path / "v6.json")

    def must_not_run(**kwargs):
        raise AssertionError("live timing must not run when every symbol is terminal")

    monkeypatch.setattr(lock_module, "run_timing", must_not_run)

    result = run_with_terminal_lock(
        v6_payload_path=payload_path,
        output_dir=tmp_path / "out",
        source_run_id="close-123",
        notify=False,
        previous_state_path=previous_path,
        force_notify=False,
        allow_all_unavailable=False,
    )

    assert result["terminal_locked"] == 2
    assert result["live_success"] == 0
    assert result["follow_up_needed"] is False


def test_force_resend_bypasses_terminal_lock(tmp_path: Path, monkeypatch) -> None:
    now = datetime.now(NY)
    previous_path = tmp_path / "previous.json"
    previous_path.write_text(json.dumps(_previous(now)), encoding="utf-8")
    payload_path = _payload(tmp_path / "v6.json")
    called = {"value": False}

    def fake_run_timing(**kwargs):
        called["value"] = True
        assert kwargs["force_notify"] is True
        assert Path(kwargs["v6_payload_path"]) == payload_path
        return {"forced": True}

    monkeypatch.setattr(lock_module, "run_timing", fake_run_timing)

    result = run_with_terminal_lock(
        v6_payload_path=payload_path,
        output_dir=tmp_path / "out",
        source_run_id="close-123",
        notify=False,
        previous_state_path=previous_path,
        force_notify=True,
        allow_all_unavailable=False,
    )

    assert called["value"] is True
    assert result == {"forced": True}
