"""
高级风控模块 - 参考freqtrade ROI表 + vnpy风控 + ATR动态止损
"""
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import CONFIG


@dataclass
class ROITarget:
    """ROI动态止盈目标"""
    holding_days: int
    profit_pct: float

    def __repr__(self):
        return f"{self.holding_days}天 -> {self.profit_pct*100:.1f}%"


class AdvancedRiskManager:
    """
    高级风控管理器
    参考freqtrade的ROI表 + ATR动态止损 + 凯利仓位
    """

    def __init__(self):
        self.config = CONFIG.risk

        # ROI动态止盈表 (参考freqtrade)
        # 持仓越久, 止盈线越低
        self.roi_table = [
            ROITarget(0, 0.20),     # 立即: 20%止盈
            ROITarget(5, 0.15),     # 5天后: 15%止盈
            ROITarget(10, 0.10),    # 10天后: 10%止盈
            ROITarget(20, 0.06),    # 20天后: 6%止盈
            ROITarget(40, 0.03),    # 40天后: 3%止盈
            ROITarget(60, 0.01),    # 60天后: 1%止盈
        ]

        # 追踪止损配置
        self.trailing_stop = True
        self.trailing_stop_positive = 0.03      # 盈利3%后启动追踪
        self.trailing_stop_positive_offset = 0.05  # 从最高点回撤5%触发
        self.trailing_only_offset_is_reached = True

    def get_roi_target(self, holding_days: int) -> float:
        """根据持仓天数获取止盈目标"""
        for roi in self.roi_table:
            if holding_days <= roi.holding_days:
                return roi.profit_pct
        return self.roi_table[-1].profit_pct

    def calc_atr_stoploss(self, price: float, atr: float,
                          multiplier: float = 2.0) -> float:
        """
        ATR动态止损 (参考freqtrade custom_stoploss)
        止损价 = 价格 - ATR * 倍数
        """
        return price - atr * multiplier

    def calc_risk_position_size(self, capital: float, entry_price: float,
                                 stop_price: float,
                                 risk_pct: float = 0.02) -> int:
        """
        风险百分比仓位计算 (参考freqtrade)
        每笔交易风险 = 总资金 * risk_pct
        仓位 = 风险金额 / (入场价 - 止损价)
        """
        risk_amount = capital * risk_pct
        risk_per_share = abs(entry_price - stop_price)
        if risk_per_share < 0.01:
            risk_per_share = entry_price * 0.08  # 默认8%止损

        shares = risk_amount / risk_per_share
        # A股100股整数倍
        shares = int(shares / 100) * 100

        # 检查最大仓位限制
        max_shares = int(capital * self.config.max_position_pct / entry_price / 100) * 100
        return min(shares, max_shares)

    def calc_kelly_position(self, win_rate: float, avg_win: float,
                            avg_loss: float) -> float:
        """
        凯利公式仓位比例
        f = p - q/b
        p = 胜率, q = 败率, b = 平均盈利/平均亏损
        """
        if avg_loss == 0 or win_rate <= 0:
            return 0

        b = avg_win / avg_loss
        q = 1 - win_rate
        kelly = win_rate - q / b

        # 半凯利 (更保守)
        half_kelly = kelly * 0.5

        return max(min(half_kelly, 0.25), 0)

    def check_trailing_stop(self, entry_price: float, current_price: float,
                            highest_price: float, holding_days: int) -> Tuple[bool, str]:
        """
        追踪止损检查 (参考freqtrade)

        Returns:
            (是否触发, 原因)
        """
        profit_pct = (current_price - entry_price) / entry_price
        drawdown_from_high = (highest_price - current_price) / highest_price

        # ROI止盈检查
        roi_target = self.get_roi_target(holding_days)
        if profit_pct >= roi_target:
            return True, f"ROI止盈: 盈利{profit_pct*100:.1f}% >= 目标{roi_target*100:.1f}%"

        # 固定止损
        if profit_pct <= -self.config.stop_loss_pct:
            return True, f"止损: 亏损{profit_pct*100:.1f}%"

        # 追踪止损
        if self.trailing_stop:
            if self.trailing_only_offset_is_reached:
                # 只有达到正偏移后才启动追踪
                if profit_pct >= self.trailing_stop_positive:
                    if drawdown_from_high >= self.trailing_stop_positive_offset:
                        return True, f"追踪止损: 从高点回撤{drawdown_from_high*100:.1f}%"
            else:
                if drawdown_from_high >= self.config.trailing_stop_pct:
                    return True, f"追踪止损: 回撤{drawdown_from_high*100:.1f}%"

        return False, ""

    def should_reduce_position(self, portfolio_drawdown: float,
                                daily_loss: float) -> bool:
        """是否需要减仓"""
        if portfolio_drawdown > self.config.max_drawdown_pct * 0.7:
            return True
        if daily_loss > self.config.max_daily_loss_pct:
            return True
        return False

    def get_dynamic_stoploss(self, price: float, atr: float,
                              holding_days: int) -> float:
        """
        动态止损价
        - 早期: ATR止损 (较宽)
        - 中期: 收紧到固定百分比
        - 后期: 追踪止损
        """
        atr_stop = self.calc_atr_stoploss(price, atr, 2.0)
        pct_stop = price * (1 - self.config.stop_loss_pct)

        if holding_days < 5:
            return min(atr_stop, pct_stop)  # 取较宽的
        elif holding_days < 20:
            return pct_stop
        else:
            return price * (1 - self.config.stop_loss_pct * 0.7)  # 收紧


class ParameterOptimizer:
    """
    策略参数优化器
    参考vnpy的参数优化模块
    """

    @staticmethod
    def grid_search(strategy_cls, df: pd.DataFrame,
                    param_grid: Dict[str, list],
                    metric: str = "sharpe") -> List[dict]:
        """
        网格搜索参数优化

        Args:
            strategy_cls: 策略类
            df: 数据
            param_grid: 参数网格 {"short_window": [3,5,7], "long_window": [15,20,25]}
            metric: 优化指标 sharpe/return/win_rate

        Returns:
            排序后的结果列表
        """
        from backtest.engine import BacktestEngine
        from itertools import product

        keys = list(param_grid.keys())
        values = list(param_grid.values())
        results = []

        for combo in product(*values):
            params = dict(zip(keys, combo))
            try:
                strategy = strategy_cls(**params)
                engine = BacktestEngine()
                stats = engine.run(df, strategy)

                result = {**params}
                result["total_return"] = float(stats.get("总收益率", "0%").replace("%", ""))
                result["sharpe"] = float(stats.get("夏普比率", "0"))
                result["max_drawdown"] = float(stats.get("最大回撤", "0%").replace("%", ""))
                result["win_rate"] = float(stats.get("胜率", "0%").replace("%", ""))
                result["trades"] = int(stats.get("交易次数", 0))
                results.append(result)
            except Exception as e:
                continue

        # 按指标排序
        if metric == "sharpe":
            results.sort(key=lambda x: x["sharpe"], reverse=True)
        elif metric == "return":
            results.sort(key=lambda x: x["total_return"], reverse=True)
        elif metric == "win_rate":
            results.sort(key=lambda x: x["win_rate"], reverse=True)

        return results

    @staticmethod
    def walk_forward(strategy_cls, df: pd.DataFrame,
                     param_grid: Dict[str, list],
                     train_pct: float = 0.7,
                     n_splits: int = 3) -> dict:
        """
        滚动前向优化 (Walk-Forward Analysis)
        避免过拟合的关键方法
        """
        from backtest.engine import BacktestEngine
        from itertools import product

        n = len(df)
        split_size = int(n * train_pct)
        step = (n - split_size) // n_splits

        all_results = []

        for i in range(n_splits):
            start = i * step
            train_end = start + split_size
            test_end = min(train_end + step, n)

            train_df = df.iloc[start:train_end].reset_index(drop=True)
            test_df = df.iloc[train_end:test_end].reset_index(drop=True)

            # 在训练集上优化
            best_params = None
            best_sharpe = -999

            keys = list(param_grid.keys())
            values = list(param_grid.values())

            for combo in product(*values):
                params = dict(zip(keys, combo))
                try:
                    strategy = strategy_cls(**params)
                    engine = BacktestEngine()
                    stats = engine.run(train_df, strategy)
                    sharpe = float(stats.get("夏普比率", "0"))
                    if sharpe > best_sharpe:
                        best_sharpe = sharpe
                        best_params = params
                except Exception:
                    continue

            # 在测试集上验证
            if best_params:
                strategy = strategy_cls(**best_params)
                engine = BacktestEngine()
                stats = engine.run(test_df, strategy)
                all_results.append({
                    "fold": i + 1,
                    "params": best_params,
                    "train_sharpe": best_sharpe,
                    "test_return": stats.get("总收益率", "0%"),
                    "test_sharpe": stats.get("夏普比率", "0"),
                })

        return {
            "folds": all_results,
            "avg_test_sharpe": np.mean([float(r["test_sharpe"]) for r in all_results]) if all_results else 0,
        }
