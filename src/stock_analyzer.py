# -*- coding: utf-8 -*-
"""
===================================
趋势交易分析器 - 基于用户交易理念
===================================

交易理念核心原则：
1. 严进策略 - 不追高，追求每笔交易成功率
2. 趋势交易 - MA5>MA10>MA20 多头排列，顺势而为
3. 效率优先 - 关注筹码结构好的股票
4. 买点偏好 - 在 MA5/MA10 附近回踩买入

技术标准：
- 多头排列：MA5 > MA10 > MA20
- 乖离率：(Close - MA5) / MA5 < 5%（不追高）
- 量能形态：缩量回调优先

可靠性约束：
- 指标历史长度不足时明确标记 unavailable，不使用“中性/多头”默认值参与评分。
- MA60 仅在至少 60 根 K 线时提供，禁止使用 MA20 冒充长期均线。
- 综合评分按可用指标权重归一化，并记录覆盖率；覆盖率不足时禁止生成积极买入信号。
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from src.config import get_config
from src.schemas.decision_scale import signal_key_for_score

logger = logging.getLogger(__name__)


class TrendStatus(Enum):
    """趋势状态枚举"""

    STRONG_BULL = "强势多头"
    BULL = "多头排列"
    WEAK_BULL = "弱势多头"
    CONSOLIDATION = "盘整"
    WEAK_BEAR = "弱势空头"
    BEAR = "空头排列"
    STRONG_BEAR = "强势空头"


class VolumeStatus(Enum):
    """量能状态枚举"""

    UNAVAILABLE = "数据不足"
    HEAVY_VOLUME_UP = "放量上涨"
    HEAVY_VOLUME_DOWN = "放量下跌"
    SHRINK_VOLUME_UP = "缩量上涨"
    SHRINK_VOLUME_DOWN = "缩量回调"
    NORMAL = "量能正常"


class BuySignal(Enum):
    """买入信号枚举"""

    STRONG_BUY = "强烈买入"
    BUY = "买入"
    HOLD = "持有"
    WAIT = "观望"
    SELL = "卖出"
    STRONG_SELL = "强烈卖出"


class MACDStatus(Enum):
    """MACD状态枚举"""

    UNAVAILABLE = "数据不足"
    GOLDEN_CROSS_ZERO = "零轴上金叉"
    GOLDEN_CROSS = "金叉"
    BULLISH = "多头"
    NEUTRAL = "中性"
    CROSSING_UP = "上穿零轴"
    CROSSING_DOWN = "下穿零轴"
    BEARISH = "空头"
    DEATH_CROSS = "死叉"


class RSIStatus(Enum):
    """RSI状态枚举"""

    UNAVAILABLE = "数据不足"
    OVERBOUGHT = "超买"
    STRONG_BUY = "强势买入"
    NEUTRAL = "中性"
    WEAK = "弱势"
    OVERSOLD = "超卖"


@dataclass
class TrendAnalysisResult:
    """趋势分析结果。"""

    code: str

    # 趋势判断
    trend_status: TrendStatus = TrendStatus.CONSOLIDATION
    ma_alignment: str = ""
    trend_strength: float = 0.0

    # 均线数据
    ma5: float = 0.0
    ma10: float = 0.0
    ma20: float = 0.0
    ma60: Optional[float] = None
    current_price: float = 0.0

    # 乖离率
    bias_ma5: float = 0.0
    bias_ma10: float = 0.0
    bias_ma20: float = 0.0

    # 量能分析
    volume_status: VolumeStatus = VolumeStatus.UNAVAILABLE
    volume_ratio_5d: float = 0.0
    volume_trend: str = ""

    # 支撑压力
    support_ma5: bool = False
    support_ma10: bool = False
    resistance_levels: List[float] = field(default_factory=list)
    support_levels: List[float] = field(default_factory=list)

    # MACD 指标
    macd_dif: float = 0.0
    macd_dea: float = 0.0
    macd_bar: float = 0.0
    macd_status: MACDStatus = MACDStatus.UNAVAILABLE
    macd_signal: str = ""

    # RSI 指标
    rsi_6: float = 0.0
    rsi_12: float = 0.0
    rsi_24: float = 0.0
    rsi_status: RSIStatus = RSIStatus.UNAVAILABLE
    rsi_signal: str = ""

    # 买入信号
    buy_signal: BuySignal = BuySignal.WAIT
    signal_score: int = 0
    signal_reasons: List[str] = field(default_factory=list)
    risk_factors: List[str] = field(default_factory=list)

    # 评分可解释性
    score_coverage: float = 0.0
    available_score_max: int = 0
    missing_indicator_groups: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """转换为稳定的可序列化字典。"""

        return {
            "code": self.code,
            "trend_status": self.trend_status.value,
            "ma_alignment": self.ma_alignment,
            "trend_strength": self.trend_strength,
            "ma5": self.ma5,
            "ma10": self.ma10,
            "ma20": self.ma20,
            "ma60": self.ma60,
            "current_price": self.current_price,
            "bias_ma5": self.bias_ma5,
            "bias_ma10": self.bias_ma10,
            "bias_ma20": self.bias_ma20,
            "volume_status": self.volume_status.value,
            "volume_ratio_5d": self.volume_ratio_5d,
            "volume_trend": self.volume_trend,
            "support_ma5": self.support_ma5,
            "support_ma10": self.support_ma10,
            "support_levels": list(self.support_levels),
            "resistance_levels": list(self.resistance_levels),
            "buy_signal": self.buy_signal.value,
            "signal_score": self.signal_score,
            "signal_reasons": list(self.signal_reasons),
            "risk_factors": list(self.risk_factors),
            "score_coverage": self.score_coverage,
            "available_score_max": self.available_score_max,
            "missing_indicator_groups": list(self.missing_indicator_groups),
            "macd_dif": self.macd_dif,
            "macd_dea": self.macd_dea,
            "macd_bar": self.macd_bar,
            "macd_status": self.macd_status.value,
            "macd_signal": self.macd_signal,
            "rsi_6": self.rsi_6,
            "rsi_12": self.rsi_12,
            "rsi_24": self.rsi_24,
            "rsi_status": self.rsi_status.value,
            "rsi_signal": self.rsi_signal,
        }


class StockTrendAnalyzer:
    """基于均线、量价、MACD 与 RSI 的确定性趋势分析器。"""

    VOLUME_SHRINK_RATIO = 0.7
    VOLUME_HEAVY_RATIO = 1.5
    MA_SUPPORT_TOLERANCE = 0.02

    MACD_FAST = 12
    MACD_SLOW = 26
    MACD_SIGNAL = 9

    RSI_SHORT = 6
    RSI_MID = 12
    RSI_LONG = 24
    RSI_OVERBOUGHT = 70
    RSI_OVERSOLD = 30

    # 少于 85% 的技术评分权重可用时，只允许生成 watch/sell 方向，禁止因归一化产生虚高买入分。
    MIN_ACTIONABLE_SCORE_COVERAGE = 0.85

    def analyze(self, df: pd.DataFrame, code: str) -> TrendAnalysisResult:
        """分析单只股票趋势并返回确定性结果。"""

        result = TrendAnalysisResult(code=code)

        if df is None or df.empty or len(df) < 20:
            logger.warning("%s 数据不足，无法进行趋势分析", code)
            result.risk_factors.append("数据不足，无法完成分析")
            result.missing_indicator_groups = ["trend", "volume", "macd", "rsi"]
            return result

        df = df.sort_values("date").reset_index(drop=True)
        df = self._calculate_mas(df)
        df = self._calculate_macd(df)
        df = self._calculate_rsi(df)

        latest = df.iloc[-1]
        result.current_price = float(latest["close"])
        result.ma5 = float(latest["MA5"])
        result.ma10 = float(latest["MA10"])
        result.ma20 = float(latest["MA20"])
        ma60_value = latest.get("MA60")
        result.ma60 = float(ma60_value) if pd.notna(ma60_value) else None

        self._analyze_trend(df, result)
        self._calculate_bias(result)
        self._analyze_volume(df, result)
        self._analyze_support_resistance(df, result)
        self._analyze_macd(df, result)
        self._analyze_rsi(df, result)
        self._generate_signal(result)
        return result

    def _calculate_mas(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算均线；历史不足时 MA60 保持 NaN，而不是冒充 MA20。"""

        df = df.copy()
        df["MA5"] = df["close"].rolling(window=5).mean()
        df["MA10"] = df["close"].rolling(window=10).mean()
        df["MA20"] = df["close"].rolling(window=20).mean()
        df["MA60"] = df["close"].rolling(window=60).mean()
        return df

    def _calculate_macd(self, df: pd.DataFrame) -> pd.DataFrame:
        """按标准 12/26/9 计算 MACD。"""

        df = df.copy()
        ema_fast = df["close"].ewm(span=self.MACD_FAST, adjust=False).mean()
        ema_slow = df["close"].ewm(span=self.MACD_SLOW, adjust=False).mean()
        df["MACD_DIF"] = ema_fast - ema_slow
        df["MACD_DEA"] = df["MACD_DIF"].ewm(span=self.MACD_SIGNAL, adjust=False).mean()
        df["MACD_BAR"] = (df["MACD_DIF"] - df["MACD_DEA"]) * 2
        return df

    def _calculate_rsi(self, df: pd.DataFrame) -> pd.DataFrame:
        """按 Wilder EMA/SMMA 口径计算 RSI。"""

        df = df.copy()
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)

        for period in (self.RSI_SHORT, self.RSI_MID, self.RSI_LONG):
            avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            rsi = rsi.replace([np.inf, -np.inf], np.nan).fillna(50)
            df[f"RSI_{period}"] = rsi

        return df

    def _analyze_trend(self, df: pd.DataFrame, result: TrendAnalysisResult) -> None:
        """判断均线排列和趋势强度。"""

        ma5, ma10, ma20 = result.ma5, result.ma10, result.ma20

        if ma5 > ma10 > ma20:
            prev = df.iloc[-5] if len(df) >= 5 else df.iloc[-1]
            prev_spread = (
                (prev["MA5"] - prev["MA20"]) / prev["MA20"] * 100
                if prev["MA20"] > 0
                else 0
            )
            curr_spread = (ma5 - ma20) / ma20 * 100 if ma20 > 0 else 0
            if curr_spread > prev_spread and curr_spread > 5:
                result.trend_status = TrendStatus.STRONG_BULL
                result.ma_alignment = "强势多头排列，均线发散上行"
                result.trend_strength = 90
            else:
                result.trend_status = TrendStatus.BULL
                result.ma_alignment = "多头排列 MA5>MA10>MA20"
                result.trend_strength = 75
        elif ma5 > ma10 and ma10 <= ma20:
            result.trend_status = TrendStatus.WEAK_BULL
            result.ma_alignment = "弱势多头，MA5>MA10 但 MA10≤MA20"
            result.trend_strength = 55
        elif ma5 < ma10 < ma20:
            prev = df.iloc[-5] if len(df) >= 5 else df.iloc[-1]
            prev_spread = (
                (prev["MA20"] - prev["MA5"]) / prev["MA5"] * 100
                if prev["MA5"] > 0
                else 0
            )
            curr_spread = (ma20 - ma5) / ma5 * 100 if ma5 > 0 else 0
            if curr_spread > prev_spread and curr_spread > 5:
                result.trend_status = TrendStatus.STRONG_BEAR
                result.ma_alignment = "强势空头排列，均线发散下行"
                result.trend_strength = 10
            else:
                result.trend_status = TrendStatus.BEAR
                result.ma_alignment = "空头排列 MA5<MA10<MA20"
                result.trend_strength = 25
        elif ma5 < ma10 and ma10 >= ma20:
            result.trend_status = TrendStatus.WEAK_BEAR
            result.ma_alignment = "弱势空头，MA5<MA10 但 MA10≥MA20"
            result.trend_strength = 40
        else:
            result.trend_status = TrendStatus.CONSOLIDATION
            result.ma_alignment = "均线缠绕，趋势不明"
            result.trend_strength = 50

    def _calculate_bias(self, result: TrendAnalysisResult) -> None:
        """计算价格相对 MA5/10/20 的乖离率。"""

        price = result.current_price
        if result.ma5 > 0:
            result.bias_ma5 = (price - result.ma5) / result.ma5 * 100
        if result.ma10 > 0:
            result.bias_ma10 = (price - result.ma10) / result.ma10 * 100
        if result.ma20 > 0:
            result.bias_ma20 = (price - result.ma20) / result.ma20 * 100

    def _analyze_volume(self, df: pd.DataFrame, result: TrendAnalysisResult) -> None:
        """分析最近一日相对前 5 日均量的量价形态。"""

        if len(df) < 6:
            result.volume_status = VolumeStatus.UNAVAILABLE
            result.volume_trend = "数据不足"
            return

        latest = df.iloc[-1]
        previous_volumes = pd.to_numeric(df["volume"].iloc[-6:-1], errors="coerce")
        latest_volume = pd.to_numeric(pd.Series([latest["volume"]]), errors="coerce").iloc[0]
        vol_5d_avg = previous_volumes.mean()

        if not np.isfinite(latest_volume) or not np.isfinite(vol_5d_avg) or vol_5d_avg <= 0:
            result.volume_status = VolumeStatus.UNAVAILABLE
            result.volume_trend = "成交量不可用"
            return

        result.volume_ratio_5d = float(latest_volume) / float(vol_5d_avg)
        prev_close = float(df.iloc[-2]["close"])
        price_change = (float(latest["close"]) - prev_close) / prev_close * 100 if prev_close else 0.0

        if result.volume_ratio_5d >= self.VOLUME_HEAVY_RATIO:
            if price_change > 0:
                result.volume_status = VolumeStatus.HEAVY_VOLUME_UP
                result.volume_trend = "放量上涨，多头力量强劲"
            else:
                result.volume_status = VolumeStatus.HEAVY_VOLUME_DOWN
                result.volume_trend = "放量下跌，注意风险"
        elif result.volume_ratio_5d <= self.VOLUME_SHRINK_RATIO:
            if price_change > 0:
                result.volume_status = VolumeStatus.SHRINK_VOLUME_UP
                result.volume_trend = "缩量上涨，上攻动能不足"
            else:
                result.volume_status = VolumeStatus.SHRINK_VOLUME_DOWN
                result.volume_trend = "缩量回调，洗盘特征明显（好）"
        else:
            result.volume_status = VolumeStatus.NORMAL
            result.volume_trend = "量能正常"

    def _analyze_support_resistance(self, df: pd.DataFrame, result: TrendAnalysisResult) -> None:
        """提取均线支撑和最近 20 日高点压力。"""

        price = result.current_price
        if result.ma5 > 0:
            ma5_distance = abs(price - result.ma5) / result.ma5
            if ma5_distance <= self.MA_SUPPORT_TOLERANCE and price >= result.ma5:
                result.support_ma5 = True
                result.support_levels.append(result.ma5)

        if result.ma10 > 0:
            ma10_distance = abs(price - result.ma10) / result.ma10
            if ma10_distance <= self.MA_SUPPORT_TOLERANCE and price >= result.ma10:
                result.support_ma10 = True
                if result.ma10 not in result.support_levels:
                    result.support_levels.append(result.ma10)

        if result.ma20 > 0 and price >= result.ma20 and result.ma20 not in result.support_levels:
            result.support_levels.append(result.ma20)

        if len(df) >= 20:
            recent_high = float(df["high"].iloc[-20:].max())
            if np.isfinite(recent_high) and recent_high > price:
                result.resistance_levels.append(recent_high)

    def _analyze_macd(self, df: pd.DataFrame, result: TrendAnalysisResult) -> None:
        """分析 MACD；历史不足时明确标记 unavailable。"""

        if len(df) < self.MACD_SLOW:
            result.macd_status = MACDStatus.UNAVAILABLE
            result.macd_signal = "数据不足"
            return

        latest = df.iloc[-1]
        prev = df.iloc[-2]
        result.macd_dif = float(latest["MACD_DIF"])
        result.macd_dea = float(latest["MACD_DEA"])
        result.macd_bar = float(latest["MACD_BAR"])

        prev_dif_dea = float(prev["MACD_DIF"] - prev["MACD_DEA"])
        curr_dif_dea = result.macd_dif - result.macd_dea
        is_golden_cross = prev_dif_dea <= 0 < curr_dif_dea
        is_death_cross = prev_dif_dea >= 0 > curr_dif_dea
        is_crossing_up = float(prev["MACD_DIF"]) <= 0 < result.macd_dif
        is_crossing_down = float(prev["MACD_DIF"]) >= 0 > result.macd_dif

        if is_golden_cross and result.macd_dif > 0:
            result.macd_status = MACDStatus.GOLDEN_CROSS_ZERO
            result.macd_signal = "⭐ 零轴上金叉，强烈买入信号！"
        elif is_crossing_up:
            result.macd_status = MACDStatus.CROSSING_UP
            result.macd_signal = "⚡ DIF上穿零轴，趋势转强"
        elif is_golden_cross:
            result.macd_status = MACDStatus.GOLDEN_CROSS
            result.macd_signal = "✅ 金叉，趋势向上"
        elif is_death_cross:
            result.macd_status = MACDStatus.DEATH_CROSS
            result.macd_signal = "❌ 死叉，趋势向下"
        elif is_crossing_down:
            result.macd_status = MACDStatus.CROSSING_DOWN
            result.macd_signal = "⚠️ DIF下穿零轴，趋势转弱"
        elif result.macd_dif > 0 and result.macd_dea > 0:
            result.macd_status = MACDStatus.BULLISH
            result.macd_signal = "✓ 多头排列，持续上涨"
        elif result.macd_dif < 0 and result.macd_dea < 0:
            result.macd_status = MACDStatus.BEARISH
            result.macd_signal = "⚠ 空头排列，持续下跌"
        else:
            result.macd_status = MACDStatus.NEUTRAL
            result.macd_signal = "MACD 中性区域"

    def _analyze_rsi(self, df: pd.DataFrame, result: TrendAnalysisResult) -> None:
        """分析 RSI；至少需要 RSI_LONG + 1 根收盘价形成完整差分窗口。"""

        if len(df) < self.RSI_LONG + 1:
            result.rsi_status = RSIStatus.UNAVAILABLE
            result.rsi_signal = "数据不足"
            return

        latest = df.iloc[-1]
        result.rsi_6 = float(latest[f"RSI_{self.RSI_SHORT}"])
        result.rsi_12 = float(latest[f"RSI_{self.RSI_MID}"])
        result.rsi_24 = float(latest[f"RSI_{self.RSI_LONG}"])
        rsi_mid = result.rsi_12

        if rsi_mid > self.RSI_OVERBOUGHT:
            result.rsi_status = RSIStatus.OVERBOUGHT
            result.rsi_signal = f"⚠️ RSI超买({rsi_mid:.1f}>70)，短期回调风险高"
        elif rsi_mid > 60:
            result.rsi_status = RSIStatus.STRONG_BUY
            result.rsi_signal = f"✅ RSI强势({rsi_mid:.1f})，多头力量充足"
        elif rsi_mid >= 40:
            result.rsi_status = RSIStatus.NEUTRAL
            result.rsi_signal = f"RSI中性({rsi_mid:.1f})，震荡整理中"
        elif rsi_mid >= self.RSI_OVERSOLD:
            result.rsi_status = RSIStatus.WEAK
            result.rsi_signal = f"⚡ RSI弱势({rsi_mid:.1f})，关注反弹"
        else:
            result.rsi_status = RSIStatus.OVERSOLD
            result.rsi_signal = f"⭐ RSI超卖({rsi_mid:.1f}<30)，反弹机会大"

    def _generate_signal(self, result: TrendAnalysisResult) -> None:
        """按可用指标生成 0-100 归一化评分，并记录指标覆盖率。"""

        raw_score = 0
        available_max = 0
        reasons: List[str] = []
        risks: List[str] = []
        missing: List[str] = []

        def add(points: int, max_points: int, *, available: bool = True) -> None:
            nonlocal raw_score, available_max
            if not available:
                return
            raw_score += int(points)
            available_max += int(max_points)

        trend_scores = {
            TrendStatus.STRONG_BULL: 30,
            TrendStatus.BULL: 26,
            TrendStatus.WEAK_BULL: 18,
            TrendStatus.CONSOLIDATION: 12,
            TrendStatus.WEAK_BEAR: 8,
            TrendStatus.BEAR: 4,
            TrendStatus.STRONG_BEAR: 0,
        }
        add(trend_scores.get(result.trend_status, 12), 30)
        if result.trend_status in {TrendStatus.STRONG_BULL, TrendStatus.BULL}:
            reasons.append(f"✅ {result.trend_status.value}，顺势做多")
        elif result.trend_status in {TrendStatus.BEAR, TrendStatus.STRONG_BEAR}:
            risks.append(f"⚠️ {result.trend_status.value}，不宜做多")

        bias = result.bias_ma5
        bias_available = result.ma5 > 0 and np.isfinite(bias)
        if bias_available:
            base_threshold = get_config().bias_threshold
            trend_strength = result.trend_strength if np.isfinite(result.trend_strength) else 0.0
            strong_trend = result.trend_status == TrendStatus.STRONG_BULL and trend_strength >= 70
            effective_threshold = base_threshold * 1.5 if strong_trend else base_threshold

            if bias < 0:
                if bias > -3:
                    bias_points = 20
                    reasons.append(f"✅ 价格略低于MA5({bias:.1f}%)，回踩买点")
                elif bias > -5:
                    bias_points = 16
                    reasons.append(f"✅ 价格回踩MA5({bias:.1f}%)，观察支撑")
                else:
                    bias_points = 8
                    risks.append(f"⚠️ 乖离率过大({bias:.1f}%)，可能破位")
            elif bias < 2:
                bias_points = 18
                reasons.append(f"✅ 价格贴近MA5({bias:.1f}%)，介入好时机")
            elif bias < base_threshold:
                bias_points = 14
                reasons.append(f"⚡ 价格略高于MA5({bias:.1f}%)，可小仓介入")
            elif bias > effective_threshold:
                bias_points = 4
                risks.append(f"❌ 乖离率过高({bias:.1f}%>{effective_threshold:.1f}%)，严禁追高！")
            elif bias > base_threshold and strong_trend:
                bias_points = 10
                reasons.append(f"⚡ 强势趋势中乖离率偏高({bias:.1f}%)，可轻仓追踪")
            else:
                bias_points = 4
                risks.append(f"❌ 乖离率过高({bias:.1f}%>{base_threshold:.1f}%)，严禁追高！")
            add(bias_points, 20)
        else:
            missing.append("bias")

        volume_scores = {
            VolumeStatus.SHRINK_VOLUME_DOWN: 15,
            VolumeStatus.HEAVY_VOLUME_UP: 12,
            VolumeStatus.NORMAL: 10,
            VolumeStatus.SHRINK_VOLUME_UP: 6,
            VolumeStatus.HEAVY_VOLUME_DOWN: 0,
        }
        volume_available = result.volume_status != VolumeStatus.UNAVAILABLE
        if volume_available:
            add(volume_scores.get(result.volume_status, 8), 15)
            if result.volume_status == VolumeStatus.SHRINK_VOLUME_DOWN:
                reasons.append("✅ 缩量回调，主力洗盘")
            elif result.volume_status == VolumeStatus.HEAVY_VOLUME_DOWN:
                risks.append("⚠️ 放量下跌，注意风险")
        else:
            missing.append("volume")

        support_available = result.ma5 > 0 and result.ma10 > 0
        if support_available:
            support_points = 0
            if result.support_ma5:
                support_points += 5
                reasons.append("✅ MA5支撑有效")
            if result.support_ma10:
                support_points += 5
                reasons.append("✅ MA10支撑有效")
            add(support_points, 10)
        else:
            missing.append("support")

        macd_scores = {
            MACDStatus.GOLDEN_CROSS_ZERO: 15,
            MACDStatus.GOLDEN_CROSS: 12,
            MACDStatus.CROSSING_UP: 10,
            MACDStatus.BULLISH: 8,
            MACDStatus.NEUTRAL: 5,
            MACDStatus.BEARISH: 2,
            MACDStatus.CROSSING_DOWN: 0,
            MACDStatus.DEATH_CROSS: 0,
        }
        macd_available = result.macd_status != MACDStatus.UNAVAILABLE
        if macd_available:
            add(macd_scores.get(result.macd_status, 5), 15)
            if result.macd_status in {MACDStatus.GOLDEN_CROSS_ZERO, MACDStatus.GOLDEN_CROSS}:
                reasons.append(f"✅ {result.macd_signal}")
            elif result.macd_status in {MACDStatus.DEATH_CROSS, MACDStatus.CROSSING_DOWN}:
                risks.append(f"⚠️ {result.macd_signal}")
            elif result.macd_signal:
                reasons.append(result.macd_signal)
        else:
            missing.append("macd")

        rsi_scores = {
            RSIStatus.OVERSOLD: 10,
            RSIStatus.STRONG_BUY: 8,
            RSIStatus.NEUTRAL: 5,
            RSIStatus.WEAK: 3,
            RSIStatus.OVERBOUGHT: 0,
        }
        rsi_available = result.rsi_status != RSIStatus.UNAVAILABLE
        if rsi_available:
            add(rsi_scores.get(result.rsi_status, 5), 10)
            if result.rsi_status in {RSIStatus.OVERSOLD, RSIStatus.STRONG_BUY}:
                reasons.append(f"✅ {result.rsi_signal}")
            elif result.rsi_status == RSIStatus.OVERBOUGHT:
                risks.append(f"⚠️ {result.rsi_signal}")
            elif result.rsi_signal:
                reasons.append(result.rsi_signal)
        else:
            missing.append("rsi")

        coverage = available_max / 100.0
        normalized_score = int(round(raw_score / available_max * 100)) if available_max > 0 else 0
        normalized_score = max(0, min(100, normalized_score))

        if coverage < self.MIN_ACTIONABLE_SCORE_COVERAGE:
            risks.append(f"⚠️ 技术指标覆盖率仅 {coverage:.0%}，不足以生成积极买入信号")
            if normalized_score >= 60:
                normalized_score = 59

        result.signal_score = normalized_score
        result.signal_reasons = reasons
        result.risk_factors = risks
        result.score_coverage = round(coverage, 4)
        result.available_score_max = available_max
        result.missing_indicator_groups = missing

        score_signal = signal_key_for_score(normalized_score)
        if score_signal == "strong_buy" and result.trend_status in {TrendStatus.STRONG_BULL, TrendStatus.BULL}:
            result.buy_signal = BuySignal.STRONG_BUY
        elif score_signal in {"strong_buy", "buy"} and result.trend_status in {
            TrendStatus.STRONG_BULL,
            TrendStatus.BULL,
            TrendStatus.WEAK_BULL,
        }:
            result.buy_signal = BuySignal.BUY
        elif score_signal in {"strong_buy", "buy"} and result.trend_status in {
            TrendStatus.CONSOLIDATION,
            TrendStatus.WEAK_BEAR,
        }:
            result.buy_signal = BuySignal.WAIT
        elif score_signal == "watch":
            result.buy_signal = BuySignal.WAIT
        elif score_signal == "sell" or result.trend_status in {TrendStatus.BEAR, TrendStatus.STRONG_BEAR}:
            result.buy_signal = BuySignal.STRONG_SELL
        else:
            result.buy_signal = BuySignal.SELL

    def format_analysis(self, result: TrendAnalysisResult) -> str:
        """格式化分析结果为文本。"""

        ma60_text = f"{result.ma60:.2f}" if result.ma60 is not None else "N/A（历史不足60根K线）"
        lines = [
            f"=== {result.code} 趋势分析 ===",
            "",
            f"📊 趋势判断: {result.trend_status.value}",
            f"   均线排列: {result.ma_alignment}",
            f"   趋势强度: {result.trend_strength}/100",
            "",
            "📈 均线数据:",
            f"   现价: {result.current_price:.2f}",
            f"   MA5:  {result.ma5:.2f} (乖离 {result.bias_ma5:+.2f}%)",
            f"   MA10: {result.ma10:.2f} (乖离 {result.bias_ma10:+.2f}%)",
            f"   MA20: {result.ma20:.2f} (乖离 {result.bias_ma20:+.2f}%)",
            f"   MA60: {ma60_text}",
            "",
            f"📊 量能分析: {result.volume_status.value}",
            f"   量比(vs5日): {result.volume_ratio_5d:.2f}",
            f"   量能趋势: {result.volume_trend}",
            "",
            f"📈 MACD指标: {result.macd_status.value}",
            f"   DIF: {result.macd_dif:.4f}",
            f"   DEA: {result.macd_dea:.4f}",
            f"   MACD: {result.macd_bar:.4f}",
            f"   信号: {result.macd_signal}",
            "",
            f"📊 RSI指标: {result.rsi_status.value}",
            f"   RSI(6): {result.rsi_6:.1f}",
            f"   RSI(12): {result.rsi_12:.1f}",
            f"   RSI(24): {result.rsi_24:.1f}",
            f"   信号: {result.rsi_signal}",
            "",
            f"🎯 操作建议: {result.buy_signal.value}",
            f"   综合评分: {result.signal_score}/100",
            f"   指标覆盖率: {result.score_coverage:.0%}",
        ]

        if result.signal_reasons:
            lines.extend(["", "✅ 买入理由:"])
            lines.extend(f"   {reason}" for reason in result.signal_reasons)

        if result.risk_factors:
            lines.extend(["", "⚠️ 风险因素:"])
            lines.extend(f"   {risk}" for risk in result.risk_factors)

        return "\n".join(lines)


def analyze_stock(df: pd.DataFrame, code: str) -> TrendAnalysisResult:
    """便捷函数：分析单只股票。"""

    return StockTrendAnalyzer().analyze(df, code)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    dates = pd.date_range(start="2025-01-01", periods=60, freq="D")
    np.random.seed(42)
    base_price = 10.0
    prices = [base_price]
    for _ in range(59):
        change = np.random.randn() * 0.02 + 0.003
        prices.append(prices[-1] * (1 + change))

    frame = pd.DataFrame(
        {
            "date": dates,
            "open": prices,
            "high": [p * (1 + np.random.uniform(0, 0.02)) for p in prices],
            "low": [p * (1 - np.random.uniform(0, 0.02)) for p in prices],
            "close": prices,
            "volume": [np.random.randint(1000000, 5000000) for _ in prices],
        }
    )

    analyzer = StockTrendAnalyzer()
    print(analyzer.format_analysis(analyzer.analyze(frame, "000001")))
