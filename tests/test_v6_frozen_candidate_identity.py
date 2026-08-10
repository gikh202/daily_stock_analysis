from __future__ import annotations

from copy import deepcopy

import src.v6_daily.research_governance as governance


def test_frozen_forward_candidates_match_current_definition() -> None:
    watch = governance._forward_watch({"minimum_samples": 3}, [])

    assert watch["definitions_match_freeze"] is True
    assert watch["ready_for_manual_review"] == 0
    assert [
        (item["variant"], item["horizon_days"])
        for item in watch["candidates"]
    ] == list(governance.FROZEN_ALPHA_CANDIDATES)
    for item in watch["candidates"]:
        assert item["definition_matches_freeze"] is True
        assert item["frozen_definition"] == item["current_definition"]
        assert item["status"] == "waiting_for_outcomes"


def test_profile_identity_drift_invalidates_forward_evidence(monkeypatch) -> None:
    original = governance.shadow_profiles

    def drifted_profiles(instrument_type: str):
        profiles = deepcopy(original(instrument_type))
        weights = profiles["momentum_focus"][10]
        weights["momentum"] = float(weights["momentum"]) + 0.000001
        return profiles

    monkeypatch.setattr(governance, "shadow_profiles", drifted_profiles)
    watch = governance._forward_watch({"minimum_samples": 3}, [])
    momentum = next(
        item
        for item in watch["candidates"]
        if item["variant"] == "momentum_focus" and item["horizon_days"] == 10
    )

    assert watch["definitions_match_freeze"] is False
    assert momentum["definition_matches_freeze"] is False
    assert momentum["frozen_definition"]["profile_identities"] != momentum["current_definition"]["profile_identities"]
    assert momentum["status"] == "definition_drifted"
    assert watch["ready_for_manual_review"] == 0
