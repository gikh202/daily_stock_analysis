from __future__ import annotations

import math
import os
import urllib.parse
from typing import Any, Dict, Iterable, Mapping, Optional

from . import free_sources as base


V9_EXTRA_FRED_SERIES = {
    "DFII10": "US 10Y TIPS real yield",
    "DTWEXBGS": "Trade Weighted U.S. Dollar Index: Broad",
    "WALCL": "Federal Reserve total assets",
}


def _finite(value: Any) -> Optional[float]:
    return base._finite(value)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return base._clamp(value, low, high)


def _series_value(series: Mapping[str, Any], series_id: str, field: str) -> Optional[float]:
    item = series.get(series_id)
    summary = item.get("summary") if isinstance(item, Mapping) else None
    return _finite(summary.get(field)) if isinstance(summary, Mapping) else None


def _real_yield_risk(series: Mapping[str, Any]) -> Optional[float]:
    level = _series_value(series, "DFII10", "value")
    change = _series_value(series, "DFII10", "change_20obs")
    if level is None and change is None:
        return None
    parts: list[tuple[float, float]] = []
    if level is not None:
        # Real yields above roughly 2% are materially restrictive for long-duration assets.
        parts.append((_clamp((level + 0.50) / 3.0 * 100.0), 0.65))
    if change is not None:
        # A +50 bp move across the recent window is treated as a strong tightening impulse.
        parts.append((_clamp(50.0 + change * 100.0), 0.35))
    weight = sum(w for _, w in parts)
    return None if weight <= 0 else round(sum(v * w for v, w in parts) / weight, 2)


def _liquidity_risk(series: Mapping[str, Any]) -> Optional[float]:
    dollar_level = _series_value(series, "DTWEXBGS", "value")
    dollar_change = _series_value(series, "DTWEXBGS", "change_20obs")
    walcl_change = _series_value(series, "WALCL", "change_20obs")
    parts: list[tuple[float, float]] = []

    if dollar_change is not None:
        # A rising broad dollar generally tightens global USD financial conditions.
        dollar_risk = _clamp(50.0 + dollar_change * 10.0)
        parts.append((dollar_risk, 0.55))
    elif dollar_level is not None:
        # Level alone is weak evidence; keep its weight deliberately small.
        parts.append((50.0, 0.15))

    if walcl_change is not None:
        # WALCL is reported in millions of USD. Contraction raises liquidity risk.
        contraction = -walcl_change / 100000.0
        fed_risk = _clamp(50.0 + 38.0 * math.tanh(contraction))
        parts.append((fed_risk, 0.45))

    weight = sum(w for _, w in parts)
    return None if weight <= 0 else round(sum(v * w for v, w in parts) / weight, 2)


def _merge_macro_risk(result: Dict[str, Any]) -> None:
    fred = result.get("fred")
    if not isinstance(fred, dict):
        return
    derived = fred.get("derived")
    if not isinstance(derived, dict):
        derived = {}
        fred["derived"] = derived

    base_risk = _finite(derived.get("macro_risk_score"))
    real_yield = _real_yield_risk(fred)
    liquidity = _liquidity_risk(fred)

    components: list[tuple[float, float, str]] = []
    if base_risk is not None:
        components.append((base_risk, 0.70, "base_rates_credit_volatility"))
    if real_yield is not None:
        components.append((real_yield, 0.15, "real_yield"))
    if liquidity is not None:
        components.append((liquidity, 0.15, "usd_liquidity"))

    if not components:
        return
    total = sum(weight for _, weight, _ in components)
    combined = round(sum(value * weight for value, weight, _ in components) / total, 2)
    derived["base_macro_risk_score"] = base_risk
    derived["real_yield_risk_score"] = real_yield
    derived["liquidity_risk_score"] = liquidity
    derived["macro_risk_score"] = combined
    derived["macro_risk_version"] = "macro-risk-v9-real-yield-liquidity.1"
    derived["macro_risk_sources"] = [name for _, _, name in components]
    derived["missing_evidence_policy"] = "missing components are omitted, never replaced with neutral 50"


def fetch_free_context_v9(codes: Iterable[str]) -> Dict[str, Any]:
    """Extend the existing free-source snapshot without changing its failure policy."""
    result = base.fetch_free_context(codes)
    status = result.get("status") if isinstance(result, dict) else None
    if not isinstance(status, dict) or not status.get("enabled"):
        return result

    fred_key = os.getenv("FRED_API_KEY", "").strip()
    if not fred_key:
        return result

    fred = result.setdefault("fred", {})
    if not isinstance(fred, dict):
        fred = {}
        result["fred"] = fred

    for series_id, label in V9_EXTRA_FRED_SERIES.items():
        try:
            query = urllib.parse.urlencode(
                {
                    "series_id": series_id,
                    "api_key": fred_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 90,
                }
            )
            payload = base._get_json(f"{base.FRED_OBSERVATIONS_URL}?{query}")
            summary = base._series_summary(payload if isinstance(payload, dict) else {})
            fred[series_id] = {
                "label": label,
                "summary": summary,
                "latest": (
                    {"date": summary.get("date"), "value": summary.get("value")}
                    if summary
                    else None
                ),
            }
        except Exception as exc:
            fred[series_id] = {
                "label": label,
                "error": f"{type(exc).__name__}: {exc}",
            }

    _merge_macro_risk(result)
    fred["v9_extension"] = {
        "series": sorted(V9_EXTRA_FRED_SERIES),
        "role": "real-yield and USD-liquidity regime evidence",
        "numeric_policy": "current snapshots are eligible only for current canonical signals",
    }
    return result
