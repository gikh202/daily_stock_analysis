# -*- coding: utf-8 -*-
"""
YFinance 财报事件适配器
=====================

目标：
- 为美股公司补充“下一次财报日期 / EPS 一致预期 / 历史 EPS surprise /
  EPS 预期修正 / 营收一致预期”等结构化事件数据。
- ETF / 指数直接跳过。
- 所有网络异常 fail-open：返回状态和错误，不中断主分析。
- 缺失数据只表示“证据不可用”，不能被解释成利空。

依赖：
- yfinance（项目 requirements.txt 已包含）
- pandas
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timezone
from threading import RLock
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


class YfinanceEarningsEventAdapter:
    """美股公司财报事件适配器。"""

    # 常见指数 / ETF。第一版先做保守跳过，同时还会结合 stock_name 判断 ETF。
    _KNOWN_FUND_SYMBOLS = frozenset(
        {
            "SPY", "VOO", "IVV", "QQQ", "QQQM", "VTI", "VT", "DIA", "IWM",
            "SCHD", "SCHG", "SCHX", "SPLG", "RSP", "VUG", "VTV", "VEA", "VWO",
            "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE",
            "SMH", "SOXX", "ARKK", "TLT", "IEF", "SHY", "BND", "AGG", "GLD", "SLV",
        }
    )

    def __init__(self) -> None:
        self.enabled = self._env_bool("EARNINGS_EVENT_ENABLED", True)
        self.cache_ttl_seconds = max(
            0,
            self._env_int("EARNINGS_EVENT_CACHE_TTL_SECONDS", 21600),
        )
        self._cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._cache_lock = RLock()

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return raw.strip().lower() not in {"0", "false", "no", "off"}

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            if value is None or pd.isna(value):
                return None
            number = float(value)
            if pd.isna(number):
                return None
            return number
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_int(value: Any) -> Optional[int]:
        number = YfinanceEarningsEventAdapter._safe_float(value)
        return int(number) if number is not None else None

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        text = " ".join(str(exc).split()).strip()
        return f"{type(exc).__name__}: {text}"[:240]

    @classmethod
    def _is_index_or_etf(cls, symbol: str, stock_name: str = "") -> bool:
        upper_symbol = (symbol or "").strip().upper()
        upper_name = (stock_name or "").strip().upper()

        if not upper_symbol:
            return False
        if upper_symbol.startswith("^"):
            return True
        if upper_symbol in cls._KNOWN_FUND_SYMBOLS:
            return True

        # 只使用较明确的基金关键词，避免普通公司名误判。
        fund_name_markers = (
            " ETF",
            "ETF ",
            "EXCHANGE TRADED FUND",
            "INDEX FUND",
            "NASDAQ 100 ETF",
            "S&P 500 ETF",
        )
        return any(marker in upper_name for marker in fund_name_markers)

    def _get_cached(self, symbol: str) -> Optional[Dict[str, Any]]:
        if self.cache_ttl_seconds <= 0:
            return None
        with self._cache_lock:
            item = self._cache.get(symbol)
            if not item:
                return None
            cached_at, payload = item
            if time.time() - cached_at > self.cache_ttl_seconds:
                self._cache.pop(symbol, None)
                return None
            return dict(payload)

    def _put_cached(self, symbol: str, payload: Dict[str, Any]) -> None:
        if self.cache_ttl_seconds <= 0:
            return
        with self._cache_lock:
            self._cache[symbol] = (time.time(), dict(payload))

    @staticmethod
    def _event_risk(days_to_earnings: Optional[int]) -> str:
        if days_to_earnings is None:
            return "unknown"
        if days_to_earnings <= 3:
            return "very_high"
        if days_to_earnings <= 7:
            return "high"
        if days_to_earnings <= 14:
            return "medium"
        return "low"

    @staticmethod
    def _get_row(df: Any, row_name: str) -> Optional[pd.Series]:
        if not isinstance(df, pd.DataFrame) or df.empty:
            return None
        if row_name not in df.index:
            return None
        row = df.loc[row_name]
        if isinstance(row, pd.DataFrame):
            if row.empty:
                return None
            return row.iloc[0]
        if isinstance(row, pd.Series):
            return row
        return None

    @staticmethod
    def _as_timestamp(value: Any) -> Optional[pd.Timestamp]:
        if value is None:
            return None
        try:
            ts = pd.Timestamp(value)
            if pd.isna(ts):
                return None
            return ts
        except Exception:
            return None

    def _fill_from_earnings_dates(
        self,
        ticker: Any,
        result: Dict[str, Any],
        errors: List[str],
    ) -> None:
        """
        get_earnings_dates() 同时提供未来预计财报日和历史实际 EPS / Surprise(%).

        yfinance 当前格式：
        - index: Earnings Date
        - columns: EPS Estimate / Reported EPS / Surprise(%)
        """
        try:
            frame = ticker.get_earnings_dates(limit=12)
        except Exception as exc:
            errors.append(f"earnings_dates:{self._safe_error(exc)}")
            return

        if not isinstance(frame, pd.DataFrame) or frame.empty:
            return

        today = datetime.now(timezone.utc).date()
        rows: List[Tuple[pd.Timestamp, pd.Series]] = []

        for idx, row in frame.iterrows():
            ts = self._as_timestamp(idx)
            if ts is not None:
                rows.append((ts, row))

        if not rows:
            return

        rows.sort(key=lambda item: item[0])

        # 下一次尚未公布的财报。
        future_candidates: List[Tuple[pd.Timestamp, pd.Series]] = []
        for ts, row in rows:
            event_day = ts.date()
            reported_eps = self._safe_float(row.get("Reported EPS"))
            if event_day >= today and reported_eps is None:
                future_candidates.append((ts, row))

        if future_candidates:
            next_ts, next_row = future_candidates[0]
            days = (next_ts.date() - today).days
            result["next_earnings_date"] = next_ts.date().isoformat()
            result["next_earnings_datetime"] = next_ts.isoformat()
            result["days_to_earnings"] = days
            result["event_risk"] = self._event_risk(days)

            estimate = self._safe_float(next_row.get("EPS Estimate"))
            if estimate is not None:
                result["next_eps_estimate"] = estimate

        # 最近一次已经公布的财报。
        past_candidates: List[Tuple[pd.Timestamp, pd.Series]] = []
        for ts, row in rows:
            if ts.date() <= today and self._safe_float(row.get("Reported EPS")) is not None:
                past_candidates.append((ts, row))

        if past_candidates:
            last_ts, last_row = past_candidates[-1]
            result["last_earnings_date"] = last_ts.date().isoformat()
            result["last_eps_estimate"] = self._safe_float(last_row.get("EPS Estimate"))
            result["last_eps_actual"] = self._safe_float(last_row.get("Reported EPS"))

            surprise = self._safe_float(last_row.get("Surprise(%)"))
            if surprise is None:
                surprise = self._safe_float(last_row.get("Surprise (%)"))
            if surprise is not None:
                # yfinance 的 Earnings Dates 表本身以百分比列展示 Surprise(%).
                result["last_eps_surprise_pct"] = surprise

    def _fill_calendar_fallback(
        self,
        ticker: Any,
        result: Dict[str, Any],
        errors: List[str],
    ) -> None:
        """当 earnings_dates 没给出未来日期时，使用 Ticker.calendar 作为兜底。"""
        if result.get("next_earnings_date"):
            return

        try:
            calendar = ticker.calendar or {}
        except Exception as exc:
            errors.append(f"calendar:{self._safe_error(exc)}")
            return

        if not isinstance(calendar, dict):
            return

        raw_dates = calendar.get("Earnings Date")
        candidates: List[pd.Timestamp] = []

        if isinstance(raw_dates, (list, tuple)):
            values = list(raw_dates)
        elif raw_dates is None:
            values = []
        else:
            values = [raw_dates]

        today = datetime.now(timezone.utc).date()
        for value in values:
            ts = self._as_timestamp(value)
            if ts is not None and ts.date() >= today:
                candidates.append(ts)

        if candidates:
            next_ts = min(candidates)
            days = (next_ts.date() - today).days
            result["next_earnings_date"] = next_ts.date().isoformat()
            result["next_earnings_datetime"] = next_ts.isoformat()
            result["days_to_earnings"] = days
            result["event_risk"] = self._event_risk(days)

        if result.get("next_eps_estimate") is None:
            estimate = self._safe_float(calendar.get("Earnings Average"))
            if estimate is not None:
                result["next_eps_estimate"] = estimate

    def _fill_earnings_estimate(
        self,
        ticker: Any,
        result: Dict[str, Any],
        errors: List[str],
    ) -> None:
        try:
            frame = ticker.get_earnings_estimate()
        except Exception as exc:
            errors.append(f"earnings_estimate:{self._safe_error(exc)}")
            return

        row = self._get_row(frame, "0q")
        if row is None:
            return

        result["eps_estimate_current_q"] = self._safe_float(row.get("avg"))
        result["eps_estimate_low"] = self._safe_float(row.get("low"))
        result["eps_estimate_high"] = self._safe_float(row.get("high"))
        result["eps_analyst_count"] = self._safe_int(row.get("numberOfAnalysts"))
        result["eps_year_ago"] = self._safe_float(row.get("yearAgoEps"))

        # 若 Earnings Dates 没有当前财报 EPS 预期，用分析师一致预期补齐。
        if result.get("next_eps_estimate") is None:
            result["next_eps_estimate"] = result.get("eps_estimate_current_q")

    def _fill_eps_revisions(
        self,
        ticker: Any,
        result: Dict[str, Any],
        errors: List[str],
    ) -> None:
        try:
            frame = ticker.get_eps_revisions()
        except Exception as exc:
            errors.append(f"eps_revisions:{self._safe_error(exc)}")
            return

        row = self._get_row(frame, "0q")
        if row is None:
            return

        up_7 = self._safe_int(row.get("upLast7days")) or 0
        down_7 = self._safe_int(row.get("downLast7days")) or 0
        up_30 = self._safe_int(row.get("upLast30days")) or 0
        down_30 = self._safe_int(row.get("downLast30days")) or 0

        result["eps_revision_up_7d"] = up_7
        result["eps_revision_down_7d"] = down_7
        result["eps_revision_up_30d"] = up_30
        result["eps_revision_down_30d"] = down_30
        result["eps_revision_net_30d"] = up_30 - down_30

        if up_30 > down_30:
            direction = "up"
        elif down_30 > up_30:
            direction = "down"
        else:
            direction = "neutral"
        result["eps_revision_direction_30d"] = direction

    def _fill_eps_trend(
        self,
        ticker: Any,
        result: Dict[str, Any],
        errors: List[str],
    ) -> None:
        try:
            frame = ticker.get_eps_trend()
        except Exception as exc:
            errors.append(f"eps_trend:{self._safe_error(exc)}")
            return

        row = self._get_row(frame, "0q")
        if row is None:
            return

        current = self._safe_float(row.get("current"))
        ago_30 = self._safe_float(row.get("30daysAgo"))

        result["eps_trend_current"] = current
        result["eps_trend_30d_ago"] = ago_30

        if current is not None and ago_30 is not None:
            result["eps_trend_change_30d"] = round(current - ago_30, 4)
            if ago_30 != 0:
                result["eps_trend_change_30d_pct"] = round(
                    (current / ago_30 - 1.0) * 100.0,
                    2,
                )

    def _fill_revenue_estimate(
        self,
        ticker: Any,
        result: Dict[str, Any],
        errors: List[str],
    ) -> None:
        try:
            frame = ticker.get_revenue_estimate()
        except Exception as exc:
            errors.append(f"revenue_estimate:{self._safe_error(exc)}")
            return

        row = self._get_row(frame, "0q")
        if row is None:
            return

        result["revenue_estimate_current_q"] = self._safe_float(row.get("avg"))
        result["revenue_estimate_low"] = self._safe_float(row.get("low"))
        result["revenue_estimate_high"] = self._safe_float(row.get("high"))
        result["revenue_analyst_count"] = self._safe_int(row.get("numberOfAnalysts"))
        result["revenue_year_ago"] = self._safe_float(row.get("yearAgoRevenue"))

    @staticmethod
    def _meaningful_field_count(data: Dict[str, Any]) -> int:
        ignored = {
            "symbol",
            "as_of",
            "instrument_type",
            "source",
            "event_risk",
        }
        count = 0
        for key, value in data.items():
            if key in ignored:
                continue
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            count += 1
        return count

    def get_earnings_event(
        self,
        symbol: str,
        *,
        stock_name: str = "",
    ) -> Dict[str, Any]:
        """
        返回统一结构：

        {
          "status": "ok|partial|not_supported|failed",
          "data": {...},
          "source_chain": [...],
          "errors": [...]
        }
        """
        normalized = (symbol or "").strip().upper()

        if not self.enabled:
            return {
                "status": "not_supported",
                "data": {},
                "source_chain": [],
                "errors": ["earnings event pipeline disabled"],
            }

        if not normalized:
            return {
                "status": "failed",
                "data": {},
                "source_chain": [],
                "errors": ["empty symbol"],
            }

        if self._is_index_or_etf(normalized, stock_name):
            return {
                "status": "not_supported",
                "data": {
                    "symbol": normalized,
                    "instrument_type": "index_or_etf",
                    "skip_reason": "company earnings event not applicable to index/ETF",
                },
                "source_chain": [
                    {
                        "provider": "yfinance_earnings_event",
                        "result": "not_supported",
                        "duration_ms": 0,
                    }
                ],
                "errors": [],
            }

        cached = self._get_cached(normalized)
        if cached is not None:
            cached["cache_hit"] = True
            return cached

        started = time.monotonic()
        errors: List[str] = []
        data: Dict[str, Any] = {
            "symbol": normalized,
            "instrument_type": "equity",
            "source": "yfinance",
            "as_of": datetime.now(timezone.utc).isoformat(),
        }

        try:
            import yfinance as yf
            ticker = yf.Ticker(normalized)
        except Exception as exc:
            payload = {
                "status": "failed",
                "data": data,
                "source_chain": [
                    {
                        "provider": "yfinance_earnings_event",
                        "result": "failed",
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    }
                ],
                "errors": [f"init:{self._safe_error(exc)}"],
            }
            self._put_cached(normalized, payload)
            return payload

        # 先用 Earnings Dates，一次请求同时覆盖“未来日期 + 上次实际 EPS/surprise”。
        self._fill_from_earnings_dates(ticker, data, errors)
        self._fill_calendar_fallback(ticker, data, errors)

        # 分析师一致预期与预期修正。
        self._fill_earnings_estimate(ticker, data, errors)
        self._fill_eps_revisions(ticker, data, errors)
        self._fill_eps_trend(ticker, data, errors)
        self._fill_revenue_estimate(ticker, data, errors)

        if "event_risk" not in data:
            data["event_risk"] = self._event_risk(data.get("days_to_earnings"))

        meaningful = self._meaningful_field_count(data)
        has_next_date = bool(data.get("next_earnings_date"))
        has_expectation = any(
            data.get(key) is not None
            for key in (
                "next_eps_estimate",
                "eps_estimate_current_q",
                "revenue_estimate_current_q",
                "eps_revision_direction_30d",
            )
        )

        if meaningful == 0:
            status = "failed" if errors else "not_supported"
        elif has_next_date and has_expectation:
            status = "ok" if not errors else "partial"
        else:
            status = "partial"

        duration_ms = int((time.monotonic() - started) * 1000)
        payload = {
            "status": status,
            "data": data,
            "source_chain": [
                {
                    "provider": "yfinance_earnings_event",
                    "result": status,
                    "duration_ms": duration_ms,
                }
            ],
            "errors": errors,
        }

        self._put_cached(normalized, payload)

        logger.info(
            "[财报事件] %s status=%s next=%s days=%s risk=%s revision30d=%s duration_ms=%s",
            normalized,
            status,
            data.get("next_earnings_date"),
            data.get("days_to_earnings"),
            data.get("event_risk"),
            data.get("eps_revision_direction_30d"),
            duration_ms,
        )
        return payload
