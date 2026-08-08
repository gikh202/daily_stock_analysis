# -*- coding: utf-8 -*-
"""
Structured US market-regime adapter.

Uses SPY/QQQ trend, VIX and US 10Y yield (^TNX) to build a coarse
risk_on / neutral / risk_off context. This module never changes a stock score.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class MarketRegimeAdapter:
    """Fetch and cache a compact US market-regime snapshot."""

    SYMBOLS = ("SPY", "QQQ", "^VIX", "^TNX")

    def __init__(self) -> None:
        self.enabled = self._env_bool("MARKET_REGIME_ENABLED", True)
        self.cache_ttl_seconds = max(
            60, self._env_int("MARKET_REGIME_CACHE_TTL_SECONDS", 900)
        )
        self.download_timeout_seconds = max(
            2, self._env_int("MARKET_REGIME_HTTP_TIMEOUT_SECONDS", 8)
        )
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_at = 0.0
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
        # Defensive normalization for Yahoo endpoint/version differences.
        return value / 10.0 if value > 20 else value

    @staticmethod
    def _pct_change(latest: Optional[float], prior: Optional[float]) -> Optional[float]:
        if latest is None or prior in (None, 0):
            return None
        return round((latest / prior - 1.0) * 100.0, 2)

    def _snapshot_from_series(self, series: pd.Series) -> Dict[str, Any]:
        if series is None or series.empty:
            return {"status": "unavailable"}

        latest = self._safe_float(series.iloc[-1])
        prior_5 = self._safe_float(series.iloc[-6]) if len(series) >= 6 else None
        ma10 = self._safe_float(series.tail(10).mean()) if len(series) >= 10 else None
        ma20 = self._safe_float(series.tail(20).mean()) if len(series) >= 20 else None
        try:
            trade_date = pd.Timestamp(series.index[-1]).date().isoformat()
        except Exception:
            trade_date = None

        return {
            "status": "available",
            "trade_date": trade_date,
            "close": round(latest, 4) if latest is not None else None,
            "ma10": round(ma10, 4) if ma10 is not None else None,
            "ma20": round(ma20, 4) if ma20 is not None else None,
            "change_5d_pct": self._pct_change(latest, prior_5),
            "above_ma20": (
                latest > ma20
                if latest is not None and ma20 is not None
                else None
            ),
        }

    def _classify(self, components: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
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
                        (normalized_yield - prior_yield) * 100.0, 1
                    )

        if len(risk_off_reasons) >= 2:
            regime = "risk_off"
        elif len(risk_on_reasons) >= 2 and not risk_off_reasons:
            regime = "risk_on"
        else:
            regime = "neutral"

        available_count = sum(
            1
            for key in self.SYMBOLS
            if components.get(key, {}).get("status") == "available"
        )
        confidence = "high" if available_count == 4 else (
            "medium" if available_count >= 3 else "low"
        )

        return {
            "regime": regime,
            "confidence": confidence,
            "risk_on_reasons": risk_on_reasons,
            "risk_off_reasons": risk_off_reasons,
            "available_components": available_count,
        }

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
                import yfinance as yf

                frame = yf.download(
                    list(self.SYMBOLS),
                    period="3mo",
                    interval="1d",
                    auto_adjust=False,
                    progress=False,
                    threads=True,
                    timeout=self.download_timeout_seconds,
                )

                components: Dict[str, Dict[str, Any]] = {}
                for symbol in self.SYMBOLS:
                    components[symbol] = self._snapshot_from_series(
                        self._extract_close(frame, symbol)
                    )

                classification = self._classify(components)
                available_count = classification["available_components"]
                status = "ok" if available_count == 4 else (
                    "partial" if available_count > 0 else "failed"
                )

                payload: Dict[str, Any] = {
                    "status": status,
                    "source": "yfinance",
                    "as_of": datetime.now(timezone.utc).isoformat(),
                    "regime": classification["regime"],
                    "confidence": classification["confidence"],
                    "risk_on_reasons": classification["risk_on_reasons"],
                    "risk_off_reasons": classification["risk_off_reasons"],
                    "components": components,
                    "policy": (
                        "Market regime is context only; do not mechanically "
                        "change score or force buy/sell."
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
                    "components": {},
                    "reason": f"{type(exc).__name__}: {str(exc)[:200]}",
                    "duration_ms": int((time.monotonic() - started) * 1000),
                }

            self._cache = dict(payload)
            self._cache_at = time.time()

            logger.info(
                "[MarketRegime] status=%s regime=%s confidence=%s duration_ms=%s",
                payload.get("status"),
                payload.get("regime"),
                payload.get("confidence"),
                payload.get("duration_ms"),
            )
            return dict(payload)
