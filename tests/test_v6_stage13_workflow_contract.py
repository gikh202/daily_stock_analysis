from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "03-v6-daily.yml"


def test_stage13_workflow_preserves_zero_forbidden_import_count() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "int(import_guard.get('forbidden_import_count') or -1)" not in text
    assert "forbidden_import_count = import_guard.get('forbidden_import_count')" in text
    assert "or forbidden_import_count is None" in text
    assert "or int(forbidden_import_count) != 0" in text


def test_stage13_notification_remains_gated_after_successful_validation() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "f.write('valid=true\\n')" in text
    assert "f.write('production_ready=true\\n')" in text
    assert "Production Gate v7 通过后发送最终综合日报" in text
    assert "V6 final notification: PASS" in text
