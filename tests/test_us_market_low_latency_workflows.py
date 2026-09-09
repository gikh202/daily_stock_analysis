from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DAILY = ROOT / ".github" / "workflows" / "00-daily-analysis.yml"
CLOSE_FLASH = ROOT / ".github" / "workflows" / "00a-us-close-flash.yml"
OPEN = ROOT / ".github" / "workflows" / "01-us-open-confirmation.yml"
V6 = ROOT / ".github" / "workflows" / "03-v6-daily.yml"


def test_deep_close_schedule_has_no_artificial_delay() -> None:
    text = DAILY.read_text(encoding="utf-8")
    assert "cron: '5 20 * * 1-5'" in text
    assert "cron: '5 21 * * 1-5'" in text
    assert "Gate true post-close window" in text
    assert "America/New_York" in text
    assert "sleep $((RANDOM" not in text
    assert "30 22 * * 1-5" not in text


def test_close_flash_is_separate_and_lightweight() -> None:
    text = CLOSE_FLASH.read_text(encoding="utf-8")
    assert "cron: '0 20 * * 1-5'" in text
    assert "cron: '0 21 * * 1-5'" in text
    assert "requirements-realtime.txt" in text
    assert "requirements.txt" not in text
    assert "run_us_close_flash.py" in text
    assert "is_session" in text


def test_open_path_is_lightweight_and_dense_at_open() -> None:
    text = OPEN.read_text(encoding="utf-8")
    assert "cron: '30,35,40,45 13 * * 1-5'" in text
    assert "cron: '0,30,35,40,45 14 * * 1-5'" in text
    assert "requirements-realtime.txt" in text
    assert "pip install -r requirements.txt" not in text


def test_v6_ignores_inactive_dst_candidate_without_analysis_artifact() -> None:
    text = V6.read_text(encoding="utf-8")
    assert "id: gate" in text
    assert "listWorkflowRunArtifacts" in text
    assert "startsWith('analysis-reports-')" in text
    assert "needs: upstream-gate" in text
    assert "needs.upstream-gate.outputs.should_run == 'true'" in text


def test_open_cron_candidates_respect_github_five_minute_floor() -> None:
    text = OPEN.read_text(encoding="utf-8")
    for marker in (
        "30,35,40,45 13 * * 1-5",
        "0,30,35,40,45 14 * * 1-5",
    ):
        minute_field = marker.split()[0]
        minutes = sorted(int(value) for value in minute_field.split(","))
        gaps = [b - a for a, b in zip(minutes, minutes[1:])]
        assert all(gap >= 5 for gap in gaps)


def test_open_workflow_deployment_can_smoke_trigger_live_gate() -> None:
    text = OPEN.read_text(encoding="utf-8")
    assert "push:" in text
    assert "branches: [main]" in text
    assert ".github/workflows/01-us-open-confirmation.yml" in text
    assert '"$HM" -lt 930' in text
    assert '"$HM" -ge 1600' in text
