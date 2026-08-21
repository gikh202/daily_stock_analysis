from datetime import datetime
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

import sitecustomize
from scripts.run_us_open_confirmation_safe import _near_open_retry_seconds
from scripts.run_us_open_timing import _semantic_price_state, _should_notify


ROOT = Path(__file__).resolve().parents[1]
OPEN = ROOT / ".github" / "workflows" / "01-us-open-confirmation.yml"
SMOKE = ROOT / ".github" / "workflows" / "02-live-provider-smoke.yml"
WATCHDOG = ROOT / ".github" / "workflows" / "01-us-open-schedule-watchdog.yml"
SAFE_RUNNER = ROOT / "scripts" / "run_us_open_confirmation_safe.py"
TIMING_RUNNER = ROOT / "scripts" / "run_us_open_timing.py"
NY = ZoneInfo("America/New_York")


def test_daily_analysis_forces_no_notify_without_changing_workflow_contract() -> None:
    argv = ["main.py"]
    env = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_WORKFLOW": "每日股票分析",
    }
    assert sitecustomize.apply_daily_workflow_notification_guard(argv, env) is True
    assert argv == ["main.py", "--no-notify"]


def test_daily_notification_guard_is_narrow() -> None:
    argv = ["main.py"]
    assert sitecustomize.apply_daily_workflow_notification_guard(
        argv,
        {"GITHUB_ACTIONS": "true", "GITHUB_WORKFLOW": "CI"},
    ) is False
    assert argv == ["main.py"]


def test_unconfigured_legacy_stock_fallback_is_blocked() -> None:
    argv = ["main.py", "--no-notify"]
    base = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_WORKFLOW": "每日股票分析",
        "STOCK_LIST_CONFIG": "",
        "STOCK_LIST": "600519",
    }
    assert sitecustomize.uses_unconfigured_daily_fallback_stock(argv, base) is True

    configured = dict(base, STOCK_LIST_CONFIG="600519")
    assert sitecustomize.uses_unconfigured_daily_fallback_stock(argv, configured) is False


def test_provider_smoke_is_manual_and_paid_llm_is_opt_in() -> None:
    text = SMOKE.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "schedule:" not in text
    assert "default: false" in text
    assert "LIVE_SMOKE_LLM_ENABLED" in text


def test_redundant_open_watchdog_is_removed() -> None:
    assert not WATCHDOG.exists()


def test_open_confirmation_is_email_only_and_uses_safe_runner() -> None:
    text = OPEN.read_text(encoding="utf-8")
    assert "NOTIFICATION_REPORT_CHANNELS: email" in text
    assert "run_us_open_confirmation_safe.py" in text
    assert "us-open-terminal-${{ steps.gate.outputs.ny_date }}" in text
    assert "us-open-state-${{ steps.gate.outputs.ny_date }}-" in text


def test_safe_runner_supports_actions_direct_script_invocation() -> None:
    result = subprocess.run(
        [sys.executable, str(SAFE_RUNNER), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "actual execution clock" in result.stdout


def test_timing_policy_import_keeps_notification_stack_lazy() -> None:
    text = TIMING_RUNNER.read_text(encoding="utf-8")
    prefix, notify_block = text.split("def _notify", 1)
    assert "from src.notification import NotificationService" not in prefix
    assert "from src.notification import NotificationService" in notify_block


def test_near_open_retry_waits_only_until_minimum_opening_window() -> None:
    assert _near_open_retry_seconds(datetime(2026, 8, 20, 9, 31, tzinfo=NY)) == 245.0
    assert _near_open_retry_seconds(datetime(2026, 8, 20, 9, 35, tzinfo=NY)) == 0.0
    assert _near_open_retry_seconds(datetime(2026, 8, 20, 10, 5, tzinfo=NY)) == 0.0


def test_safe_runner_has_quote_outage_fallback_and_nonterminal_state() -> None:
    safe = SAFE_RUNNER.read_text(encoding="utf-8")
    timing = TIMING_RUNNER.read_text(encoding="utf-8")
    assert "all live U.S. session quotes unavailable" in safe
    assert "allow_all_unavailable=True" in safe
    assert 'action="DATA_UNAVAILABLE"' in timing
    assert "terminal=False" in timing
    assert "follow_up_needed" in timing


def test_unchanged_timing_state_is_not_resent_just_because_an_hour_passed() -> None:
    previous = {
        "state_signature": "same",
        "generated_at": "2026-08-20T09:35:00-04:00",
    }
    assert _should_notify(
        previous,
        signature="same",
        generated_at=datetime(2026, 8, 20, 12, 0, tzinfo=NY),
        force=False,
    ) is False
    assert _should_notify(
        previous,
        signature="changed",
        generated_at=datetime(2026, 8, 20, 9, 45, tzinfo=NY),
        force=False,
    ) is True


def test_semantic_price_state_ignores_ten_cent_noise_inside_entry_zone() -> None:
    assert _semantic_price_state(100.00, 99.0, 101.0, 95.0) == "inside_entry"
    assert _semantic_price_state(100.10, 99.0, 101.0, 95.0) == "inside_entry"
    assert _semantic_price_state(101.70, 99.0, 101.0, 95.0) == "above_entry_0_5_1pct"


def test_open_timing_report_labels_better_entry_metric_as_uncalibrated_score() -> None:
    text = TIMING_RUNNER.read_text(encoding="utf-8")
    assert "更好买点启发式评分（未校准）" in text
    compact = "".join(text.split())
    assert '"semantics":"heuristic_score"' in compact
