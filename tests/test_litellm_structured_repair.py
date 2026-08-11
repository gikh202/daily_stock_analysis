# -*- coding: utf-8 -*-
"""Regression tests for validator-aware LiteLLM structured-output recovery."""

import json

import pytest

from src.llm.generation_backend import GenerationError, GenerationErrorCode
from src.llm.litellm_backend import LiteLLMGenerationBackend


class _AllModelsValidationFailed(Exception):
    def __init__(self, previous: str):
        super().__init__("all models failed structured validation")
        self.last_response_text = previous


def test_structured_generation_caps_temperature_without_touching_free_form() -> None:
    calls = []

    def completion(prompt, generation_config, **kwargs):
        calls.append((prompt, dict(generation_config), kwargs))
        return "ok", "deepseek/deepseek-v4-flash", {"provider": "deepseek"}

    backend = LiteLLMGenerationBackend(completion)
    validator = lambda _text: None

    structured = backend.generate(
        "structured",
        {"temperature": 0.7, "max_tokens": 256},
        response_validator=validator,
    )
    free_form = backend.generate(
        "free",
        {"temperature": 0.7, "max_tokens": 256},
    )

    assert calls[0][1]["temperature"] == pytest.approx(0.2)
    assert calls[1][1]["temperature"] == pytest.approx(0.7)
    assert structured.diagnostics == {}
    assert free_form.diagnostics == {}


def test_structured_generation_preserves_lower_caller_temperature() -> None:
    seen = {}

    def completion(_prompt, generation_config, **_kwargs):
        seen.update(generation_config)
        return "ok", "deepseek/deepseek-v4-flash", {}

    backend = LiteLLMGenerationBackend(completion)
    backend.generate(
        "structured",
        {"temperature": 0.05},
        response_validator=lambda _text: None,
    )

    assert seen["temperature"] == pytest.approx(0.05)


def test_validator_failure_with_previous_response_gets_one_evidence_aware_repair() -> None:
    calls = []
    previous = '{"forecast":{"horizons":{}},"dashboard":{"intelligence":{"latest_news":"invented"}}}'

    def completion(prompt, generation_config, **kwargs):
        calls.append((prompt, dict(generation_config), kwargs))
        if len(calls) == 1:
            raise _AllModelsValidationFailed(previous)
        return (
            '{"forecast":{"horizons":{"5d":{},"10d":{},"20d":{}}}}',
            "deepseek/deepseek-v4-pro",
            {"provider": "deepseek", "total_tokens": 42},
        )

    def validator(text: str) -> None:
        payload = json.loads(text)
        horizons = payload.get("forecast", {}).get("horizons", {})
        if set(horizons) != {"5d", "10d", "20d"}:
            raise ValueError("news_evidence_contract_failed")

    backend = LiteLLMGenerationBackend(completion)
    result = backend.generate(
        "ORIGINAL REQUEST WITH [E01] date=2026-08-07",
        {"temperature": 0.7, "max_tokens": 8192},
        system_prompt="system",
        stream=True,
        response_validator=validator,
        audit_context={"call_type": "analysis"},
    )

    assert len(calls) == 2
    assert calls[0][1]["temperature"] == pytest.approx(0.2)
    assert calls[1][1]["temperature"] == pytest.approx(0.1)
    assert calls[1][2]["stream"] is False
    assert calls[1][2]["response_validator"] is validator
    assert calls[1][2]["system_prompt"] == "system"
    assert calls[1][2]["audit_context"] == {"call_type": "analysis"}
    assert "Structured-output validation repair" in calls[1][0]
    assert "use ONLY recent-news claims" in calls[1][0]
    assert "RE-EVALUATE" in calls[1][0]
    assert previous in calls[1][0]
    assert result.model == "deepseek/deepseek-v4-pro"
    assert result.provider == "deepseek"
    assert result.diagnostics == {"validator_repair_used": True}


def test_failed_repair_re_raises_original_validation_failure() -> None:
    previous = '{"sentiment_score": 66, "trend_prediction": "看多"}'
    original = _AllModelsValidationFailed(previous)
    calls = 0

    def completion(_prompt, _generation_config, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise original
        raise RuntimeError("repair transport failed")

    def validator(_text: str) -> None:
        raise ValueError("semantic validation still failed")

    backend = LiteLLMGenerationBackend(completion)

    with pytest.raises(_AllModelsValidationFailed) as exc_info:
        backend.generate(
            "structured",
            {"temperature": 0.7},
            response_validator=validator,
        )

    assert calls == 2
    assert exc_info.value is original
    assert exc_info.value.last_response_text == previous


def test_no_validator_repair_for_transport_style_failure_without_previous_response() -> None:
    def completion(_prompt, _generation_config, **_kwargs):
        raise RuntimeError("network down")

    backend = LiteLLMGenerationBackend(completion)

    with pytest.raises(RuntimeError, match="network down"):
        backend.generate(
            "structured",
            {"temperature": 0.7},
            response_validator=lambda _text: None,
        )


def test_generation_error_includes_safe_validation_reason_and_message() -> None:
    error = GenerationError(
        error_code=GenerationErrorCode.SCHEMA_VALIDATION_FAILED,
        stage="validation",
        retryable=True,
        fallbackable=True,
        backend="litellm",
        details={
            "reason": "news_evidence_contract_failed",
            "message": "recent-news fields missing date/fresh Evidence ID: latest_news",
        },
    )

    assert str(error) == (
        "schema_validation_failed at validation for backend litellm: "
        "news_evidence_contract_failed: recent-news fields missing date/fresh Evidence ID: latest_news"
    )
