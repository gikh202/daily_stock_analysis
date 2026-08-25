from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github/workflows/02-us-open-backtest.yml"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_missing_mature_minute_history_is_recorded_not_promoted() -> None:
    text = _workflow_text()

    assert "no causal observations with historical minute bars" in text
    assert '"status": "insufficient_data"' in text
    assert '"eligible": False' in text
    assert "record insufficient_data" in text


def test_real_backtest_failures_still_fail_the_job() -> None:
    text = _workflow_text()

    assert 'echo "::error::US-open causal backtest failed with exit code $STATUS"' in text
    assert 'exit "$STATUS"' in text
