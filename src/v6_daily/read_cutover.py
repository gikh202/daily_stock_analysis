from __future__ import annotations

import copy
from typing import Any, Dict, Mapping, Sequence

from .normalized_read_store import NORMALIZED_READ_SCHEMA_VERSION, NormalizedV6ReadStore
from .report import build_daily_payload


READ_CUTOVER_SCHEMA_VERSION = "v6-read-cutover-v1"
NORMALIZED_PRIMARY_SOURCE = "normalized_v6_tables"
LEGACY_FALLBACK_SOURCE = "legacy_v6_signals_outcomes"


def _canonical_trade_plan(value: Any) -> Dict[str, Any]:
    plan = dict(value) if isinstance(value, Mapping) else {}
    invalidation = plan.get("invalidation")
    if invalidation is None:
        invalidation = plan.get("invalidations")
    return {
        "action": plan.get("action"),
        "entry_zone": plan.get("entry_zone"),
        "stop_loss": plan.get("stop_loss"),
        "targets": list(plan.get("targets") or []),
        "max_position_pct": plan.get("max_position_pct"),
        "risk_reward": plan.get("risk_reward"),
        "confirmations": list(plan.get("confirmations") or []),
        "invalidation": list(invalidation or []),
    }


def _canonical_board_item(value: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "id": value.get("id"),
        "analysis_history_id": value.get("analysis_history_id"),
        "query_id": value.get("query_id"),
        "code": value.get("code"),
        "analysis_created_at": value.get("analysis_created_at"),
        "v6_created_at": value.get("v6_created_at"),
        "engine_version": value.get("engine_version"),
        "direction": value.get("direction"),
        "forecast_score": value.get("forecast_score"),
        "decision": value.get("decision"),
        "quality_score": value.get("quality_score"),
        "opportunity_score": value.get("opportunity_score"),
        "risk_score": value.get("risk_score"),
        "evidence_coverage": value.get("evidence_coverage"),
        "baseline_price": value.get("baseline_price"),
        "market_regime": value.get("market_regime"),
        "market_breadth": value.get("market_breadth"),
        "llm_health": value.get("llm_health"),
        "instrument_type": value.get("instrument_type"),
        "effective_trade_date": value.get("effective_trade_date"),
        "features": value.get("features") or {},
        "trade_plan": _canonical_trade_plan(value.get("trade_plan")),
        "catalysts": list(value.get("catalysts") or []),
        "risks": list(value.get("risks") or []),
        "limitations": list(value.get("limitations") or []),
        "diagnostics": value.get("diagnostics") or {},
        "horizon_forecasts": value.get("horizon_forecasts") or {},
        "context_features": value.get("context_features") or {},
    }


def canonical_business_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "counts": dict(payload.get("counts") or {}),
        "market_pulse": dict(payload.get("market_pulse") or {}),
        "board": [
            _canonical_board_item(item)
            for item in (payload.get("board") or [])
            if isinstance(item, Mapping)
        ],
        "deltas": [
            dict(item)
            for item in (payload.get("deltas") or [])
            if isinstance(item, Mapping)
        ],
        "scoreboard": copy.deepcopy(payload.get("scoreboard") or {}),
        "public_context": copy.deepcopy(payload.get("public_context") or {}),
    }


def _diff(left: Any, right: Any, path: str = "$", *, limit: int = 12) -> list[str]:
    result: list[str] = []

    def walk(a: Any, b: Any, current: str) -> None:
        if len(result) >= limit:
            return
        if type(a) is not type(b):
            result.append(f"{current}: type {type(a).__name__} != {type(b).__name__}")
            return
        if isinstance(a, dict):
            keys = sorted(set(a) | set(b), key=str)
            for key in keys:
                if key not in a:
                    result.append(f"{current}.{key}: missing in legacy")
                elif key not in b:
                    result.append(f"{current}.{key}: missing in normalized")
                else:
                    walk(a[key], b[key], f"{current}.{key}")
                if len(result) >= limit:
                    return
            return
        if isinstance(a, list):
            if len(a) != len(b):
                result.append(f"{current}: len {len(a)} != {len(b)}")
                return
            for index, (x, y) in enumerate(zip(a, b)):
                walk(x, y, f"{current}[{index}]")
                if len(result) >= limit:
                    return
            return
        if a != b:
            result.append(f"{current}: {a!r} != {b!r}")

    walk(left, right, path)
    return result


def compare_daily_payloads(
    legacy_payload: Mapping[str, Any],
    normalized_payload: Mapping[str, Any],
) -> Dict[str, Any]:
    legacy = canonical_business_payload(legacy_payload)
    normalized = canonical_business_payload(normalized_payload)
    differences = _diff(legacy, normalized)
    return {
        "schema_version": READ_CUTOVER_SCHEMA_VERSION,
        "normalized_read_schema_version": NORMALIZED_READ_SCHEMA_VERSION,
        "parity": "exact" if not differences else "mismatch",
        "differences": differences,
        "legacy_board_size": len(legacy.get("board") or []),
        "normalized_board_size": len(normalized.get("board") or []),
        "legacy_signal_count": int((legacy.get("counts") or {}).get("signals") or 0),
        "normalized_signal_count": int((normalized.get("counts") or {}).get("signals") or 0),
        "legacy_outcome_count": int((legacy.get("counts") or {}).get("outcomes") or 0),
        "normalized_outcome_count": int((normalized.get("counts") or {}).get("outcomes") or 0),
    }


def cutover_daily_payload(
    legacy_payload: Mapping[str, Any],
    *,
    db_path: str,
    active_engine_version: str,
    run_stats: Dict[str, Any],
    min_samples: int,
    public_context: Mapping[str, Any] | None = None,
    requested_source: str = "normalized",
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    normalized_store = NormalizedV6ReadStore(
        db_path,
        active_engine_version=active_engine_version,
    )
    quick = normalized_store.quick_check()
    if quick.strip().lower() != "ok":
        raise RuntimeError(f"normalized read quick_check failed: {quick}")
    foreign_key_errors = normalized_store.foreign_key_errors()
    if foreign_key_errors:
        raise RuntimeError(
            "normalized read foreign_key_check failed: "
            + repr(foreign_key_errors[:3])
        )

    normalized_payload = build_daily_payload(
        normalized_store,
        run_stats=run_stats,
        min_samples=max(3, int(min_samples)),
        public_context=dict(public_context or {}),
    )
    parity = compare_daily_payloads(legacy_payload, normalized_payload)
    parity["quick_check"] = quick
    parity["foreign_key_errors"] = len(foreign_key_errors)

    requested = str(requested_source or "normalized").strip().lower()
    if requested not in {"normalized", "legacy"}:
        raise ValueError(f"unsupported V6 read source: {requested_source!r}")

    if requested == "normalized":
        if parity["parity"] != "exact":
            raise RuntimeError(
                "normalized production read parity failed: "
                + "; ".join(parity.get("differences") or ["unknown drift"])
            )
        selected = normalized_payload
        selected_source = NORMALIZED_PRIMARY_SOURCE
        mode = "normalized_primary_with_legacy_parity_guard"
    else:
        selected = copy.deepcopy(dict(legacy_payload))
        selected_source = LEGACY_FALLBACK_SOURCE
        mode = "manual_legacy_fallback"

    # Preserve the original run timestamp so cutover changes only the read source,
    # not report identity or notification dedup semantics.
    selected["generated_at"] = legacy_payload.get("generated_at")
    selected["read_cutover"] = {
        **parity,
        "requested_source": requested,
        "selected_source": selected_source,
        "mode": mode,
        "fail_closed": requested == "normalized",
    }
    return selected, dict(selected["read_cutover"])
