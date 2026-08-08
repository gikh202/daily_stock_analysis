# -*- coding: utf-8 -*-
"""
Structured US market context for prediction.

Provides:
1. Market Regime: SPY / QQQ / VIX / US 10Y
2. Market Breadth proxy:
   - RSP (S&P 500 equal-weight) vs SPY
   - QQQE (Nasdaq-100 equal-weight) vs QQQ
3. Stock Relative Strength:
   - 5D / 10D / 20D / 60D absolute return
   - excess return vs SPY / QQQ
   - recent realized volatility

This module only produces structured context. It must not mechanically alter
buy/sell scores.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class MarketRegimeAdapter:
    """Fetch and cache US market and stock-relative prediction context."""

    CORE_SYMBOLS = ("SPY", "QQQ", "^VIX", "^TNX")
    BREADTH_SYMBOLS = ("RSP", "QQQE")
    SYMBOLS = CORE_SYMBOLS + BREADTH_SYMBOLS
    RETURN_HORIZONS = (5, 10, 20, 60)

    def __init__(self) -> None:
        self.enabled = self._env_bool("MARKET_REGIME_ENABLED", True)
        self.prediction_context_enabled = self._env_bool(
            "PREDICTION_CONTEXT_ENABLED",
            True,
        )
        self.cache_ttl_seconds = max(
            60,
            self._env_int("MARKET_REGIME_CACHE_TTL_SECONDS", 900),
        )
        self.stock_cache_ttl_seconds = max(
            60,
            self._env_int("PREDICTION_CONTEXT_CACHE_TTL_SECONDS", 900),
        )
        self.download_timeout_seconds = max(
            2,
            self._env_int("MARKET_REGIME_HTTP_TIMEOUT_SECONDS", 8),
        )

        self._cache: Optional[Dict[str, Any]] = None
        self._cache_at = 0.0
        self._stock_cache: Dict[str, Dict[str, Any]] = {}
        self._stock_cache_at: Dict[str, float] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return str(raw).strip().lower() not in {"0", "false", "no", "off"}

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
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        return str(symbol or "").strip().upper()

    @staticmethod
    def _extract_close(frame: pd.DataFrame, symbol: str) -> pd.Series:
        if frame is None or frame.empty:
            return pd.Series(dtype="float64")

        series = None
        if isinstance(frame.columns, pd.MultiIndex):
            level0 = frame.columns.get_level_values(0)
            level1 = frame.columns.get_level_values(1)

            if "Close" in level0:
                close_block = frame["Close"]
                if isinstance(close_block, pd.DataFrame) and symbol in close_block.columns:
                    series = close_block[symbol]
                elif isinstance(close_block, pd.Series):
                    series = close_block
            elif symbol in level0:
                symbol_block = frame[symbol]
                if isinstance(symbol_block, pd.DataFrame) and "Close" in symbol_block.columns:
                    series = symbol_block["Close"]
            elif "Close" in level1:
                try:
                    series = frame[(symbol, "Close")]
                except Exception:
                    series = None
        elif "Close" in frame.columns:
            series = frame["Close"]

        if series is None:
            return pd.Series(dtype="float64")
        return pd.to_numeric(series, errors="coerce").dropna()

    @staticmethod
    def _normalize_tnx_yield(value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        return value / 10.0 if value > 20 else value

    @staticmethod
    def _pct_change(latest: Optional[float], prior: Optional[float]) -> Optional[float]:
        if latest is None or prior in (None, 0):
            return None
        return round((latest / prior - 1.0) * 100.0, 2)

    @classmethod
    def _series_return(cls, series: pd.Series, days: int) -> Optional[float]:
        if series is None or len(series) < days + 1:
            return None
        latest = cls._safe_float(series.iloc[-1])
        prior = cls._safe_float(series.iloc[-(days + 1)])
        return cls._pct_change(latest, prior)

    @classmethod
    def _realized_volatility_20d(cls, series: pd.Series) -> Optional[float]:
        if series is None or len(series) < 21:
            return None
        returns = pd.to_numeric(series, errors="coerce").pct_change().dropna().tail(20)
        if len(returns) < 10:
            return None
        value = returns.std(ddof=1) * (252 ** 0.5) * 100.0
        return round(float(value), 2)

    def _snapshot_from_series(self, series: pd.Series) -> Dict[str, Any]:
        if series is None or series.empty:
            return {"status": "unavailable"}

        latest = self._safe_float(series.iloc[-1])
        ma10 = self._safe_float(series.tail(10).mean()) if len(series) >= 10 else None
        ma20 = self._safe_float(series.tail(20).mean()) if len(series) >= 20 else None
        ma50 = self._safe_float(series.tail(50).mean()) if len(series) >= 50 else None

        try:
            trade_date = pd.Timestamp(series.index[-1]).date().isoformat()
        except Exception:
            trade_date = None

        payload: Dict[str, Any] = {
            "status": "available",
            "trade_date": trade_date,
            "close": round(latest, 4) if latest is not None else None,
            "ma10": round(ma10, 4) if ma10 is not None else None,
            "ma20": round(ma20, 4) if ma20 is not None else None,
            "ma50": round(ma50, 4) if ma50 is not None else None,
            "above_ma20": (
                latest > ma20
                if latest is not None and ma20 is not None
                else None
            ),
            "above_ma50": (
                latest > ma50
                if latest is not None and ma50 is not None
                else None
            ),
            "realized_vol_20d_pct": self._realized_volatility_20d(series),
        }

        for days in self.RETURN_HORIZONS:
            payload[f"change_{days}d_pct"] = self._series_return(series, days)

        return payload

    @staticmethod
    def _excess(
        target_return: Optional[float],
        benchmark_return: Optional[float],
    ) -> Optional[float]:
        if target_return is None or benchmark_return is None:
            return None
        return round(target_return - benchmark_return, 2)

    def _classify_breadth(
        self,
        components: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Classify market breadth using equal-weight-vs-cap-weight proxies."""
        rsp = components.get("RSP", {})
        spy = components.get("SPY", {})
        qqqe = components.get("QQQE", {})
        qqq = components.get("QQQ", {})

        rsp_excess_20 = self._excess(
            self._safe_float(rsp.get("change_20d_pct")),
            self._safe_float(spy.get("change_20d_pct")),
        )
        qqqe_excess_20 = self._excess(
            self._safe_float(qqqe.get("change_20d_pct")),
            self._safe_float(qqq.get("change_20d_pct")),
        )

        reasons = []
        broad_votes = 0
        narrow_votes = 0
        available_pairs = 0

        if rsp.get("status") == "available" and spy.get("status") == "available":
            available_pairs += 1
            if rsp.get("above_ma20") is True and rsp_excess_20 is not None and rsp_excess_20 >= 0:
                broad_votes += 1
                reasons.append(
                    f"RSP位于MA20上方且20日相对SPY超额{rsp_excess_20:+.2f}%"
                )
            elif rsp.get("above_ma20") is False and rsp_excess_20 is not None and rsp_excess_20 < 0:
                narrow_votes += 1
                reasons.append(
                    f"RSP位于MA20下方且20日落后SPY{abs(rsp_excess_20):.2f}%"
                )

        if qqqe.get("status") == "available" and qqq.get("status") == "available":
            available_pairs += 1
            if qqqe.get("above_ma20") is True and qqqe_excess_20 is not None and qqqe_excess_20 >= 0:
                broad_votes += 1
                reasons.append(
                    f"QQQE位于MA20上方且20日相对QQQ超额{qqqe_excess_20:+.2f}%"
                )
            elif qqqe.get("above_ma20") is False and qqqe_excess_20 is not None and qqqe_excess_20 < 0:
                narrow_votes += 1
                reasons.append(
                    f"QQQE位于MA20下方且20日落后QQQ{abs(qqqe_excess_20):.2f}%"
                )

        if available_pairs == 0:
            breadth = "unknown"
            status = "unavailable"
        elif broad_votes >= 2:
            breadth = "broad"
            status = "available"
        elif narrow_votes >= 2:
            breadth = "narrow"
            status = "available"
        else:
            breadth = "neutral"
            status = "available"

        return {
            "status": status,
            "breadth": breadth,
            "method": "equal_weight_proxy",
            "rsp_vs_spy_20d_pct": rsp_excess_20,
            "qqqe_vs_qqq_20d_pct": qqqe_excess_20,
            "reasons": reasons,
            "note": (
                "Breadth is a proxy based on equal-weight ETFs versus cap-weight "
                "indexes; it is not an advance/decline dataset."
            ),
        }

    def _classify_regime(
        self,
        components: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        risk_on_reasons = []
        risk_off_reasons = []

        for symbol in ("SPY", "QQQ"):
            block = components.get(symbol, {})
            above = block.get("above_ma20")
            if above is True:
                risk_on_reasons.append(f"{symbol}位于MA20上方")
            elif above is False:
                risk_off_reasons.append(f"{symbol}位于MA20下方")

        vix = components.get("^VIX", {})
        vix_level = self._safe_float(vix.get("close"))
        vix_change = self._safe_float(vix.get("change_5d_pct"))
        if vix_level is not None:
            if vix_level <= 18:
                risk_on_reasons.append(f"VIX={vix_level:.2f}处于低波动区")
            elif vix_level >= 25:
                risk_off_reasons.append(f"VIX={vix_level:.2f}处于高波动区")
        if vix_change is not None and vix_change >= 20:
            risk_off_reasons.append(f"VIX近5日上升{vix_change:.1f}%")

        tnx = components.get("^TNX", {})
        tnx_close_raw = self._safe_float(tnx.get("close"))
        tnx_change_pct = self._safe_float(tnx.get("change_5d_pct"))
        if tnx_close_raw is not None:
            normalized_yield = self._normalize_tnx_yield(tnx_close_raw)
            tnx["yield_pct"] = (
                round(normalized_yield, 3)
                if normalized_yield is not None
                else None
            )
            if normalized_yield is not None and tnx_change_pct is not None:
                divisor = 1.0 + tnx_change_pct / 100.0
                if divisor:
                    prior_yield = normalized_yield / divisor
                    tnx["change_5d_bp"] = round(
                        (normalized_yield - prior_yield) * 100.0,
                        1,
                    )

        if len(risk_off_reasons) >= 2:
            regime = "risk_off"
        elif len(risk_on_reasons) >= 2 and not risk_off_reasons:
            regime = "risk_on"
        else:
            regime = "neutral"

        available_core = sum(
            1
            for key in self.CORE_SYMBOLS
            if components.get(key, {}).get("status") == "available"
        )
        confidence = (
            "high"
            if available_core == len(self.CORE_SYMBOLS)
            else "medium"
            if available_core >= 3
            else "low"
        )

        return {
            "regime": regime,
            "confidence": confidence,
            "risk_on_reasons": risk_on_reasons,
            "risk_off_reasons": risk_off_reasons,
            "available_core_components": available_core,
        }

    def _download(
        self,
        symbols: Iterable[str],
        *,
        period: str,
    ) -> pd.DataFrame:
        import yfinance as yf

        return yf.download(
            list(symbols),
            period=period,
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=True,
            timeout=self.download_timeout_seconds,
        )

    def get_us_market_regime(self) -> Dict[str, Any]:
        if not self.enabled:
            return {
                "status": "disabled",
                "regime": "unknown",
                "source": "yfinance",
                "reason": "feature_disabled",
            }

        now = time.time()
        with self._lock:
            if self._cache is not None and now - self._cache_at <= self.cache_ttl_seconds:
                cached = dict(self._cache)
                cached["cache_hit"] = True
                return cached

            started = time.monotonic()
            try:
                frame = self._download(self.SYMBOLS, period="6mo")

                components: Dict[str, Dict[str, Any]] = {}
                for symbol in self.SYMBOLS:
                    components[symbol] = self._snapshot_from_series(
                        self._extract_close(frame, symbol)
                    )

                regime = self._classify_regime(components)
                breadth = self._classify_breadth(components)

                available_core = regime["available_core_components"]
                status = (
                    "ok"
                    if available_core == len(self.CORE_SYMBOLS)
                    else "partial"
                    if available_core > 0
                    else "failed"
                )

                payload: Dict[str, Any] = {
                    "status": status,
                    "source": "yfinance",
                    "as_of": datetime.now(timezone.utc).isoformat(),
                    "regime": regime["regime"],
                    "confidence": regime["confidence"],
                    "risk_on_reasons": regime["risk_on_reasons"],
                    "risk_off_reasons": regime["risk_off_reasons"],
                    "market_breadth": breadth,
                    "components": components,
                    "policy": (
                        "Market regime and breadth are context/risk modifiers only; "
                        "they must not mechanically change stock score or force "
                        "buy/sell."
                    ),
                    "duration_ms": int((time.monotonic() - started) * 1000),
                }
            except Exception as exc:
                payload = {
                    "status": "failed",
                    "source": "yfinance",
                    "as_of": datetime.now(timezone.utc).isoformat(),
                    "regime": "unknown",
                    "confidence": "low",
                    "market_breadth": {
                        "status": "unavailable",
                        "breadth": "unknown",
                    },
                    "components": {},
                    "reason": f"{type(exc).__name__}: {str(exc)[:200]}",
                    "duration_ms": int((time.monotonic() - started) * 1000),
                }

            self._cache = dict(payload)
            self._cache_at = time.time()

            logger.info(
                "[MarketRegime] status=%s regime=%s confidence=%s breadth=%s duration_ms=%s",
                payload.get("status"),
                payload.get("regime"),
                payload.get("confidence"),
                (
                    payload.get("market_breadth", {}).get("breadth")
                    if isinstance(payload.get("market_breadth"), dict)
                    else "unknown"
                ),
                payload.get("duration_ms"),
            )
            return dict(payload)

    def _build_stock_prediction_context_from_frame(
        self,
        *,
        symbol: str,
        frame: pd.DataFrame,
        market_regime: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized = self._normalize_symbol(symbol)
        target = self._extract_close(frame, normalized)
        spy = self._extract_close(frame, "SPY")
        qqq = self._extract_close(frame, "QQQ")

        if target.empty:
            raise RuntimeError(f"no target history for {normalized}")

        horizons: Dict[str, Dict[str, Any]] = {}
        for days in self.RETURN_HORIZONS:
            target_ret = self._series_return(target, days)
            spy_ret = self._series_return(spy, days)
            qqq_ret = self._series_return(qqq, days)
            horizons[f"{days}d"] = {
                "target_return_pct": target_ret,
                "spy_return_pct": spy_ret,
                "qqq_return_pct": qqq_ret,
                "excess_vs_spy_pct": self._excess(target_ret, spy_ret),
                "excess_vs_qqq_pct": self._excess(target_ret, qqq_ret),
            }

        rs20 = horizons.get("20d", {})
        rs60 = horizons.get("60d", {})
        rs20_spy = self._safe_float(rs20.get("excess_vs_spy_pct"))
        rs60_spy = self._safe_float(rs60.get("excess_vs_spy_pct"))

        if (
            rs20_spy is not None
            and rs60_spy is not None
            and rs20_spy > 0
            and rs60_spy > 0
        ):
            rs_state = "outperform"
        elif (
            rs20_spy is not None
            and rs60_spy is not None
            and rs20_spy < 0
            and rs60_spy < 0
        ):
            rs_state = "underperform"
        else:
            rs_state = "mixed"

        if market_regime is None:
            market_regime = self.get_us_market_regime()

        return {
            "status": "ok",
            "symbol": normalized,
            "source": "yfinance",
            "as_of": datetime.now(timezone.utc).isoformat(),
            "primary_benchmark": (
                "QQQ" if normalized in {"QQQ", "QQQM"} else "SPY"
            ),
            "horizons": horizons,
            "relative_strength_state": rs_state,
            "realized_vol_20d_pct": self._realized_volatility_20d(target),
            "market_breadth": (
                dict(market_regime.get("market_breadth", {}))
                if isinstance(market_regime, dict)
                and isinstance(market_regime.get("market_breadth"), dict)
                else {}
            ),
            "policy": (
                "Relative strength and breadth are forecasting evidence only. "
                "They do not mechanically set action or score."
            ),
        }

    def prefetch_stock_prediction_contexts(
        self,
        symbols: Iterable[str],
    ) -> Dict[str, Dict[str, Any]]:
        """Fetch all target relative-strength contexts in one YFinance batch.

        This keeps the comparison timestamp consistent across all stocks and
        avoids one extra HTTP request per stock.
        """
        normalized_symbols = []
        for symbol in symbols or []:
            normalized = self._normalize_symbol(symbol)
            if normalized and normalized not in normalized_symbols:
                normalized_symbols.append(normalized)

        if not self.prediction_context_enabled or not normalized_symbols:
            return {}

        started = time.monotonic()
        market_regime = self.get_us_market_regime()

        try:
            download_symbols = list(dict.fromkeys(
                normalized_symbols + ["SPY", "QQQ"]
            ))
            frame = self._download(download_symbols, period="6mo")
        except Exception as exc:
            logger.warning(
                "[PredictionContext] batch prefetch failed before parsing: %s",
                exc,
            )
            return {}

        output: Dict[str, Dict[str, Any]] = {}
        for normalized in normalized_symbols:
            try:
                payload = self._build_stock_prediction_context_from_frame(
                    symbol=normalized,
                    frame=frame,
                    market_regime=market_regime,
                )
                payload["duration_ms"] = int(
                    (time.monotonic() - started) * 1000
                )
            except Exception as exc:
                payload = {
                    "status": "failed",
                    "symbol": normalized,
                    "source": "yfinance",
                    "as_of": datetime.now(timezone.utc).isoformat(),
                    "relative_strength_state": "unknown",
                    "horizons": {},
                    "reason": f"{type(exc).__name__}: {str(exc)[:200]}",
                    "duration_ms": int(
                        (time.monotonic() - started) * 1000
                    ),
                }

            output[normalized] = payload
            with self._lock:
                self._stock_cache[normalized] = dict(payload)
                self._stock_cache_at[normalized] = time.time()

        logger.info(
            "[PredictionContext] batch_prefetch symbols=%s success=%s failed=%s duration_ms=%s",
            len(normalized_symbols),
            sum(1 for item in output.values() if item.get("status") == "ok"),
            sum(1 for item in output.values() if item.get("status") != "ok"),
            int((time.monotonic() - started) * 1000),
        )
        return output

    def get_stock_prediction_context(self, symbol: str) -> Dict[str, Any]:
        """Return target-specific relative-strength context for forecasting."""
        normalized = self._normalize_symbol(symbol)

        if not self.prediction_context_enabled:
            return {
                "status": "disabled",
                "symbol": normalized,
                "source": "yfinance",
                "reason": "feature_disabled",
            }
        if not normalized:
            return {
                "status": "failed",
                "symbol": normalized,
                "source": "yfinance",
                "reason": "missing_symbol",
            }

        now = time.time()
        with self._lock:
            cached = self._stock_cache.get(normalized)
            cached_at = self._stock_cache_at.get(normalized, 0.0)
            if (
                cached is not None
                and now - cached_at <= self.stock_cache_ttl_seconds
            ):
                payload = dict(cached)
                payload["cache_hit"] = True
                return payload

        started = time.monotonic()
        try:
            frame = self._download(
                list(dict.fromkeys([normalized, "SPY", "QQQ"])),
                period="6mo",
            )
            payload = self._build_stock_prediction_context_from_frame(
                symbol=normalized,
                frame=frame,
                market_regime=self.get_us_market_regime(),
            )
            payload["duration_ms"] = int(
                (time.monotonic() - started) * 1000
            )
        except Exception as exc:
            payload = {
                "status": "failed",
                "symbol": normalized,
                "source": "yfinance",
                "as_of": datetime.now(timezone.utc).isoformat(),
                "relative_strength_state": "unknown",
                "horizons": {},
                "reason": f"{type(exc).__name__}: {str(exc)[:200]}",
                "duration_ms": int(
                    (time.monotonic() - started) * 1000
                ),
            }

        with self._lock:
            self._stock_cache[normalized] = dict(payload)
            self._stock_cache_at[normalized] = time.time()

        logger.info(
            "[PredictionContext] %s status=%s rs=%s vol20=%s duration_ms=%s",
            normalized,
            payload.get("status"),
            payload.get("relative_strength_state"),
            payload.get("realized_vol_20d_pct"),
            payload.get("duration_ms"),
        )
        return dict(payload)

