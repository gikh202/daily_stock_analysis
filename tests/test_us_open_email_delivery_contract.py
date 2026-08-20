from pathlib import Path

from scripts.send_us_open_failure_alert import _split_receivers, build_message


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "01-us-open-confirmation.yml"


def test_open_confirmation_requires_email_channel_success() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "NOTIFICATION_REPORT_CHANNELS: email" in text
    assert "id: confirm" in text
    assert "--notify" in text


def test_open_confirmation_starts_at_market_open_and_uses_runtime_gate() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    for marker in (
        "cron: '30 13 * * 1-5'",
        "cron: '30 14 * * 1-5'",
        '"$HM" -ge 930',
        '"$HM" -le 1600',
        "scheduled_runtime_confirmation",
        "发送开盘实时执行确认",
    ):
        assert marker in text
    assert "cron: '45 13 * * 1-5'" in text
    assert "cron: '45 14 * * 1-5'" in text


def test_open_confirmation_has_once_per_day_failure_email() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    for marker in (
        "id: failure-alert-cache",
        "us-open-confirmation-failure-alert-${{ steps.gate.outputs.ny_date }}",
        "id: failure-alert",
        "failure()",
        "scripts/send_us_open_failure_alert.py",
        "steps.failure-alert.outcome == 'success'",
    ):
        assert marker in text


def test_failure_email_message_contains_safe_action_and_run_url() -> None:
    subject, body = build_message(
        session_date="2026-08-17",
        run_url="https://github.com/example/repo/actions/runs/123",
        prior_v6_outcome="success",
        plan_outcome="success",
        confirmation_outcome="failure",
    )
    assert "美股开盘确认失败" in subject
    assert "运行开盘实时确认或发送邮件失败" in body
    assert "当前不要依据缺失/陈旧数据建立新仓" in body
    assert "后续开盘补偿候选会继续按计划尝试" in body
    assert "actions/runs/123" in body


def test_receiver_parser_supports_commas_and_semicolons() -> None:
    assert _split_receivers("a@example.com;b@example.com, c@example.com") == [
        "a@example.com",
        "b@example.com",
        "c@example.com",
    ]
