from __future__ import annotations

import copy
from typing import Any, Dict, Mapping

from .production_read_store import PRODUCTION_READ_SCHEMA_VERSION, ProductionV6ReadStore
from .production_report import build_daily_payload


PRODUCTION_CUTOVER_SCHEMA_VERSION = "v6-production-self-cutover-v1"
PRODUCTION_PRIMARY_SOURCE = "normalized_v6_tables"
PRODUCTION_SELF_GUARD_MODE = "normalized_primary_self_consistency_guard"


def _canonical_trade_plan(value: Any, *, decision: Any = None) -> Dict[str, Any]:
    plan = dict(value) if isinstance(value, Mapping) else {}
    invalidation = plan.get("invalidation")
    if invalidation is None:
        invalidation = plan.get("invalidations")
    return {
        "action": plan.get("action") or decision,
        "entry_zone": plan.get("entry_zone"),
        "stop_loss": plan.get("stop_loss"),
        "targets": list(plan.get("targets") or []),
        "max_position_pct": plan.get("max_position_pct"),
        "risk_reward": plan.get("risk_reward"),
        "confirmations": list(plan.get("confirmations") or []),
        "invalidation": list(invalidation or []),
    }


def _canonical_board_item(value: Mapping[str, Any]) -> Dict[str, Any]:
    decision = value.get("decision")
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
        "decision": decision,
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
        "trade_plan": _canonical_trade_plan(value.get("trade_plan"), decision=decision),
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
            for key in sorted(set(a) | set(b), key=str):
                if key not in a:
                    result.append(f"{current}.{key}: missing in reference")
                elif key not in b:
                    result.append(f"{current}.{key}: missing in regenerated")
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


def cutover_daily_payload(
    normalized_reference_payload: Mapping[str, Any],
    *,
    db_path: str,
    active_engine_version: str,
    run_stats: Dict[str, Any],
    min_samples: int,
    public_context: Mapping[str, Any] | None = None,
    requested_source: str = "normalized",
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    requested = str(requested_source or "normalized").strip().lower()
    if requested != "normalized":
        raise ValueError(
            "Stage 11 production read path is normalized-only; "
            f"unsupported V6 read source: {requested_source!r}"
        )

    store = ProductionV6ReadStore(
        db_path,
        active_engine_version=active_engine_version,
    )
    quick = store.quick_check()
    if quick.strip().lower() != "ok":
        raise RuntimeError(f"normalized read quick_check failed: {quick}")
    foreign_key_errors = store.foreign_key_errors()
    if foreign_key_errors:
        raise RuntimeError(
            "normalized read foreign_key_check failed: "
            + repr(foreign_key_errors[:3])
        )

    regenerated = build_daily_payload(
        store,
        run_stats=run_stats,
        min_samples=max(3, int(min_samples)),
        public_context=dict(public_context or {}),
    )
    reference_business = canonical_business_payload(normalized_reference_payload)
    regenerated_business = canonical_business_payload(regenerated)
    differences = _diff(reference_business, regenerated_business)
    if differences:
        raise RuntimeError(
            "normalized production self-consistency failed: "
            + "; ".join(differences)
        )

    selected = copy.deepcopy(dict(regenerated))
    selected["generated_at"] = normalized_reference_payload.get("generated_at")
    metadata = {
        "schema_version": PRODUCTION_CUTOVER_SCHEMA_VERSION,
        "normalized_read_schema_version": PRODUCTION_READ_SCHEMA_VERSION,
        "requested_source": "normalized",
        "selected_source": PRODUCTION_PRIMARY_SOURCE,
        "mode": PRODUCTION_SELF_GUARD_MODE,
        "parity": "exact",
        "differences": [],
        "reference_source": PRODUCTION_PRIMARY_SOURCE,
        "legacy_reference_used": False,
        "legacy_consumer_count": 0,
        "fail_closed": True,
        "quick_check": quick,
        "foreign_key_errors": 0,
        "reference_board_size": len(reference_business.get("board") or []),
        "normalized_board_size": len(regenerated_business.get("board") or []),
        "reference_signal_count": int(
            (reference_business.get("counts") or {}).get("signals") or 0
        ),
        "normalized_signal_count": int(
            (regenerated_business.get("counts") or {}).get("signals") or 0
        ),
        "reference_outcome_count": int(
            (reference_business.get("counts") or {}).get("outcomes") or 0
        ),
        "normalized_outcome_count": int(
            (regenerated_business.get("counts") or {}).get("outcomes") or 0
        ),
    }
    selected["read_cutover"] = metadata
    return selected, dict(metadata)
