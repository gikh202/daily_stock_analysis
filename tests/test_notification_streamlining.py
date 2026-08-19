from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DAILY = ROOT / ".github" / "workflows" / "00-daily-analysis.yml"
OPEN = ROOT / ".github" / "workflows" / "01-us-open-confirmation.yml"
SMOKE = ROOT / ".github" / "workflows" / "02-live-provider-smoke.yml"
WATCHDOG = ROOT / ".github" / "workflows" / "01-us-open-schedule-watchdog.yml"
SAFE_RUNNER = ROOT / "scripts" / "run_us_open_confirmation_safe.py"


def test_daily_analysis_never_sends_user_notifications() -> None:
    text = DAILY.read_text(encoding="utf-8")
    assert "python main.py --no-notify" in text
    assert "STOCK_LIST 未配置" in text


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


def test_safe_runner_has_quote_outage_fallback() -> None:
    text = SAFE_RUNNER.read_text(encoding="utf-8")
    assert "all live U.S. session quotes unavailable" in text
    assert "当前不要下单" in text
    assert "us-open-confirmation-v2-safe-fallback" in text
