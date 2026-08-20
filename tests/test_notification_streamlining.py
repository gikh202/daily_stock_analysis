from pathlib import Path
import subprocess
import sys

import sitecustomize


ROOT = Path(__file__).resolve().parents[1]
OPEN = ROOT / ".github" / "workflows" / "01-us-open-confirmation.yml"
SMOKE = ROOT / ".github" / "workflows" / "02-live-provider-smoke.yml"
WATCHDOG = ROOT / ".github" / "workflows" / "01-us-open-schedule-watchdog.yml"
SAFE_RUNNER = ROOT / "scripts" / "run_us_open_confirmation_safe.py"


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
    assert "us-open-confirmation-${{ steps.gate.outputs.ny_date }}" in text


def test_safe_runner_supports_actions_direct_script_invocation() -> None:
    result = subprocess.run(
        [sys.executable, str(SAFE_RUNNER), "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Fail-safe U.S. open +15m confirmation" in result.stdout


def test_safe_runner_has_quote_outage_fallback() -> None:
    text = SAFE_RUNNER.read_text(encoding="utf-8")
    assert "all live U.S. session quotes unavailable" in text
    assert "当前不要下单" in text
    assert "us-open-confirmation-v2-safe-fallback" in text
