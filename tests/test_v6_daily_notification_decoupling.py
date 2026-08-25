from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT_DIR / ".github/workflows/03-v6-daily.yml"


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_v6_delivery_failure_does_not_invalidate_valid_forecast() -> None:
    text = _workflow_text()

    assert "run_payload['notification'] = notification" in text
    assert "forecast remains valid for next-session open confirmation" in text
    assert "delivery failure does not invalidate" in text
    assert 'raise SystemExit(f"V6 final notification failed: {notification}")' not in text


def test_v6_notification_gate_failures_remain_hard_failures() -> None:
    text = _workflow_text()

    assert "raise SystemExit(f'production gate forbids notification: {gate}')" in text
    assert (
        "raise SystemExit('notification blocked: legacy fact tables are not physically retired')"
        in text
    )
