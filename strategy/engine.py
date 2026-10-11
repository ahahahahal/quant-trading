"""
策略引擎 - 基类 + 5种内置策略
参考 vnpy 的 CtaTemplate 和 backtrader 的 Strategy 设计
"""
import logging
import pandas as pd
import numpy as np
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from enum import Enum
from datetime import datetime

log = logging.getLogger("quant.strategy")


class Signal(Enum):
    BUY = 1
    SELL = -1
    HOLD = 0


@dataclass
class TradeSignal:
    """交易信号"""
    symbol: str
    signal: Signal
    price: float
    date: str
    strategy: str
    score: float = 0.0        # 信号强度 0~1
    reason: str = ""
    target_pct: float = 0.0   # 目标仓位比例

    def to_dict(self):
        return {
            "symbol": self.symbol,
            "signal": self.signal.name,
            "price": self.price,
            "date": self.date,
            "strategy": self.strategy,
            "score": self.score,
            "reason": self.reason,
            "target_pct": self.target_pct,
        }


class BaseStrategy(ABC):
    """策略基类"""

    def __init__(self, name: str = "base", params: dict = None):
        self.name = name
        self.params = params or {}
        self.signals: List[TradeSignal] = []

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame, idx: int) -> Signal:
        """在第idx行生成信号"""
        pass

    def run(self, df: pd.DataFrame) -> List[TradeSignal]:
        """遍历数据生成全部信号"""
        self.signals = []
        for i in range(1, len(df)):
            sig = self.generate_signal(df, i)
            if sig != Signal.HOLD:
                self.signals.append(TradeSignal(
                    symbol=df.iloc[i].get("symbol", ""),
                    signal=sig,
                    price=df.iloc[i]["close"],
                    date=str(df.iloc[i]["date"]),
                    strategy=self.name,
                    score=self._calc_score(df, i, sig),
                    reason=self._explain(df, i, sig),
                ))
        return self.signals

    def _calc_score(self, df: pd.DataFrame, idx: int, sig: Signal) -> float:
        """计算信号强度"""
        return 0.5

    def _explain(self, df: pd.DataFrame, idx: int, sig: Signal) -> str:
        """生成信号说明"""
        return ""


# ==================== 策略1: 双均线策略 ====================
class DualMAStrategy(BaseStrategy):
    """
    经典双均线交叉策略
    - 金叉买入: 短期均线上穿长期均线
    - 死叉卖出: 短期均线下穿长期均线
    - 增加成交量确认
    """

    def __init__(self, short_window: int = 5, long_window: int = 20,
                 vol_confirm: bool = True):
        super().__init__(
            name="dual_ma",
            params={"short": short_window, "long": long_window}
        )
        self.short_w = short_window
        self.long_w = long_window
        self.vol_confirm = vol_confirm

    def generate_signal(self, df: pd.DataFrame, idx: int) -> Signal:
        if idx < self.long_w:
            return Signal.HOLD

        cur_short = df.iloc[idx][f"ma{self.short_w}"]
        cur_long = df.iloc[idx][f"ma{self.long_w}"]
        pre_short = df.iloc[idx - 1][f"ma{self.short_w}"]
        pre_long = df.iloc[idx - 1][f"ma{self.long_w}"]

        # 金叉
        if pre_short <= pre_long and cur_short > cur_long:
            if self.vol_confirm and df.iloc[idx].get("vol_ratio", 1) < 1.2:
                return Signal.HOLD  # 量能不足
            return Signal.BUY

        # 死叉
        if pre_short >= pre_long and cur_short < cur_long:
            return Signal.SELL

        return Signal.HOLD

    def _calc_score(self, df, idx, sig):
        score = 0.5
        if sig == Signal.BUY:
            if df.iloc[idx].get("ma_bull", 0):
                score += 0.2
            if df.iloc[idx].get("vol_ratio", 1) > 1.5:
                score += 0.15
            if df.iloc[idx].get("rsi14", 50) < 70:
                score += 0.15
        return min(score, 1.0)

    def _explain(self, df, idx, sig):
        if sig == Signal.BUY:
            return f"MA{self.short_w}上穿MA{self.long_w}金叉, 量比={df.iloc[idx].get('vol_ratio', 0):.2f}"
        return f"MA{self.short_w}下穿MA{self.long_w}死叉"


# ==================== 策略2: MACD趋势策略 ====================
class MACDStrategy(BaseStrategy):
    """
    MACD策略
    - DIF上穿DEA + 零轴之上 → 强买
    - DIF上穿DEA + 零轴之下 → 弱买
    - MACD柱由正转负 → 卖出
    """

    def __init__(self):
        super().__init__(name="macd")

    def generate_signal(self, df: pd.DataFrame, idx: int) -> Signal:
        if idx < 1:
            return Signal.HOLD

        dif = df.iloc[idx]["macd_dif"]
        dea = df.iloc[idx]["macd_dea"]
        hist = df.iloc[idx]["macd_hist"]
        pre_hist = df.iloc[idx - 1]["macd_hist"]

        # 金叉买入
        if pre_hist <= 0 and hist > 0:
            return Signal.BUY
        # 死叉卖出
        if pre_hist >= 0 and hist < 0:
            return Signal.SELL

        return Signal.HOLD

    def _calc_score(self, df, idx, sig):
        dif = df.iloc[idx]["macd_dif"]
        if sig == Signal.BUY:
            return 0.7 if dif > 0 else 0.5  # 零轴之上更强
        return 0.6

    def _explain(self, df, idx, sig):
        dif = df.iloc[idx]["macd_dif"]
        pos = "零轴上方" if dif > 0 else "零轴下方"
        if sig == Signal.BUY:
            return f"MACD金叉 ({pos}), DIF={dif:.4f}"
        return f"MACD死叉 ({pos})"


# ==================== 策略3: 布林带均值回归策略 ====================
class BollingerMeanReversion(BaseStrategy):
    """
    布林带均值回归策略
    - 价格触及下轨 + RSI超卖 → 买入
    - 价格触及上轨 + RSI超买 → 卖出
    """

    def __init__(self, rsi_threshold_low: float = 30, rsi_threshold_high: float = 70):
        super().__init__(
            name="boll_mr",
            params={"rsi_low": rsi_threshold_low, "rsi_high": rsi_threshold_high}
        )
        self.rsi_low = rsi_threshold_low
        self.rsi_high = rsi_threshold_high

    def generate_signal(self, df: pd.DataFrame, idx: int) -> Signal:
        close = df.iloc[idx]["close"]
        lower = df.iloc[idx].get("boll_lower", close)
        upper = df.iloc[idx].get("boll_upper", close)
        rsi = df.iloc[idx].get("rsi14", 50)

        # 触及下轨+超卖
        if close <= lower and rsi < self.rsi_low:
            return Signal.BUY
        # 触及上轨+超买
        if close >= upper and rsi > self.rsi_high:
            return Signal.SELL

        return Signal.HOLD

    def _calc_score(self, df, idx, sig):
        rsi = df.iloc[idx].get("rsi14", 50)
        if sig == Signal.BUY:
            return 0.5 + max(0, (self.rsi_low - rsi) / 100)
        return 0.5 + max(0, (rsi - self.rsi_high) / 100)

    def _explain(self, df, idx, sig):
        close = df.iloc[idx]["close"]
        rsi = df.iloc[idx].get("rsi14", 50)
        if sig == Signal.BUY:
            return f"触及布林下轨({close:.2f}), RSI={rsi:.1f} 超卖"
        return f"触及布林上轨({close:.2f}), RSI={rsi:.1f} 超买"


# ==================== 策略4: 放量突破策略 ====================
class VolumeBreakoutStrategy(BaseStrategy):
    """
    放量突破策略
    - 价格创N日新高 + 量比>2 → 买入
    - 跌破20日均线 → 卖出
    """

    def __init__(self, breakout_window: int = 20, vol_ratio_min: float = 2.0):
        super().__init__(
            name="vol_breakout",
            params={"window": breakout_window, "vol_min": vol_ratio_min}
        )
        self.window = breakout_window
        self.vol_min = vol_ratio_min

    def generate_signal(self, df: pd.DataFrame, idx: int) -> Signal:
        if idx < self.window:
            return Signal.HOLD

        close = df.iloc[idx]["close"]
        high_n = df.iloc[idx].get(f"high_{self.window}d", close)
        vol_ratio = df.iloc[idx].get("vol_ratio", 1)
        ma20 = df.iloc[idx].get("ma20", close)

        # 放量创新高
        if close >= high_n * 0.99 and vol_ratio >= self.vol_min:
            return Signal.BUY

        # 跌破均线
        if close < ma20 and df.iloc[idx - 1]["close"] >= df.iloc[idx - 1].get("ma20", close):
            return Signal.SELL

        return Signal.HOLD

    def _calc_score(self, df, idx, sig):
        vol_ratio = df.iloc[idx].get("vol_ratio", 1)
        if sig == Signal.BUY:
            return min(0.5 + vol_ratio / 10, 1.0)
        return 0.6

    def _explain(self, df, idx, sig):
        if sig == Signal.BUY:
            return f"放量突破{self.window}日高点, 量比={df.iloc[idx].get('vol_ratio', 0):.2f}"
        return "跌破20日均线"


# ==================== 策略5: KDJ+RSI多因子策略 ====================
class MultiFactorStrategy(BaseStrategy):
    """
    多因子综合策略
    - KDJ金叉 + RSI拐头 + MACD共振 → 买入
    - 任一因子发出卖出信号 → 卖出
    """

    def __init__(self):
        super().__init__(name="multi_factor")

    def generate_signal(self, df: pd.DataFrame, idx: int) -> Signal:
        if idx < 2:
            return Signal.HOLD

        buy_score = 0

        # KDJ金叉
        if (df.iloc[idx - 1]["kdj_j"] < df.iloc[idx - 1]["kdj_k"] and
                df.iloc[idx]["kdj_j"] > df.iloc[idx]["kdj_k"]):
            buy_score += 1

        # RSI从超卖回升
        if df.iloc[idx - 1].get("rsi14", 50) < 35 and df.iloc[idx].get("rsi14", 50) > 35:
            buy_score += 1

        # MACD柱放大
        if df.iloc[idx]["macd_hist"] > df.iloc[idx - 1]["macd_hist"] > 0:
            buy_score += 1

        # 均线多头
        if df.iloc[idx].get("ma_bull", 0):
            buy_score += 1

        # 量能配合
        if df.iloc[idx].get("vol_ratio", 1) > 1.3:
            buy_score += 1

        if buy_score >= 3:
            return Signal.BUY

        # 卖出条件
        sell_score = 0
        if df.iloc[idx].get("rsi14", 50) > 80:
            sell_score += 1
        if df.iloc[idx]["kdj_j"] > 90:
            sell_score += 1
        if df.iloc[idx]["macd_hist"] < df.iloc[idx - 1]["macd_hist"] < 0:
            sell_score += 1

        if sell_score >= 2:
            return Signal.SELL

        return Signal.HOLD

    def _calc_score(self, df, idx, sig):
        return 0.7 if sig == Signal.BUY else 0.65

    def _explain(self, df, idx, sig):
        if sig == Signal.BUY:
            return "多因子共振: KDJ+RSI+MACD+均线+量能"
        return "多因子超买: RSI+KDJ过高"


# ==================== 策略工厂 ====================
STRATEGY_REGISTRY = {
    "dual_ma": DualMAStrategy,
    "macd": MACDStrategy,
    "boll_mr": BollingerMeanReversion,
    "vol_breakout": VolumeBreakoutStrategy,
    "multi_factor": MultiFactorStrategy,
}


def create_strategy(name: str, **kwargs) -> BaseStrategy:
    """策略工厂"""
    if name not in STRATEGY_REGISTRY:
        raise ValueError(f"未知策略: {name}, 可选: {list(STRATEGY_REGISTRY.keys())}")
    return STRATEGY_REGISTRY[name](**kwargs)


def run_all_strategies(df: pd.DataFrame) -> Dict[str, List[TradeSignal]]:
    """运行全部策略"""
    results = {}
    for name, cls in STRATEGY_REGISTRY.items():
        strategy = cls()
        signals = strategy.run(df)
        results[name] = signals
        log.info("%s: 产生 %d 个信号", name, len(signals))
    return results
