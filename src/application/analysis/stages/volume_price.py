# -*- coding: utf-8 -*-
"""Volume-price feature extraction stage for the analysis pipeline."""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd


class VolumePriceFeaturesStage:
    """Build deterministic volume/price confirmation features from daily bars."""

    @staticmethod
    def run(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """Preserve the historical complete-daily-bar feature contract."""
        if df is None or df.empty:
            return None

        work = df.copy()
        if "close" not in work.columns or "volume" not in work.columns:
            return None

        if "date" in work.columns:
            try:
                work = work.sort_values("date")
            except Exception:
                pass

        work["close"] = pd.to_numeric(work["close"], errors="coerce")
        work["volume"] = pd.to_numeric(work["volume"], errors="coerce")
        work = work.dropna(subset=["close", "volume"])
        work = work[(work["close"] > 0) & (work["volume"] >= 0)]
        if len(work) < 2:
            return None

        latest = work.iloc[-1]
        previous = work.iloc[:-1]
        latest_close = float(latest["close"])
        latest_volume = float(latest["volume"])

        positive_previous_volume = previous.loc[previous["volume"] > 0, "volume"]
        prev5 = positive_previous_volume.tail(5)
        prev20 = positive_previous_volume.tail(20)

        rvol5 = (
            latest_volume / float(prev5.mean())
            if latest_volume > 0 and len(prev5) >= 3 and float(prev5.mean()) > 0
            else None
        )
        rvol20 = (
            latest_volume / float(prev20.mean())
            if latest_volume > 0 and len(prev20) >= 10 and float(prev20.mean()) > 0
            else None
        )

        positive_all_volume = work.loc[work["volume"] > 0, "volume"]
        current5 = positive_all_volume.tail(5)
        current20 = positive_all_volume.tail(20)

        volume_ma5 = float(current5.mean()) if len(current5) >= 3 else None
        volume_ma20 = float(current20.mean()) if len(current20) >= 10 else None

        previous_5_block = (
            positive_all_volume.iloc[-10:-5]
            if len(positive_all_volume) >= 10
            else pd.Series(dtype=float)
        )
        previous_5_avg = (
            float(previous_5_block.mean())
            if len(previous_5_block) >= 3 and float(previous_5_block.mean()) > 0
            else None
        )
        volume_trend_5d_pct = (
            (volume_ma5 / previous_5_avg - 1.0) * 100.0
            if volume_ma5 is not None and previous_5_avg is not None
            else None
        )
        volume_trend_vs20_pct = (
            (volume_ma5 / volume_ma20 - 1.0) * 100.0
            if volume_ma5 is not None and volume_ma20 is not None and volume_ma20 > 0
            else None
        )

        previous_close = float(previous.iloc[-1]["close"]) if not previous.empty else None
        price_change_pct = (
            (latest_close / previous_close - 1.0) * 100.0
            if previous_close is not None and previous_close > 0
            else None
        )

        volume_reference = rvol20 if rvol20 is not None else rvol5
        if volume_reference is None:
            volume_regime = "数据不足"
        elif volume_reference >= 1.50:
            volume_regime = "显著放量"
        elif volume_reference >= 1.20:
            volume_regime = "温和放量"
        elif volume_reference >= 0.80:
            volume_regime = "正常量能"
        else:
            volume_regime = "缩量"

        price_volume_signal = "中性"
        if price_change_pct is not None and volume_reference is not None:
            if price_change_pct > 0.20 and volume_reference >= 1.20:
                price_volume_signal = "上涨放量-多头确认增强"
            elif price_change_pct > 0.20 and volume_reference < 0.80:
                price_volume_signal = "上涨缩量-上涨确认不足"
            elif price_change_pct < -0.20 and volume_reference >= 1.20:
                price_volume_signal = "下跌放量-空头确认增强"
            elif price_change_pct < -0.20 and volume_reference < 0.80:
                price_volume_signal = "下跌缩量-下跌确认有限"
            elif abs(price_change_pct) <= 0.20:
                price_volume_signal = "价格横盘-量能作为突破预警"

        trade_date = latest.get("date")
        if hasattr(trade_date, "isoformat"):
            try:
                trade_date = trade_date.isoformat()
            except Exception:
                trade_date = str(trade_date)
        elif trade_date is not None:
            trade_date = str(trade_date)

        return {
            "trade_date": trade_date,
            "rvol5": round(rvol5, 2) if rvol5 is not None else None,
            "rvol20": round(rvol20, 2) if rvol20 is not None else None,
            "volume_ma5": round(volume_ma5, 0) if volume_ma5 is not None else None,
            "volume_ma20": round(volume_ma20, 0) if volume_ma20 is not None else None,
            "volume_trend_5d_pct": (
                round(volume_trend_5d_pct, 2)
                if volume_trend_5d_pct is not None
                else None
            ),
            "volume_trend_vs20_pct": (
                round(volume_trend_vs20_pct, 2)
                if volume_trend_vs20_pct is not None
                else None
            ),
            "dollar_volume_proxy": round(latest_close * latest_volume, 0),
            "price_change_pct": (
                round(price_change_pct, 2)
                if price_change_pct is not None
                else None
            ),
            "volume_regime": volume_regime,
            "price_volume_signal": price_volume_signal,
            "source": "complete_daily_bars",
        }
