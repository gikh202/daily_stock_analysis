# -*- coding: utf-8 -*-
"""LiteLLM generation backend wrapper."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, Optional, Tuple

from json_repair import repair_json

from src.llm.generation_backend import (
    GenerationBackend,
    GenerationCapabilities,
    GenerationResult,
)


logger = logging.getLogger(__name__)

LiteLLMCallable = Callable[..., Tuple[str, str, Dict[str, Any]]]

_STRUCTURED_TEMPERATURE_CAP = 0.2
_STRUCTURED_REPAIR_TEMPERATURE_CAP = 0.1
_MAX_REPAIR_RESPONSE_CHARS = 32_000


def _provider_from_model(model: str) -> str:
    if not model:
        return ""
    if "/" in model:
        return model.split("/", 1)[0]
    return "openai"


def _clamp_temperature(
    generation_config: Dict[str, Any],
    *,
    cap: float,
) -> Dict[str, Any]:
    """Return a request copy with temperature capped for strict JSON work."""
    updated = dict(generation_config or {})
    raw_temperature = updated.get("temperature")
    if raw_temperature in (None, ""):
        updated["temperature"] = cap
        return updated
    try:
        updated["temperature"] = min(float(raw_temperature), cap)
    except (TypeError, ValueError):
        updated["temperature"] = cap
    return updated


def _build_validator_repair_prompt(
    original_prompt: str,
    previous_response: str,
    failure: BaseException,
) -> str:
    """Build one narrow repair request after every configured model failed validation.

    The repair is deliberately validation-focused rather than a blind retry. It
    preserves the original evidence context, tells the model to remove unsupported
    recent-news claims, and explicitly allows the forecast to be reconsidered if
    those unsupported claims influenced it.
    """
    previous = (previous_response or "").strip()
    if len(previous) > _MAX_REPAIR_RESPONSE_CHARS:
        previous = previous[-_MAX_REPAIR_RESPONSE_CHARS:]
    failure_text = str(failure or "").strip()
    if len(failure_text) > 800:
        failure_text = failure_text[:800]

    return "\n\n".join(
        [
            original_prompt,
            """### Structured-output validation repair

The previous response failed the repository's deterministic JSON / forecast / evidence validator.
Rebuild the COMPLETE JSON object from the original request and return JSON only (no Markdown fence and no explanation).

Repair rules:
1. Keep every required top-level and dashboard field that the original request asks for.
2. `forecast.horizons` must contain 5d, 10d, and 20d. Each direction must be bullish/neutral/bearish; `up_probability` must be numeric in [0,100]; expected-return numbers must be finite and internally consistent with direction.
3. For `dashboard.intelligence.latest_news`, `risk_alerts`, and `positive_catalysts`, use ONLY recent-news claims whose date and Evidence ID are present in the ORIGINAL evidence context. Do not invent dates, Evidence IDs, events, or sources. If no verified recent evidence supports a claim, use a neutral no-evidence statement/empty list instead.
4. If an unsupported recent-news claim influenced the previous forecast, RE-EVALUATE the affected forecast using only the verified evidence from the original request. Do not preserve a contaminated forecast merely for consistency with the previous answer.
5. Preserve valid analysis grounded in the original market/technical/fundamental inputs; do not introduce new facts.
6. Return one complete JSON object only.""",
            f"Validator failure summary: {failure_text or 'structured validation failed'}",
            "### Previous invalid response\n" + previous,
        ]
    )


def _has_complete_json_container(value: str) -> bool:
    """Return whether the response already contains a structurally closed JSON root.

    ``json_repair`` can legitimately fix commas, quoting and wrapper noise, but it
    can also close an object that was truncated by the model/token budget. Local
    repair must never turn that kind of incomplete analysis into an apparent
    success, even when a caller supplies a permissive/minimal validator.
    """
    text = str(value or "")
    object_pos = text.find("{")
    array_pos = text.find("[")
    starts = [pos for pos in (object_pos, array_pos) if pos >= 0]
    if not starts:
        return False

    stack: list[str] = []
    in_string = False
    escaped = False

    for char in text[min(starts):]:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
            continue
        if char in "{[":
            stack.append(char)
            continue
        if char not in "}]":
            continue
        if not stack:
            return False
        expected = "{" if char == "}" else "["
        if stack[-1] != expected:
            return False
        stack.pop()
        if not stack:
            return True

    return False


def _try_local_json_repair(
    previous_response: str,
    response_validator: Callable[[str], None],
) -> Optional[str]:
    """Repair non-truncation syntax drift locally, then run the strict validator.

    A candidate is eligible only when the original response already contains a
    structurally closed JSON root. This prevents ``json_repair`` from silently
    completing truncated model output. Evidence/forecast validation is also never
    weakened: the caller's original validator must pass unchanged.
    """
    previous = str(previous_response or "").strip()
    if not previous or not _has_complete_json_container(previous):
        return None
    try:
        repaired = repair_json(previous, return_objects=False)
        if not isinstance(repaired, str):
            repaired = json.dumps(repaired, ensure_ascii=False)
        repaired = repaired.strip()
        if not repaired or repaired == previous:
            return None
        response_validator(repaired)
        return repaired
    except Exception:
        return None


class LiteLLMGenerationBackend(GenerationBackend):
    """Thin adapter around the existing LiteLLM analyzer call path."""

    backend_id = "litellm"
    capabilities = GenerationCapabilities(
        supports_json=True,
        supports_tools=True,
        supports_stream=True,
        supports_vision=False,
        supports_health_check=False,
        supports_smoke_test=False,
    )

    def __init__(self, completion_callable: LiteLLMCallable):
        self._completion_callable = completion_callable

    def generate(
        self,
        prompt: str,
        generation_config: Dict[str, Any],
        *,
        system_prompt: Optional[str] = None,
        stream: bool = False,
        stream_progress_callback: Optional[Callable[[int], None]] = None,
        response_validator: Optional[Callable[[str], None]] = None,
        audit_context: Optional[Dict[str, Any]] = None,
    ) -> GenerationResult:
        # Free-form generation keeps the caller's temperature. Strict structured
        # analysis gets a lower cap to reduce enum/type/evidence drift without
        # affecting market-review prose or other unconstrained generation.
        effective_generation_config = dict(generation_config or {})
        if response_validator is not None:
            effective_generation_config = _clamp_temperature(
                effective_generation_config,
                cap=_STRUCTURED_TEMPERATURE_CAP,
            )

        validator_repair_used = False
        validator_local_json_repair_used = False
        try:
            text, model, usage = self._completion_callable(
                prompt,
                effective_generation_config,
                system_prompt=system_prompt,
                stream=stream,
                stream_progress_callback=stream_progress_callback,
                response_validator=response_validator,
                audit_context=audit_context,
            )
        except Exception as exc:
            previous_response = getattr(exc, "last_response_text", None)
            # `_call_litellm_impl` exposes `last_response_text` only when model(s)
            # actually returned content but none passed the supplied validator.
            # Transport/config/empty-output failures therefore do not enter this
            # repair path and keep their existing fallback/error semantics.
            if response_validator is None or not previous_response:
                raise

            local_repair = _try_local_json_repair(
                str(previous_response),
                response_validator,
            )
            if local_repair is not None:
                validator_repair_used = True
                validator_local_json_repair_used = True
                text = local_repair
                model = str(getattr(exc, "last_model", "") or "")
                usage = dict(getattr(exc, "last_usage", {}) or {})
                logger.info(
                    "[LLM结构修复] local JSON repair passed the original validator; "
                    "skipping additional model repair request"
                )
            else:
                validator_repair_used = True
                repair_prompt = _build_validator_repair_prompt(
                    prompt,
                    str(previous_response),
                    exc,
                )
                repair_generation_config = _clamp_temperature(
                    effective_generation_config,
                    cap=_STRUCTURED_REPAIR_TEMPERATURE_CAP,
                )
                logger.warning(
                    "[LLM结构修复] configured model chain returned content but failed "
                    "validation; retrying once with evidence-aware repair prompt"
                )
                try:
                    text, model, usage = self._completion_callable(
                        repair_prompt,
                        repair_generation_config,
                        system_prompt=system_prompt,
                        # Repair JSON non-streaming to avoid partial-object failure modes.
                        stream=False,
                        stream_progress_callback=stream_progress_callback,
                        response_validator=response_validator,
                        audit_context=audit_context,
                    )
                except Exception as repair_exc:
                    # Preserve the pre-existing analyzer safety contract. Before this
                    # repair layer, an all-model validation failure carrying a usable
                    # `last_response_text` was handled by deterministic post-gates in
                    # `analyze()`. A failed optional repair must never replace that
                    # recoverable response with a newer transport/empty-output error.
                    logger.warning(
                        "[LLM结构修复] repair attempt failed; preserving original "
                        "validated-response fallback for deterministic post-gates: %s",
                        type(repair_exc).__name__,
                    )
                    raise exc

        provider = str((usage or {}).get("provider") or _provider_from_model(model))
        diagnostics: Dict[str, Any] = {}
        if validator_repair_used:
            diagnostics["validator_repair_used"] = True
        if validator_local_json_repair_used:
            diagnostics["validator_local_json_repair_used"] = True
        return GenerationResult(
            text=text,
            model=model,
            provider=provider,
            backend=self.backend_id,
            usage=usage or {},
            raw=None,
            diagnostics=diagnostics,
        )
