# -*- coding: utf-8 -*-
"""
SEC EDGAR fundamental adapter for US stocks.

Purpose
-------
Supplement the project's existing YFinance-based US fundamental context with
official SEC EDGAR data. This adapter is additive and fail-open:
- no API key is required;
- no SEC data -> returns not_supported instead of breaking stock analysis;
- ETF/fund-like symbols without useful 10-K/10-Q company facts are skipped;
- responses are cached under data/sec_edgar so GitHub Actions cache can persist them.

Environment variables
---------------------
SEC_USER_AGENT
    Required for network access. Example:
    "daily_stock_analysis your-email@example.com"

SEC_EDGAR_ENABLED
    true/false, default true.

SEC_EDGAR_CACHE_TTL_SECONDS
    Cache TTL, default 86400 (24h).

SEC_EDGAR_HTTP_TIMEOUT_SECONDS
    Per-request timeout, default 5 seconds.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

_CACHE_DIR = Path("data/sec_edgar")

# SEC fair-access limit is 10 req/s. Keep a conservative per-process floor.
_MIN_REQUEST_INTERVAL_SECONDS = 0.12
_request_lock = threading.Lock()
_last_request_ts = 0.0


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


def _env_float(name: str, default: float, minimum: float = 0.1) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:  # NaN
        return None
    return result


def _pct_change(current: Any, previous: Any) -> Optional[float]:
    cur = _safe_float(current)
    prev = _safe_float(previous)
    if cur is None or prev in (None, 0):
        return None
    return round((cur - prev) / abs(prev) * 100.0, 4)


def _meaningful_dict(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    for value in payload.values():
        if value is None or value == "" or value == [] or value == {}:
            continue
        return True
    return False


def _merge_non_null(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Overlay non-empty values only."""
    merged = dict(base or {})
    for key, value in (overlay or {}).items():
        if value is None or value == "" or value == [] or value == {}:
            continue
        merged[key] = value
    return merged


class SecEdgarFundamentalAdapter:
    """Official SEC EDGAR Company Facts + Submissions adapter for US equities."""

    def __init__(self) -> None:
        self.enabled = _env_bool("SEC_EDGAR_ENABLED", True)
        self.user_agent = (os.getenv("SEC_USER_AGENT") or "").strip()
        self.cache_ttl = _env_int("SEC_EDGAR_CACHE_TTL_SECONDS", 86400, minimum=0)
        self.timeout = _env_float("SEC_EDGAR_HTTP_TIMEOUT_SECONDS", 5.0, minimum=0.5)
        self.session = requests.Session()
        if self.user_agent:
            self.session.headers.update(
                {
                    "User-Agent": self.user_agent,
                    "Accept-Encoding": "gzip, deflate",
                    "Accept": "application/json,text/plain,*/*",
                }
            )

    def get_fundamental_bundle(self, stock_code: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "status": "not_supported",
            "growth": {},
            "earnings": {},
            "institution": {},
            "boards": {},
            "belong_boards": [],
            "source_chain": [],
            "errors": [],
        }

        if not self.enabled:
            result["errors"].append("sec_edgar_disabled")
            return result

        if not self.user_agent:
            result["errors"].append(
                "SEC_USER_AGENT is not configured; SEC EDGAR network access skipped"
            )
            return result

        symbol = self._normalize_us_symbol(stock_code)
        if not symbol:
            result["errors"].append("empty_or_non_us_symbol")
            return result

        try:
            ticker_map = self._get_ticker_map()
            mapping = ticker_map.get(symbol)
            if not mapping:
                result["errors"].append(f"ticker_not_found:{symbol}")
                return result

            cik = str(mapping["cik"]).zfill(10)
            company_facts = self._get_json_cached(
                cache_name=f"companyfacts_{cik}.json",
                url=_SEC_COMPANY_FACTS_URL.format(cik=cik),
            )
            submissions = self._get_json_cached(
                cache_name=f"submissions_{cik}.json",
                url=_SEC_SUBMISSIONS_URL.format(cik=cik),
            )

            # Ordinary operating companies normally have 10-K / 10-Q filings.
            # If neither exists, do not force company-style fundamentals onto a fund/ETF.
            filings = self._extract_recent_filings(submissions)
            forms = {item.get("form") for item in filings}
            if not ({"10-K", "10-Q"} & forms):
                result["errors"].append("no_10k_or_10q_company_filings")
                return result

            facts = company_facts.get("facts", {}) if isinstance(company_facts, dict) else {}
            us_gaap = facts.get("us-gaap", {}) if isinstance(facts, dict) else {}
            dei = facts.get("dei", {}) if isinstance(facts, dict) else {}

            revenue = self._pick_fact(
                us_gaap,
                (
                    "RevenueFromContractWithCustomerExcludingAssessedTax",
                    "SalesRevenueNet",
                    "Revenues",
                ),
                preferred_units=("USD",),
            )
            net_income = self._pick_fact(
                us_gaap,
                ("NetIncomeLoss", "ProfitLoss"),
                preferred_units=("USD",),
            )
            operating_cf = self._pick_fact(
                us_gaap,
                ("NetCashProvidedByUsedInOperatingActivities",),
                preferred_units=("USD",),
            )
            assets = self._pick_fact(
                us_gaap,
                ("Assets",),
                preferred_units=("USD",),
            )
            liabilities = self._pick_fact(
                us_gaap,
                ("Liabilities",),
                preferred_units=("USD",),
            )
            equity = self._pick_fact(
                us_gaap,
                (
                    "StockholdersEquity",
                    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
                ),
                preferred_units=("USD",),
            )
            gross_profit = self._pick_fact(
                us_gaap,
                ("GrossProfit",),
                preferred_units=("USD",),
            )
            research_dev = self._pick_fact(
                us_gaap,
                ("ResearchAndDevelopmentExpense",),
                preferred_units=("USD",),
            )
            capex = self._pick_fact(
                us_gaap,
                (
                    "PaymentsToAcquirePropertyPlantAndEquipment",
                    "PaymentsForProceedsFromProductiveAssets",
                ),
                preferred_units=("USD",),
            )
            eps_diluted = self._pick_fact(
                us_gaap,
                ("EarningsPerShareDiluted",),
                preferred_units=("USD/shares", "USD-per-shares"),
            )
            shares_outstanding = self._pick_fact(
                dei,
                ("EntityCommonStockSharesOutstanding",),
                preferred_units=("shares",),
            )

            latest_report_end = self._latest_end(
                revenue,
                net_income,
                operating_cf,
                assets,
                liabilities,
                equity,
            )
            fiscal_year, fiscal_period = self._latest_fiscal_identity(
                revenue,
                net_income,
                operating_cf,
            )

            revenue_yoy = self._fact_yoy(revenue)
            net_income_yoy = self._fact_yoy(net_income)
            eps_yoy = self._fact_yoy(eps_diluted)

            debt_ratio_pct = None
            if self._same_end(assets, liabilities):
                asset_value = self._fact_value(assets)
                liabilities_value = self._fact_value(liabilities)
                if asset_value not in (None, 0) and liabilities_value is not None:
                    debt_ratio_pct = round(liabilities_value / asset_value * 100.0, 4)

            gross_margin_pct = None
            if self._same_end(revenue, gross_profit):
                revenue_value = self._fact_value(revenue)
                gross_profit_value = self._fact_value(gross_profit)
                if revenue_value not in (None, 0) and gross_profit_value is not None:
                    gross_margin_pct = round(gross_profit_value / revenue_value * 100.0, 4)

            free_cash_flow = None
            if self._same_end(operating_cf, capex):
                op_cf_value = self._fact_value(operating_cf)
                capex_value = self._fact_value(capex)
                if op_cf_value is not None and capex_value is not None:
                    free_cash_flow = op_cf_value - abs(capex_value)

            growth: Dict[str, Any] = {
                "revenue_yoy": revenue_yoy,
                "net_profit_yoy": net_income_yoy,
                "eps_yoy": eps_yoy,
                "gross_margin": gross_margin_pct,
            }
            growth = {k: v for k, v in growth.items() if v is not None}

            financial_report: Dict[str, Any] = {
                # Existing project-compatible keys:
                "report_date": latest_report_end,
                "revenue": self._fact_value(revenue),
                "net_profit_parent": self._fact_value(net_income),
                "operating_cash_flow": self._fact_value(operating_cf),
                "currency": "USD",
                # Extra SEC fields; downstream consumers ignore unknown keys safely.
                "assets": self._fact_value(assets),
                "liabilities": self._fact_value(liabilities),
                "stockholders_equity": self._fact_value(equity),
                "gross_profit": self._fact_value(gross_profit),
                "research_and_development": self._fact_value(research_dev),
                "capex": self._fact_value(capex),
                "free_cash_flow": free_cash_flow,
                "eps_diluted": self._fact_value(eps_diluted),
                "shares_outstanding": self._fact_value(shares_outstanding),
                "debt_ratio_pct": debt_ratio_pct,
                "gross_margin_pct": gross_margin_pct,
                "fiscal_year": fiscal_year,
                "fiscal_period": fiscal_period,
                "source": "sec_edgar",
                "cik": cik,
            }
            financial_report = {
                k: v for k, v in financial_report.items() if v is not None and v != ""
            }

            earnings: Dict[str, Any] = {}
            if financial_report:
                earnings["financial_report"] = financial_report
            if filings:
                earnings["sec_filings"] = filings[:8]
                for form_name, key in (
                    ("10-K", "latest_10k"),
                    ("10-Q", "latest_10q"),
                    ("8-K", "latest_8k"),
                ):
                    match = next((item for item in filings if item.get("form") == form_name), None)
                    if match:
                        earnings[key] = match

            if growth:
                result["growth"] = growth
                result["source_chain"].append("growth:sec_edgar.companyfacts")
            if earnings:
                result["earnings"] = earnings
                result["source_chain"].append("earnings:sec_edgar.companyfacts")
                result["source_chain"].append("filings:sec_edgar.submissions")

            has_content = bool(growth or earnings)
            result["status"] = "partial" if has_content else "not_supported"
            return result

        except Exception as exc:  # fail-open by design
            logger.warning("[SEC EDGAR] %s fundamentals failed: %s", symbol, exc)
            result["errors"].append(f"{type(exc).__name__}:{exc}")
            return result

    @staticmethod
    def merge_into_yfinance_bundle(
        yfinance_bundle: Dict[str, Any],
        sec_bundle: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Merge official SEC data over YFinance for overlapping accounting fields,
        while preserving YFinance-only fields such as dividend and sector/industry.
        """
        merged = dict(yfinance_bundle or {})
        merged.setdefault("growth", {})
        merged.setdefault("earnings", {})
        merged.setdefault("institution", {})
        merged.setdefault("boards", {})
        merged.setdefault("belong_boards", [])
        merged.setdefault("source_chain", [])
        merged.setdefault("errors", [])

        sec_growth = sec_bundle.get("growth", {}) if isinstance(sec_bundle, dict) else {}
        if isinstance(sec_growth, dict):
            merged["growth"] = _merge_non_null(merged.get("growth", {}), sec_growth)

        sec_earnings = sec_bundle.get("earnings", {}) if isinstance(sec_bundle, dict) else {}
        if isinstance(sec_earnings, dict):
            current_earnings = dict(merged.get("earnings") or {})
            sec_financial = sec_earnings.get("financial_report")
            if isinstance(sec_financial, dict):
                current_financial = current_earnings.get("financial_report")
                if not isinstance(current_financial, dict):
                    current_financial = {}
                # SEC wins only where it has a real value; YFinance fills gaps.
                current_earnings["financial_report"] = _merge_non_null(
                    current_financial,
                    sec_financial,
                )
            for key, value in sec_earnings.items():
                if key == "financial_report":
                    continue
                if value is not None and value != "" and value != [] and value != {}:
                    current_earnings[key] = value
            merged["earnings"] = current_earnings

        merged["source_chain"] = list(merged.get("source_chain") or []) + list(
            sec_bundle.get("source_chain") or []
        )
        merged["errors"] = list(merged.get("errors") or []) + list(
            sec_bundle.get("errors") or []
        )

        has_content = bool(
            merged.get("growth")
            or merged.get("earnings")
            or merged.get("belong_boards")
        )
        merged["status"] = "partial" if has_content else "not_supported"
        return merged

    def _get_ticker_map(self) -> Dict[str, Dict[str, Any]]:
        payload = self._get_json_cached(
            cache_name="company_tickers.json",
            url=_SEC_TICKERS_URL,
            ttl_seconds=max(self.cache_ttl, 7 * 86400),
        )
        result: Dict[str, Dict[str, Any]] = {}
        if not isinstance(payload, dict):
            return result
        for item in payload.values():
            if not isinstance(item, dict):
                continue
            ticker = str(item.get("ticker") or "").strip().upper()
            cik = item.get("cik_str")
            if not ticker or cik is None:
                continue
            result[ticker] = {
                "cik": cik,
                "title": item.get("title"),
            }
        return result

    def _get_json_cached(
        self,
        *,
        cache_name: str,
        url: str,
        ttl_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        ttl = self.cache_ttl if ttl_seconds is None else ttl_seconds
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _CACHE_DIR / cache_name

        if path.exists() and ttl > 0:
            try:
                age = time.time() - path.stat().st_mtime
                if age <= ttl:
                    with path.open("r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    if isinstance(data, dict):
                        return data
            except Exception:
                pass

        data = self._request_json(url)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
            tmp_path.replace(path)
        except Exception as exc:
            logger.debug("[SEC EDGAR] cache write failed %s: %s", path, exc)
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
        return data

    def _request_json(self, url: str) -> Dict[str, Any]:
        global _last_request_ts
        with _request_lock:
            now = time.monotonic()
            wait = _MIN_REQUEST_INTERVAL_SECONDS - (now - _last_request_ts)
            if wait > 0:
                time.sleep(wait)
            response = self.session.get(url, timeout=self.timeout)
            _last_request_ts = time.monotonic()

        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError(f"unexpected SEC payload type: {type(payload).__name__}")
        return payload

    @staticmethod
    def _normalize_us_symbol(stock_code: str) -> str:
        symbol = (stock_code or "").strip().upper()
        if not symbol:
            return ""
        if symbol.startswith("HK") or "." in symbol or symbol.isdigit():
            return ""
        return symbol

    @staticmethod
    def _extract_recent_filings(submissions: Dict[str, Any]) -> List[Dict[str, Any]]:
        recent = (
            submissions.get("filings", {}).get("recent", {})
            if isinstance(submissions, dict)
            else {}
        )
        if not isinstance(recent, dict):
            return []

        forms = recent.get("form") or []
        filing_dates = recent.get("filingDate") or []
        report_dates = recent.get("reportDate") or []
        accessions = recent.get("accessionNumber") or []
        primary_docs = recent.get("primaryDocument") or []

        items: List[Dict[str, Any]] = []
        count = min(
            len(forms),
            len(filing_dates),
            len(accessions),
            len(primary_docs),
        )
        for idx in range(count):
            form = str(forms[idx] or "").upper()
            if form not in {"10-K", "10-Q", "8-K"}:
                continue
            items.append(
                {
                    "form": form,
                    "filing_date": filing_dates[idx] or None,
                    "report_date": report_dates[idx] if idx < len(report_dates) else None,
                    "accession_number": accessions[idx] or None,
                    "primary_document": primary_docs[idx] or None,
                }
            )
        items.sort(key=lambda x: x.get("filing_date") or "", reverse=True)
        return items

    @staticmethod
    def _pick_fact(
        namespace: Dict[str, Any],
        tags: Iterable[str],
        *,
        preferred_units: Tuple[str, ...],
    ) -> Optional[Dict[str, Any]]:
        for tag in tags:
            concept = namespace.get(tag)
            if not isinstance(concept, dict):
                continue
            units = concept.get("units")
            if not isinstance(units, dict):
                continue

            selected_entries: Optional[List[Dict[str, Any]]] = None
            for unit in preferred_units:
                entries = units.get(unit)
                if isinstance(entries, list) and entries:
                    selected_entries = entries
                    break
            if selected_entries is None:
                # Last-resort: use the first available unit list.
                for entries in units.values():
                    if isinstance(entries, list) and entries:
                        selected_entries = entries
                        break
            if not selected_entries:
                continue

            filtered = [
                item
                for item in selected_entries
                if isinstance(item, dict)
                and item.get("form") in {"10-Q", "10-K"}
                and item.get("val") is not None
            ]
            if not filtered:
                continue

            # Deduplicate repeated facts/amendments, then pick the most recently filed.
            filtered.sort(
                key=lambda item: (
                    str(item.get("filed") or ""),
                    str(item.get("end") or ""),
                ),
                reverse=True,
            )
            latest = dict(filtered[0])
            latest["_tag"] = tag
            latest["_unit"] = next(
                (
                    unit
                    for unit, entries in units.items()
                    if isinstance(entries, list) and latest in entries
                ),
                None,
            )
            latest["_history"] = filtered
            return latest
        return None

    @staticmethod
    def _fact_value(fact: Optional[Dict[str, Any]]) -> Optional[float]:
        if not isinstance(fact, dict):
            return None
        return _safe_float(fact.get("val"))

    @staticmethod
    def _fact_yoy(fact: Optional[Dict[str, Any]]) -> Optional[float]:
        if not isinstance(fact, dict):
            return None
        current = _safe_float(fact.get("val"))
        if current is None:
            return None

        history = fact.get("_history")
        if not isinstance(history, list):
            return None

        current_fy = fact.get("fy")
        current_fp = fact.get("fp")
        current_form = fact.get("form")
        current_end = str(fact.get("end") or "")

        candidates: List[Dict[str, Any]] = []
        for item in history:
            if not isinstance(item, dict) or item is fact:
                continue
            if item.get("val") is None:
                continue
            # Prefer exact same fiscal period in previous fiscal year.
            if (
                current_fy is not None
                and item.get("fy") == current_fy - 1
                and item.get("fp") == current_fp
                and item.get("form") == current_form
            ):
                candidates.append(item)

        if not candidates and current_end:
            # Fallback: same form/fp and an end date roughly one year earlier.
            try:
                from datetime import date

                y, m, d = [int(x) for x in current_end.split("-")]
                current_date = date(y, m, d)
                for item in history:
                    end = str(item.get("end") or "")
                    if not end or item.get("form") != current_form or item.get("fp") != current_fp:
                        continue
                    try:
                        ey, em, ed = [int(x) for x in end.split("-")]
                        days = (current_date - date(ey, em, ed)).days
                    except Exception:
                        continue
                    if 330 <= days <= 400:
                        candidates.append(item)
            except Exception:
                pass

        if not candidates:
            return None
        candidates.sort(key=lambda item: str(item.get("filed") or ""), reverse=True)
        return _pct_change(current, candidates[0].get("val"))

    @staticmethod
    def _same_end(*facts: Optional[Dict[str, Any]]) -> bool:
        ends = [
            str(fact.get("end") or "")
            for fact in facts
            if isinstance(fact, dict) and fact.get("val") is not None
        ]
        return len(ends) >= 2 and len(set(ends)) == 1

    @staticmethod
    def _latest_end(*facts: Optional[Dict[str, Any]]) -> Optional[str]:
        ends = [
            str(fact.get("end") or "")
            for fact in facts
            if isinstance(fact, dict) and fact.get("end")
        ]
        return max(ends) if ends else None

    @staticmethod
    def _latest_fiscal_identity(
        *facts: Optional[Dict[str, Any]],
    ) -> Tuple[Optional[int], Optional[str]]:
        candidates = [fact for fact in facts if isinstance(fact, dict)]
        candidates.sort(
            key=lambda fact: (
                str(fact.get("filed") or ""),
                str(fact.get("end") or ""),
            ),
            reverse=True,
        )
        if not candidates:
            return None, None
        latest = candidates[0]
        fy = latest.get("fy")
        fp = latest.get("fp")
        try:
            fy = int(fy) if fy is not None else None
        except (TypeError, ValueError):
            fy = None
        return fy, str(fp) if fp else None
