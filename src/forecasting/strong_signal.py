from __future__ import annotations

from typing import Any

from .history import ForecastHistory


STRONG_UP = 0.58
STRONG_DOWN = 0.42


def strong_signal_skill(
    history: ForecastHistory,
    *,
    as_of_date: str | None,
    horizon_days: int,
    regime: str | None,
    symbol: str | None,
    instrument_type: str | None,
    probability_key: str = "probability_up",
) -> dict[str, Any]:
    """Measure OOS direction skill only where the historical model was decisive.

    It deliberately reuses ForecastHistory's strict-as-of rows and hierarchical
    scope selection. A signal is strong when the historical probability was at
    least 58% up or at most 42% up. No future outcome can enter the sample.
    """
    rows = history._rows(  # noqa: SLF001 - intentional shared strict-as-of contract
        as_of_date=as_of_date,
        horizon_days=horizon_days,
    )
    selected, scope, regime_samples = history._hierarchical_scope(  # noqa: SLF001
        rows,
        symbol=symbol,
        instrument_type=instrument_type,
        regime=regime,
    )
    observations: list[tuple[float, int]] = []
    for row in selected:
        probability = history._row_probability(row, probability_key)  # noqa: SLF001
        try:
            realized = float(row["return_pct"])
        except (TypeError, ValueError):
            continue
        if probability is None or not (
            probability >= STRONG_UP or probability <= STRONG_DOWN
        ):
            continue
        observations.append((float(probability), int(realized > 0.0)))

    n = len(observations)
    if n == 0:
        return {
            "samples": 0,
            "hit_rate": None,
            "positive_rate": None,
            "majority_baseline_accuracy": None,
            "skill": None,
            "scope": scope,
            "regime_samples": regime_samples,
            "thresholds": [STRONG_DOWN, STRONG_UP],
        }
    positives = sum(y for _, y in observations)
    positive_rate = positives / n
    hits = sum(int((p >= 0.50) == bool(y)) for p, y in observations)
    hit_rate = hits / n
    baseline = max(positive_rate, 1.0 - positive_rate)
    return {
        "samples": n,
        "hit_rate": hit_rate,
        "positive_rate": positive_rate,
        "majority_baseline_accuracy": baseline,
        "skill": hit_rate - baseline,
        "scope": scope,
        "regime_samples": regime_samples,
        "thresholds": [STRONG_DOWN, STRONG_UP],
    }


def scope_reliability_haircut(scope: str | None) -> float:
    """Require progressively more evidence as calibration falls back in scope."""
    return {
        "symbol_regime": 1.00,
        "symbol": 1.00,
        "instrument_regime": 0.85,
        "instrument_type": 0.85,
        "regime": 0.70,
        "global": 0.60,
    }.get(str(scope or "global").strip().lower(), 0.60)
