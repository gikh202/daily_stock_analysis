from __future__ import annotations

import json
import math
import os
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple


SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
DEFAULT_FRED_SERIES = {
    "DGS10": "US 10Y Treasury",
    "DGS2": "US 2Y Treasury",
    "BAMLH0A0HYM2": "US High Yield OAS",
    "VIXCLS": "VIX",
}


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _get_json(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 10.0,
) -> Any:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _latest_non_missing_observation(payload: Dict[str, Any]) -> Optional[Dict[str, str]]:
    observations = payload.get("observations")
    if not isinstance(observations, list):
        return None
    for item in observations:
        if not isinstance(item, dict):
            continue
        value = str(item.get("value") or "").strip()
        if value and value != ".":
            return {"date": str(item.get("date") or ""), "value": value}
    return None


def _recent_sec_filings(payload: Dict[str, Any], *, limit: int = 6) -> list[Dict[str, str]]:
    filings = payload.get("filings")
    recent = filings.get("recent") if isinstance(filings, dict) else None
    if not isinstance(recent, dict):
        return []
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accessions = recent.get("accessionNumber") or []
    primary_docs = recent.get("primaryDocument") or []
    accepted = {"10-K", "10-Q", "8-K", "20-F", "6-K"}
    result: list[Dict[str, str]] = []
    for index, form in enumerate(forms):
        form_text = str(form or "").strip()
        if form_text not in accepted:
            continue
        result.append(
            {
                "form": form_text,
                "filing_date": str(dates[index] if index < len(dates) else ""),
                "accession": str(accessions[index] if index < len(accessions) else ""),
                "primary_document": str(primary_docs[index] if index < len(primary_docs) else ""),
            }
        )
        if len(result) >= limit:
            break
    return result


def _fact_rows(
    companyfacts: Mapping[str, Any],
    tags: Sequence[str],
    unit_candidates: Sequence[str],
) -> list[Dict[str, Any]]:
    facts = companyfacts.get("facts")
    us_gaap = facts.get("us-gaap") if isinstance(facts, dict) else None
    if not isinstance(us_gaap, dict):
        return []
    for tag in tags:
        fact = us_gaap.get(tag)
        units = fact.get("units") if isinstance(fact, dict) else None
        if not isinstance(units, dict):
            continue
        for unit in unit_candidates:
            rows = units.get(unit)
            if not isinstance(rows, list):
                continue
            clean: list[Dict[str, Any]] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if str(row.get("form") or "") not in {"10-K", "10-Q", "20-F", "6-K"}:
                    continue
                value = _finite(row.get("val"))
                end = str(row.get("end") or "")
                if value is None or not end:
                    continue
                item = dict(row)
                item["val"] = value
                item["fact_tag"] = tag
                clean.append(item)
            if clean:
                return clean
    return []


def _annual_values(
    companyfacts: Mapping[str, Any],
    tags: Sequence[str],
    units: Sequence[str] = ("USD",),
) -> list[Dict[str, Any]]:
    rows = _fact_rows(companyfacts, tags, units)
    by_end: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if str(row.get("fp") or "") != "FY" or str(row.get("form") or "") not in {"10-K", "20-F"}:
            continue
        end = str(row.get("end") or "")
        previous = by_end.get(end)
        if previous is None or str(row.get("filed") or "") > str(previous.get("filed") or ""):
            by_end[end] = row
    return sorted(
        by_end.values(),
        key=lambda item: str(item.get("end") or ""),
        reverse=True,
    )


def _latest_instant(
    companyfacts: Mapping[str, Any],
    tags: Sequence[str],
    units: Sequence[str] = ("USD",),
) -> Optional[Dict[str, Any]]:
    rows = _fact_rows(companyfacts, tags, units)
    if not rows:
        return None
    rows.sort(
        key=lambda item: (
            str(item.get("end") or ""),
            str(item.get("filed") or ""),
        ),
        reverse=True,
    )
    return rows[0]


def _latest_total_debt(companyfacts: Mapping[str, Any]) -> Tuple[Optional[float], Optional[str], str, bool]:
    """Return debt without silently dropping the noncurrent portion.

    Prefer a total-debt concept. When the filer splits current and noncurrent
    maturities, sum values only when they refer to the same reporting date.
    Partial values are returned for display but marked incomplete so they do not
    contribute a full balance-sheet quality component.
    """
    total = _latest_instant(
        companyfacts,
        (
            "LongTermDebtAndFinanceLeaseObligations",
            "LongTermDebt",
            "DebtAndFinanceLeaseObligations",
        ),
    )
    if total:
        return (
            _finite(total.get("val")),
            str(total.get("end") or "") or None,
            str(total.get("fact_tag") or "total_debt"),
            True,
        )

    current = _latest_instant(
        companyfacts,
        (
            "LongTermDebtAndFinanceLeaseObligationsCurrent",
            "LongTermDebtCurrent",
            "CurrentPortionOfLongTermDebt",
        ),
    )
    noncurrent = _latest_instant(
        companyfacts,
        (
            "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
            "LongTermDebtNoncurrent",
        ),
    )
    current_value = _finite(current.get("val")) if current else None
    noncurrent_value = _finite(noncurrent.get("val")) if noncurrent else None
    current_end = str(current.get("end") or "") if current else ""
    noncurrent_end = str(noncurrent.get("end") or "") if noncurrent else ""

    if (
        current_value is not None
        and noncurrent_value is not None
        and current_end
        and current_end == noncurrent_end
    ):
        return (
            current_value + noncurrent_value,
            current_end,
            "current_plus_noncurrent_long_term_debt",
            True,
        )
    if noncurrent_value is not None:
        return noncurrent_value, noncurrent_end or None, "noncurrent_debt_partial", False
    if current_value is not None:
        return current_value, current_end or None, "current_debt_partial", False
    return None, None, "unavailable", False


def _growth(series: Sequence[Mapping[str, Any]]) -> Optional[float]:
    if len(series) < 2:
        return None
    current = _finite(series[0].get("val"))
    previous = _finite(series[1].get("val"))
    if current is None or previous is None or abs(previous) < 1e-12:
        return None
    return round((current / previous - 1.0) * 100.0, 4)


def _ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator is None or abs(denominator) < 1e-12:
        return None
    return round(100.0 * numerator / denominator, 4)


def _score_growth(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(_clamp(50.0 + 38.0 * math.tanh(value / 22.0)), 2)


def _score_margin(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(_clamp(45.0 + 42.0 * math.tanh(value / 28.0)), 2)


def _fundamental_snapshot(companyfacts: Mapping[str, Any]) -> Dict[str, Any]:
    revenue = _annual_values(
        companyfacts,
        (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
        ),
    )
    operating_income = _annual_values(companyfacts, ("OperatingIncomeLoss",))
    net_income = _annual_values(companyfacts, ("NetIncomeLoss", "ProfitLoss"))
    ocf = _annual_values(
        companyfacts,
        ("NetCashProvidedByUsedInOperatingActivities",),
    )
    capex = _annual_values(
        companyfacts,
        (
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsForProceedsFromProductiveAssets",
        ),
    )
    shares = _annual_values(
        companyfacts,
        (
            "WeightedAverageNumberOfDilutedSharesOutstanding",
            "WeightedAverageNumberOfSharesOutstandingBasic",
        ),
        ("shares",),
    )
    cash_row = _latest_instant(
        companyfacts,
        (
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        ),
    )
    debt, debt_date, debt_source, debt_complete = _latest_total_debt(companyfacts)

    revenue_latest = _finite(revenue[0].get("val")) if revenue else None
    op_latest = _finite(operating_income[0].get("val")) if operating_income else None
    net_latest = _finite(net_income[0].get("val")) if net_income else None
    ocf_latest = _finite(ocf[0].get("val")) if ocf else None
    capex_latest = _finite(capex[0].get("val")) if capex else None
    fcf = (
        None
        if ocf_latest is None or capex_latest is None
        else ocf_latest - abs(capex_latest)
    )

    revenue_growth = _growth(revenue)
    operating_income_growth = _growth(operating_income)
    net_income_growth = _growth(net_income)
    op_margin = _ratio(op_latest, revenue_latest)
    net_margin = _ratio(net_latest, revenue_latest)
    fcf_margin = _ratio(fcf, revenue_latest)
    dilution = _growth(shares)
    cash = _finite(cash_row.get("val")) if cash_row else None

    components: Dict[str, Optional[float]] = {
        "revenue_growth": _score_growth(revenue_growth),
        "operating_income_growth": _score_growth(operating_income_growth),
        "net_income_growth": _score_growth(net_income_growth),
        "operating_margin": _score_margin(op_margin),
        "fcf_margin": _score_margin(fcf_margin),
        "balance_sheet": None,
        "dilution": None,
    }
    if cash is not None and debt is not None and debt_complete:
        cash_to_debt = cash / max(abs(debt), 1.0)
        components["balance_sheet"] = round(
            _clamp(35.0 + 45.0 * math.tanh(cash_to_debt / 1.5)),
            2,
        )
    if dilution is not None:
        components["dilution"] = round(_clamp(75.0 - 6.0 * dilution), 2)

    observed = [value for value in components.values() if value is not None]
    quality = None if not observed else round(sum(observed) / len(observed), 2)
    return {
        "quality_score": quality,
        "coverage": round(len(observed) / len(components), 4),
        "revenue_yoy_pct": revenue_growth,
        "operating_income_yoy_pct": operating_income_growth,
        "net_income_yoy_pct": net_income_growth,
        "operating_margin_pct": op_margin,
        "net_margin_pct": net_margin,
        "free_cash_flow": fcf,
        "fcf_margin_pct": fcf_margin,
        "cash": cash,
        "debt": debt,
        "debt_date": debt_date,
        "debt_source": debt_source,
        "debt_complete": debt_complete,
        "diluted_shares_yoy_pct": dilution,
        "components": components,
        "source": "SEC CompanyFacts/XBRL",
    }


def _series_summary(payload: Mapping[str, Any]) -> Dict[str, Any]:
    observations = payload.get("observations")
    if not isinstance(observations, list):
        return {}
    values: list[tuple[str, float]] = []
    for item in observations:
        if not isinstance(item, dict):
            continue
        value = _finite(item.get("value"))
        date_value = str(item.get("date") or "")
        if value is not None and date_value:
            values.append((date_value, value))
    if not values:
        return {}
    current = values[0][1]
    history = [value for _, value in values]
    percentile = 100.0 * sum(1 for value in history if value <= current) / len(history)
    return {
        "date": values[0][0],
        "value": current,
        "change_5obs": None if len(values) <= 5 else round(current - values[5][1], 4),
        "change_20obs": None if len(values) <= 20 else round(current - values[20][1], 4),
        "window_percentile": round(percentile, 2),
        "observations": len(values),
    }


def _fred_derived(series: Mapping[str, Any]) -> Dict[str, Any]:
    def value(series_id: str, field: str = "value") -> Optional[float]:
        item = series.get(series_id)
        summary = item.get("summary") if isinstance(item, dict) else None
        return _finite(summary.get(field)) if isinstance(summary, dict) else None

    d10 = value("DGS10")
    d2 = value("DGS2")
    hy = value("BAMLH0A0HYM2")
    vix = value("VIXCLS")
    d10_change = value("DGS10", "change_5obs")
    hy_change = value("BAMLH0A0HYM2", "change_5obs")
    vix_change = value("VIXCLS", "change_5obs")
    curve = None if d10 is None or d2 is None else round(d10 - d2, 4)

    components: Dict[str, Optional[float]] = {
        "volatility": None if vix is None else _clamp((vix - 12.0) / 28.0 * 100.0),
        "credit": None if hy is None else _clamp((hy - 2.0) / 6.0 * 100.0),
        "rates": None,
    }
    if d10 is not None:
        rate_level = _clamp((d10 - 2.0) / 4.0 * 100.0)
        rate_change = (
            50.0
            if d10_change is None
            else _clamp(50.0 + d10_change * 100.0)
        )
        inversion = 75.0 if curve is not None and curve < 0 else 35.0
        components["rates"] = (
            0.45 * rate_level + 0.30 * rate_change + 0.25 * inversion
        )

    weighted: list[tuple[float, float]] = []
    for name, weight in (("volatility", 0.45), ("credit", 0.35), ("rates", 0.20)):
        component = components[name]
        if component is not None:
            weighted.append((component, weight))
    macro_risk = None
    if weighted:
        total_weight = sum(weight for _, weight in weighted)
        macro_risk = round(
            sum(component * weight for component, weight in weighted) / total_weight,
            2,
        )

    return {
        "macro_risk_score": macro_risk,
        "yield_curve_10y_2y": curve,
        "dgs10_change_5obs": d10_change,
        "hy_oas_change_5obs": hy_change,
        "vix_change_5obs": vix_change,
        "components": {
            key: None if component is None else round(component, 2)
            for key, component in components.items()
        },
    }


def source_status() -> Dict[str, Any]:
    return {
        "enabled": _truthy(os.getenv("V6_FREE_SOURCE_ENRICHMENT", "false")),
        "sec": {
            "configured": bool(os.getenv("SEC_USER_AGENT", "").strip()),
            "cost": "free",
            "role": "official filing metadata plus coverage-gated CompanyFacts fundamentals",
        },
        "fred": {
            "configured": bool(os.getenv("FRED_API_KEY", "").strip()),
            "cost": "free API key",
            "role": "macro level/change snapshot plus deterministic macro-risk evidence",
        },
        "existing_repository_sources": {
            "yfinance": "free market/daily data",
            "serpapi": "existing optional search provider",
            "brave": "existing optional search fallback when configured",
        },
    }


def fetch_free_context(codes: Iterable[str]) -> Dict[str, Any]:
    """Best-effort current public-data snapshot; failures never abort V6.

    The daily runner is responsible for ensuring this *current* snapshot is not
    numerically injected into historical/backfilled signals.
    """
    status = source_status()
    result: Dict[str, Any] = {"status": status, "sec": {}, "fred": {}}
    if not status["enabled"]:
        return result

    sec_user_agent = os.getenv("SEC_USER_AGENT", "").strip()
    if sec_user_agent:
        headers = {"User-Agent": sec_user_agent, "Accept": "application/json"}
        try:
            tickers_payload = _get_json(SEC_TICKERS_URL, headers=headers)
            ticker_map: Dict[str, str] = {}
            if isinstance(tickers_payload, dict):
                for item in tickers_payload.values():
                    if not isinstance(item, dict):
                        continue
                    ticker = str(item.get("ticker") or "").strip().upper()
                    cik = item.get("cik_str")
                    if ticker and cik is not None:
                        ticker_map[ticker] = f"{int(cik):010d}"
            normalized = list(
                dict.fromkeys(str(code or "").strip().upper() for code in codes)
            )[:30]
            for code in normalized:
                cik = ticker_map.get(code)
                if not cik:
                    continue
                item: Dict[str, Any] = {"cik": cik}
                try:
                    submissions = _get_json(
                        SEC_SUBMISSIONS_URL.format(cik=cik),
                        headers=headers,
                    )
                    if isinstance(submissions, dict):
                        item["company"] = submissions.get("name")
                        item["recent_filings"] = _recent_sec_filings(submissions)
                except Exception as exc:
                    item["filings_error"] = f"{type(exc).__name__}: {exc}"
                try:
                    facts = _get_json(
                        SEC_COMPANYFACTS_URL.format(cik=cik),
                        headers=headers,
                    )
                    if isinstance(facts, dict):
                        item["fundamentals"] = _fundamental_snapshot(facts)
                except Exception as exc:
                    item["fundamentals_error"] = f"{type(exc).__name__}: {exc}"
                result["sec"][code] = item
        except Exception as exc:
            result["sec_error"] = f"{type(exc).__name__}: {exc}"

    fred_key = os.getenv("FRED_API_KEY", "").strip()
    if fred_key:
        series: Dict[str, Any] = {}
        for series_id, label in DEFAULT_FRED_SERIES.items():
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
                payload = _get_json(f"{FRED_OBSERVATIONS_URL}?{query}")
                summary = _series_summary(
                    payload if isinstance(payload, dict) else {}
                )
                series[series_id] = {
                    "label": label,
                    "summary": summary,
                    "latest": (
                        {"date": summary.get("date"), "value": summary.get("value")}
                        if summary
                        else None
                    ),
                }
            except Exception as exc:
                series[series_id] = {
                    "label": label,
                    "error": f"{type(exc).__name__}: {exc}",
                }
        result["fred"] = dict(series)
        result["fred"]["derived"] = _fred_derived(series)

    return result
