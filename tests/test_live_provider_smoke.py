from __future__ import annotations

from scripts.live_provider_smoke import (
    ProbeResult,
    _redact,
    _required_failures,
    _run_probe,
    _split_secret_values,
)


def test_split_secret_values_supports_commas_newlines_and_dedupes() -> None:
    assert _split_secret_values("a,b\nc", "b\nd") == ["a", "b", "c", "d"]


def test_redact_hides_configured_provider_secrets(monkeypatch) -> None:
    monkeypatch.setenv("SERPAPI_API_KEY", "secret-key-123")

    text = _redact("request failed for key=secret-key-123")

    assert "secret-key-123" not in text
    assert "***" in text


def test_run_probe_records_failure_without_raising() -> None:
    result = _run_probe(
        "provider",
        required=True,
        probe=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert result.status == "fail"
    assert result.required is True
    assert "RuntimeError: boom" in result.detail


def test_required_failures_ignore_skipped_and_optional_failures() -> None:
    results = [
        ProbeResult("required-pass", "pass", True, 1, "ok"),
        ProbeResult("optional-fail", "fail", False, 1, "bad"),
        ProbeResult("skip", "skipped", False, 0, "not configured"),
        ProbeResult("required-fail", "fail", True, 1, "bad"),
    ]

    failures = _required_failures(results)

    assert [item.name for item in failures] == ["required-fail"]
