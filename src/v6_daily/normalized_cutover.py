from __future__ import annotations

import copy
from typing import Any, Dict, Mapping

from .normalized_read_store import NORMALIZED_READ_SCHEMA_VERSION, NormalizedV6ReadStore
from .read_cutover import canonical_business_payload
from .report import build_daily_payload


NORMALIZED_CUTOVER_SCHEMA_VERSION = "v6-normalized-self-cutover-v1"
NORMALIZED_PRIMARY_SOURCE = "normalized_v6_tables"
NORMALIZED_SELF_GUARD_MODE = "normalized_primary_self_consistency_guard"


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
    """Rebuild production business payload from canonical normalized facts only.

    The first payload is also produced by ``NormalizedV6ReadStore`` in the Stage 9
    production entrypoint. Rebuilding it here gives an independent same-source
    determinism/integrity check without consulting legacy tables. A request for
    legacy fallback is intentionally rejected on the production path.
    """
    requested = str(requested_source or "normalized").strip().lower()
    if requested != "normalized":
        raise ValueError(
            "Stage 9 production read path is normalized-only; "
            f"unsupported V6 read source: {requested_source!r}"
        )

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

    regenerated = build_daily_payload(
        normalized_store,
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
        "schema_version": NORMALIZED_CUTOVER_SCHEMA_VERSION,
        "normalized_read_schema_version": NORMALIZED_READ_SCHEMA_VERSION,
        "requested_source": "normalized",
        "selected_source": NORMALIZED_PRIMARY_SOURCE,
        "mode": NORMALIZED_SELF_GUARD_MODE,
        "parity": "exact",
        "differences": [],
        "reference_source": NORMALIZED_PRIMARY_SOURCE,
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
