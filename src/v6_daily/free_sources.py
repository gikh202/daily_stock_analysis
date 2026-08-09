from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, Optional


SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
DEFAULT_FRED_SERIES = {
    "DGS10": "US 10Y Treasury",
    "DGS2": "US 2Y Treasury",
    "BAMLH0A0HYM2": "US High Yield OAS",
    "VIXCLS": "VIX",
}


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _get_json(url: str, *, headers: Optional[Dict[str, str]] = None, timeout: float = 8.0) -> Any:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _latest_non_missing_observation(payload: Dict[str, Any]) -> Optional[Dict[str, str]]:
    observations = payload.get("observations")
    if not isinstance(observations, list):
        return None
    for item in reversed(observations):
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


def source_status() -> Dict[str, Any]:
    return {
        "enabled": _truthy(os.getenv("V6_FREE_SOURCE_ENRICHMENT", "false")),
        "sec": {
            "configured": bool(os.getenv("SEC_USER_AGENT", "").strip()),
            "cost": "free",
            "role": "official filing metadata only; not directly scored",
        },
        "fred": {
            "configured": bool(os.getenv("FRED_API_KEY", "").strip()),
            "cost": "free API key",
            "role": "macro context only; not directly scored",
        },
        "existing_repository_sources": {
            "yfinance": "free market/daily data",
            "brave": "optional free-credit search fallback via existing SearchService",
            "finnhub": "optional free-tier company/news data where configured",
        },
    }


def fetch_free_context(codes: Iterable[str]) -> Dict[str, Any]:
    """Best-effort public-data enrichment; failures are returned, never raised.

    This layer is deliberately informational in V6.0. It cannot change numeric
    forecasts until enough historical evidence exists to validate a mapping.
    """
    status = source_status()
    result: Dict[str, Any] = {"status": status, "sec": {}, "fred": {}}
    if not status["enabled"]:
        return result

    sec_user_agent = os.getenv("SEC_USER_AGENT", "").strip()
    if sec_user_agent:
        try:
            tickers_payload = _get_json(
                SEC_TICKERS_URL,
                headers={"User-Agent": sec_user_agent, "Accept-Encoding": "gzip, deflate"},
            )
            ticker_map: Dict[str, str] = {}
            if isinstance(tickers_payload, dict):
                for item in tickers_payload.values():
                    if not isinstance(item, dict):
                        continue
                    ticker = str(item.get("ticker") or "").strip().upper()
                    cik = item.get("cik_str")
                    if ticker and cik is not None:
                        ticker_map[ticker] = f"{int(cik):010d}"
            for code in list(dict.fromkeys(str(code or "").strip().upper() for code in codes))[:20]:
                cik = ticker_map.get(code)
                if not cik:
                    continue
                try:
                    payload = _get_json(
                        SEC_SUBMISSIONS_URL.format(cik=cik),
                        headers={"User-Agent": sec_user_agent, "Accept-Encoding": "gzip, deflate"},
                    )
                    if isinstance(payload, dict):
                        result["sec"][code] = {
                            "company": payload.get("name"),
                            "cik": cik,
                            "recent_filings": _recent_sec_filings(payload),
                        }
                except Exception as exc:
                    result["sec"][code] = {"error": f"{type(exc).__name__}: {exc}"}
        except Exception as exc:
            result["sec_error"] = f"{type(exc).__name__}: {exc}"

    fred_key = os.getenv("FRED_API_KEY", "").strip()
    if fred_key:
        for series_id, label in DEFAULT_FRED_SERIES.items():
            try:
                query = urllib.parse.urlencode(
                    {
                        "series_id": series_id,
                        "api_key": fred_key,
                        "file_type": "json",
                        "sort_order": "asc",
                        "limit": 30,
                    }
                )
                payload = _get_json(f"{FRED_OBSERVATIONS_URL}?{query}")
                latest = _latest_non_missing_observation(payload if isinstance(payload, dict) else {})
                result["fred"][series_id] = {"label": label, "latest": latest}
            except Exception as exc:
                result["fred"][series_id] = {"label": label, "error": f"{type(exc).__name__}: {exc}"}

    return result
