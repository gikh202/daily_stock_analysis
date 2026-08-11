from __future__ import annotations

from typing import Any, Dict, Mapping

from .legacy_physical_retirement import (
    LEGACY_PHYSICAL_RETIREMENT_SCHEMA_VERSION,
    LEGACY_RETIREMENT_RECEIPT_VERSION,
)
from .production_gate_v5 import assert_stage11_production_gate


PRODUCTION_GATE_V7_SCHEMA_VERSION = "v6-production-physical-retirement-gate-v7"
EXPECTED_STAGE13_ENTRYPOINT = "v6-stage13-physical-legacy-retirement-v1"
_ALLOWED_RETIREMENT_STATUSES = {"retired", "already_retired"}


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def evaluate_stage13_production_gate(run_payload: Mapping[str, Any]) -> Dict[str, Any]:
    reasons: list[str] = []
    try:
        base = assert_stage11_production_gate(run_payload)
    except RuntimeError as exc:
        base = {"production_ready": False, "reasons": [str(exc)]}
        reasons.append(str(exc))

    entrypoint = str(run_payload.get("stage13_entrypoint") or "").strip()
    retirement = _mapping(run_payload.get("physical_retirement"))
    post = _mapping(retirement.get("post_production"))
    status = str(retirement.get("status") or "").strip()

    if entrypoint != EXPECTED_STAGE13_ENTRYPOINT:
        reasons.append(
            f"stage13_entrypoint={entrypoint!r} is not {EXPECTED_STAGE13_ENTRYPOINT!r}"
        )
    if retirement.get("schema_version") != LEGACY_PHYSICAL_RETIREMENT_SCHEMA_VERSION:
        reasons.append("physical retirement schema version mismatch")
    if retirement.get("receipt_version") != LEGACY_RETIREMENT_RECEIPT_VERSION:
        reasons.append("physical retirement receipt version mismatch")
    if status not in _ALLOWED_RETIREMENT_STATUSES:
        reasons.append(f"physical retirement status={status!r} is not terminal")
    if retirement.get("legacy_tables_absent") is not True:
        reasons.append("retirement receipt does not certify legacy table absence")

    if post.get("legacy_tables_absent") is not True:
        reasons.append(
            f"post-production legacy tables remain: {post.get('legacy_tables_present')!r}"
        )
    if post.get("legacy_tables_present") not in ([], ()):
        reasons.append(
            f"post-production legacy table list is not empty: {post.get('legacy_tables_present')!r}"
        )
    if str(post.get("quick_check") or "").strip().lower() != "ok":
        reasons.append(f"post-production quick_check={post.get('quick_check')!r} is not ok")
    if _int(post.get("foreign_key_errors")) != 0:
        reasons.append(
            f"post-production foreign_key_errors={post.get('foreign_key_errors')!r}"
        )

    if status == "retired":
        if retirement.get("archive_verified") is not True:
            reasons.append("physical retirement did not preserve a verified archive")
        if retirement.get("restore_verified") is not True:
            reasons.append("physical retirement did not pass isolated restore rehearsal")
        if retirement.get("gate_v6_passed") is not True:
            reasons.append("physical retirement did not pass Gate v6 before DROP")
        if retirement.get("action") != "transactional_drop":
            reasons.append("physical retirement was not transactional")
        dropped = retirement.get("dropped_tables") or []
        if not dropped:
            reasons.append("retired status recorded no dropped legacy tables")

    policy = _mapping(retirement.get("policy"))
    if policy.get("archive_before_drop") is not True:
        reasons.append("retirement policy does not require archive before DROP")
    if policy.get("verified_restore_before_drop") is not True:
        reasons.append("retirement policy does not require verified restore before DROP")
    if policy.get("transactional_drop") is not True:
        reasons.append("retirement policy does not require transactional DROP")
    if policy.get("automatic_reverse_projection") is not False:
        reasons.append("reverse legacy projection is unexpectedly enabled")

    production_ready = bool(base.get("production_ready")) and not reasons
    return {
        "schema_version": PRODUCTION_GATE_V7_SCHEMA_VERSION,
        "status": "ready" if production_ready else "blocked",
        "production_ready": production_ready,
        "cache_persist_allowed": production_ready,
        "notification_allowed": production_ready,
        "base_gate_schema_version": base.get("schema_version"),
        "base_gate_ready": bool(base.get("production_ready")),
        "stage13_entrypoint": entrypoint,
        "physical_retirement_status": status,
        "legacy_tables_absent": post.get("legacy_tables_absent") is True,
        "archive_verified": retirement.get("archive_verified"),
        "restore_verified": retirement.get("restore_verified"),
        "gate_v6_passed": retirement.get("gate_v6_passed"),
        "reasons": reasons,
    }


def assert_stage13_production_gate(run_payload: Mapping[str, Any]) -> Dict[str, Any]:
    gate = evaluate_stage13_production_gate(run_payload)
    if not gate["production_ready"]:
        raise RuntimeError(
            "V6 Stage 13 production gate blocked: "
            + "; ".join(gate.get("reasons") or ["unknown gate failure"])
        )
    return gate
