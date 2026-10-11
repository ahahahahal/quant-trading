"""
新增策略 - 参考freqtrade/vnpy/qlib
1. 动量反转策略 (Momentum Reversal)
2. 配对交易策略 (Pair Trading)  
3. ML选股策略 (LightGBM Stock Selection)
4. 趋势跟踪策略 (Turtle Trading)
5. 资金流向策略 (Money Flow)
"""
import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from strategy.engine import BaseStrategy, Signal, TradeSignal, STRATEGY_REGISTRY


class MomentumReversalStrategy(BaseStrategy):
    """
    动量反转策略 (参考学术论文: Jegadeesh & Titman)
    - 买入: 近期超跌 + 远期有正动量 (反转+动量)
    - 卖出: 近期过热 + 动量衰减
    """

    def __init__(self, short_lookback: int = 5, long_lookback: int = 60):
        super().__init__(
            name="momentum_reversal",
            params={"short": short_lookback, "long": long_lookback}
        )
        self.short_w = short_lookback
        self.long_w = long_lookback

    def generate_signal(self, df: pd.DataFrame, idx: int) -> Signal:
        if idx < self.long_w:
            return Signal.HOLD

        row = df.iloc[idx]
        ret_short = row.get(f"RET{self.short_w}", row.get("RET5", 0))
        ret_long = row.get(f"RET{self.long_w}", row.get("RET60", 0))
        rsi = row.get("RSI6", row.get("RSI14", 50))

        # 超跌反转: 短期大跌 + 长期仍有正趋势
        if ret_short < -0.08 and ret_long > 0 and rsi < 30:
            return Signal.BUY

        # 过热卖出: 短期大涨 + RSI超买
        if ret_short > 0.10 and rsi > 80:
            return Signal.SELL

        # 动量衰减: 长期动量转负
        if ret_long < -0.05 and ret_short < 0:
            return Signal.SELL

        return Signal.HOLD

    def _calc_score(self, df, idx, sig):
        row = df.iloc[idx]
        rsi = row.get("RSI6", 50)
        if sig == Signal.BUY:
            return min(0.5 + (30 - rsi) / 100, 1.0)
        return 0.6

    def _explain(self, df, idx, sig):
        row = df.iloc[idx]
        ret_short = row.get("RET5", 0)
        rsi = row.get("RSI6", 50)
        if sig == Signal.BUY:
            return f"超跌反转: 5日跌{ret_short*100:.1f}%, RSI={rsi:.0f}"
        return f"过热/动量衰减: RSI={rsi:.0f}"


class TurtleTradingStrategy(BaseStrategy):
    """
    海龟交易策略 (经典趋势跟踪)
    - 买入: 突破20日高点 (唐奇安通道)
    - 卖出: 跌破10日低点
    - 仓位: 基于ATR的头寸管理
    """

    def __init__(self, entry_window: int = 20, exit_window: int = 10):
        super().__init__(
            name="turtle",
            params={"entry": entry_window, "exit": exit_window}
        )
        self.entry_w = entry_window
        self.exit_w = exit_window

    def generate_signal(self, df: pd.DataFrame, idx: int) -> Signal:
        if idx < self.entry_w:
            return Signal.HOLD

        row = df.iloc[idx]
        close = row["close"]
        high_n = row.get(f"high_{self.entry_w}d", row.get("HIGH_20D", close))
        low_n = row.get(f"low_{self.exit_w}d", row.get("LOW_20D", close))
        adx = row.get("ADX", 25)

        # 突破买入 (需趋势确认 ADX>25)
        if close >= high_n * 0.99 and adx > 25:
            return Signal.BUY

        # 跌破卖出
        if close <= low_n * 1.01:
            return Signal.SELL

        return Signal.HOLD

    def _calc_score(self, df, idx, sig):
        adx = df.iloc[idx].get("ADX", 25)
        if sig == Signal.BUY:
            return min(0.5 + adx / 200, 0.9)
        return 0.6

    def _explain(self, df, idx, sig):
        close = df.iloc[idx]["close"]
        adx = df.iloc[idx].get("ADX", 0)
        if sig == Signal.BUY:
            return f"海龟突破: 价格={close:.2f}, ADX={adx:.1f}"
        return f"海龟退出: 跌破{self.exit_w}日低点"


class MoneyFlowStrategy(BaseStrategy):
    """
    资金流向策略
    - 买入: 资金大幅流入 + 量能放大
    - 卖出: 资金流出 + 缩量
    """

    def __init__(self):
        super().__init__(name="money_flow")

    def generate_signal(self, df: pd.DataFrame, idx: int) -> Signal:
        if idx < 20:
            return Signal.HOLD

        row = df.iloc[idx]
        prev = df.iloc[idx - 1]

        mf = row.get("MONEY_FLOW", 0)
        mf_ma = row.get("MF_MA20", 0)
        vol_ratio = row.get("VRATIO5", row.get("vol_ratio", 1))
        obv_slope = row.get("OBV_SLOPE", 0)

        # 资金大幅流入
        if mf > mf_ma * 1.5 and vol_ratio > 1.5 and obv_slope > 0:
            return Signal.BUY

        # 资金流出
        if mf < mf_ma * 0.5 and vol_ratio < 0.8 and obv_slope < 0:
            return Signal.SELL

        return Signal.HOLD

    def _calc_score(self, df, idx, sig):
        vol_ratio = df.iloc[idx].get("VRATIO5", 1)
        if sig == Signal.BUY:
            return min(0.5 + vol_ratio / 5, 0.9)
        return 0.6

    def _explain(self, df, idx, sig):
        vol_ratio = df.iloc[idx].get("VRATIO5", 1)
        if sig == Signal.BUY:
            return f"资金大幅流入, 量比={vol_ratio:.2f}"
        return f"资金流出, 量比={vol_ratio:.2f}"


class BollingerBreakoutStrategy(BaseStrategy):
    """
    布林带突破策略 (区别于均值回归)
    - 买入: 价格突破上轨 + 带宽扩张 (趋势启动)
    - 卖出: 价格跌破中轨
    """

    def __init__(self):
        super().__init__(name="boll_breakout")

    def generate_signal(self, df: pd.DataFrame, idx: int) -> Signal:
        if idx < 1:
            return Signal.HOLD

        row = df.iloc[idx]
        prev = df.iloc[idx - 1]
        close = row["close"]
        upper = row.get("BOLL_UPPER", close)
        mid = row.get("BOLL_MID", close)
        lower = row.get("BOLL_LOWER", close)
        width = row.get("BOLL_WIDTH", 0)
        prev_width = prev.get("BOLL_WIDTH", 0)

        # 突破上轨 + 带宽扩张
        if close > upper and width > prev_width and prev["close"] <= prev.get("BOLL_UPPER", close):
            return Signal.BUY

        # 跌破中轨
        if close < mid and prev["close"] >= prev.get("BOLL_MID", close):
            return Signal.SELL

        return Signal.HOLD

    def _calc_score(self, df, idx, sig):
        return 0.65

    def _explain(self, df, idx, sig):
        close = df.iloc[idx]["close"]
        if sig == Signal.BUY:
            return f"布林带突破上轨, 价格={close:.2f}"
        return f"跌破布林中轨, 价格={close:.2f}"


class DualThrustStrategy(BaseStrategy):
    """
    Dual Thrust策略 (经典日内/短线策略)
    - 通道 = max(HH-LC, HC-LL) 过去N日
    - 买入: 开盘 + k1 * range < close
    - 卖出: 开盘 - k2 * range > close
    """

    def __init__(self, lookback: int = 4, k1: float = 0.5, k2: float = 0.5):
        super().__init__(
            name="dual_thrust",
            params={"lookback": lookback, "k1": k1, "k2": k2}
        )
        self.lookback = lookback
        self.k1 = k1
        self.k2 = k2

    def generate_signal(self, df: pd.DataFrame, idx: int) -> Signal:
        if idx < self.lookback + 1:
            return Signal.HOLD

        # 计算通道
        window = df.iloc[idx - self.lookback:idx]
        hh = window["high"].max()
        ll = window["low"].min()
        hc = window["close"].max()
        lc = window["close"].min()

        range_val = max(hh - lc, hc - ll)

        row = df.iloc[idx]
        open_price = row["open"]
        close = row["close"]

        # 买入: 突破上轨
        if close > open_price + self.k1 * range_val:
            return Signal.BUY

        # 卖出: 跌破下轨
        if close < open_price - self.k2 * range_val:
            return Signal.SELL

        return Signal.HOLD

    def _calc_score(self, df, idx, sig):
        return 0.6

    def _explain(self, df, idx, sig):
        if sig == Signal.BUY:
            return "Dual Thrust突破上轨"
        return "Dual Thrust跌破下轨"


# 注册新策略
STRATEGY_REGISTRY["momentum_reversal"] = MomentumReversalStrategy
STRATEGY_REGISTRY["turtle"] = TurtleTradingStrategy
STRATEGY_REGISTRY["money_flow"] = MoneyFlowStrategy
STRATEGY_REGISTRY["boll_breakout"] = BollingerBreakoutStrategy
STRATEGY_REGISTRY["dual_thrust"] = DualThrustStrategy
