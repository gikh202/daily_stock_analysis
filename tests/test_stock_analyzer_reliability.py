# -*- coding: utf-8 -*-
"""Regression coverage for deterministic technical-analysis reliability guards."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.stock_analyzer import (
    BuySignal,
    MACDStatus,
    RSIStatus,
    StockTrendAnalyzer,
    VolumeStatus,
)


def _frame(rows: int, *, volume: float | None = None) -> pd.DataFrame:
    close = np.linspace(100.0, 120.0, rows)
    if volume is None:
        volumes = np.linspace(1_000_000.0, 1_500_000.0, rows)
    else:
        volumes = np.full(rows, volume, dtype=float)
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=rows, freq="D"),
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": volumes,
        }
    )


def test_short_history_does_not_award_default_macd_or_rsi_points(monkeypatch) -> None:
    class Config:
        bias_threshold = 5.0

    monkeypatch.setattr("src.stock_analyzer.get_config", lambda: Config())

    result = StockTrendAnalyzer().analyze(_frame(20), "AAPL")

    assert result.macd_status is MACDStatus.UNAVAILABLE
    assert result.rsi_status is RSIStatus.UNAVAILABLE
    assert result.score_coverage == 0.75
    assert result.available_score_max == 75
    assert {"macd", "rsi"}.issubset(result.missing_indicator_groups)
    assert result.signal_score <= 59
    assert result.buy_signal is BuySignal.WAIT


def test_ma60_is_missing_until_sixty_real_bars(monkeypatch) -> None:
    class Config:
        bias_threshold = 5.0

    monkeypatch.setattr("src.stock_analyzer.get_config", lambda: Config())
    analyzer = StockTrendAnalyzer()

    short_result = analyzer.analyze(_frame(30), "AAPL")
    long_result = analyzer.analyze(_frame(60), "AAPL")

    assert short_result.ma60 is None
    assert long_result.ma60 is not None
    assert long_result.ma60 != long_result.ma20


def test_support_and_resistance_levels_are_serialized(monkeypatch) -> None:
    class Config:
        bias_threshold = 5.0

    monkeypatch.setattr("src.stock_analyzer.get_config", lambda: Config())

    result = StockTrendAnalyzer().analyze(_frame(60), "AAPL")
    payload = result.to_dict()

    assert payload["support_levels"] == result.support_levels
    assert payload["resistance_levels"] == result.resistance_levels
    assert "score_coverage" in payload
    assert "missing_indicator_groups" in payload


def test_zero_volume_is_unavailable_instead_of_neutral_points(monkeypatch) -> None:
    class Config:
        bias_threshold = 5.0

    monkeypatch.setattr("src.stock_analyzer.get_config", lambda: Config())

    result = StockTrendAnalyzer().analyze(_frame(30, volume=0.0), "AAPL")

    assert result.volume_status is VolumeStatus.UNAVAILABLE
    assert "volume" in result.missing_indicator_groups
    assert result.available_score_max == 85


def test_mixed_sign_macd_is_neutral_not_bullish() -> None:
    analyzer = StockTrendAnalyzer()
    frame = _frame(26)
    frame = analyzer._calculate_macd(frame)
    frame.loc[frame.index[-2], "MACD_DIF"] = 0.2
    frame.loc[frame.index[-2], "MACD_DEA"] = 0.1
    frame.loc[frame.index[-1], "MACD_DIF"] = 0.2
    frame.loc[frame.index[-1], "MACD_DEA"] = -0.1
    frame.loc[frame.index[-1], "MACD_BAR"] = 0.6

    result = analyzer.analyze(_frame(26), "AAPL")
    analyzer._analyze_macd(frame, result)

    assert result.macd_status is MACDStatus.NEUTRAL
    assert "中性" in result.macd_signal
