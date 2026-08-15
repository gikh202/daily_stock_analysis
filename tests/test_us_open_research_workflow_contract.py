from __future__ import annotations

from pathlib import Path


def test_research_workflow_runs_after_open_confirmation_and_never_notifies():
    text = Path('.github/workflows/02b-us-open-research-ledger.yml').read_text(encoding='utf-8')
    assert "workflows: ['美股开盘执行确认']" in text
    assert "github.event.workflow_run.conclusion == 'success'" in text
    assert 'capture_us_open_research.py' in text
    assert 'us_open_research.db' in text
    assert '--notify' not in text
    assert 'EMAIL_PASSWORD' not in text
    assert 'NotificationService' not in text
