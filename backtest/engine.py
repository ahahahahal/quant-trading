"""
回测引擎 - 事件驱动架构
参考 backtrader/vnpy 的回测设计
支持: 佣金/印花税/滑点/资金管理
"""
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import datetime
from pathlib import Path
import json

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import CONFIG
from strategy.engine import TradeSignal, Signal, BaseStrategy


@dataclass
class Position:
    """持仓"""
    symbol: str
    shares: int
    avg_cost: float
    current_price: float
    entry_date: str
    holding_days: int = 0

    @property
    def market_value(self) -> float:
        return self.shares * self.current_price

    @property
    def pnl(self) -> float:
        return (self.current_price - self.avg_cost) * self.shares

    @property
    def pnl_pct(self) -> float:
        if self.avg_cost == 0:
            return 0
        return (self.current_price - self.avg_cost) / self.avg_cost


@dataclass
class Trade:
    """成交记录"""
    symbol: str
    direction: str           # BUY / SELL
    price: float
    shares: int
    amount: float
    commission: float
    stamp_tax: float
    slippage_cost: float
    date: str
    strategy: str
    reason: str = ""

    @property
    def total_cost(self) -> float:
        return self.commission + self.stamp_tax + self.slippage_cost


@dataclass
class EquityCurve:
    """权益曲线记录"""
    date: str
    cash: float
    market_value: float
    total_equity: float
    benchmark_value: float
    drawdown: float
    positions_count: int


class BacktestEngine:
    """
    回测引擎

    特性:
    - T+1交易规则 (A股)
    - 佣金+印花税+滑点
    - 仓位管理
    - 止损止盈
    - 最大回撤控制
    - 详细交易日志
    """

    def __init__(self, config=None):
        self.config = config or CONFIG.backtest
        self.risk_config = CONFIG.risk

        # 账户状态
        self.initial_capital = self.config.initial_capital
        self.cash = self.initial_capital
        self.positions: Dict[str, Position] = {}
        self.trades: List[Trade] = []
        self.equity_curve: List[EquityCurve] = []
        self.pending_orders: List[dict] = []  # T+1 挂单

        # 统计
        self.peak_equity = self.initial_capital
        self.max_drawdown = 0
        self.daily_returns = []

    def _calc_commission(self, amount: float, direction: str) -> dict:
        """计算交易成本"""
        commission = max(amount * self.config.commission_rate, self.config.min_commission)
        stamp_tax = amount * self.config.stamp_tax_rate if direction == "SELL" else 0
        slippage = amount * self.config.slippage
        return {
            "commission": commission,
            "stamp_tax": stamp_tax,
            "slippage": slippage,
        }

    def _execute_buy(self, symbol: str, price: float, date: str,
                     strategy: str, reason: str, target_pct: float = 0) -> Optional[Trade]:
        """执行买入"""
        # 计算可用资金
        available = self.cash * 0.99  # 预留1%避免精度问题

        if target_pct > 0:
            # 按目标仓位比例买入
            total_equity = self.cash + sum(p.market_value for p in self.positions.values())
            target_amount = total_equity * target_pct
            # 检查单只上限
            max_amount = total_equity * self.risk_config.max_position_pct
            buy_amount = min(target_amount, available, max_amount)
        else:
            # 默认买1/5仓位
            total_equity = self.cash + sum(p.market_value for p in self.positions.values())
            buy_amount = min(available, total_equity * 0.2)

        if buy_amount < price * 100:
            return None  # 不够买1手

        # A股最小交易单位100股
        shares = int(buy_amount / price / 100) * 100
        if shares <= 0:
            return None

        actual_amount = shares * price
        costs = self._calc_commission(actual_amount, "BUY")
        total_cost = actual_amount + costs["commission"] + costs["stamp_tax"] + costs["slippage"]

        if total_cost > self.cash:
            shares -= 100
            if shares <= 0:
                return None
            actual_amount = shares * price
            costs = self._calc_commission(actual_amount, "BUY")
            total_cost = actual_amount + costs["commission"] + costs["stamp_tax"] + costs["slippage"]

        # 更新持仓
        if symbol in self.positions:
            pos = self.positions[symbol]
            total_shares = pos.shares + shares
            pos.avg_cost = (pos.avg_cost * pos.shares + price * shares) / total_shares
            pos.shares = total_shares
        else:
            self.positions[symbol] = Position(
                symbol=symbol, shares=shares, avg_cost=price,
                current_price=price, entry_date=date,
            )

        self.cash -= total_cost

        trade = Trade(
            symbol=symbol, direction="BUY", price=price,
            shares=shares, amount=actual_amount, date=date,
            strategy=strategy, reason=reason,
            commission=costs["commission"], stamp_tax=costs["stamp_tax"],
            slippage_cost=costs["slippage"],
        )
        self.trades.append(trade)
        return trade

    def _execute_sell(self, symbol: str, price: float, date: str,
                      strategy: str, reason: str, sell_pct: float = 1.0) -> Optional[Trade]:
        """执行卖出"""
        if symbol not in self.positions:
            return None

        pos = self.positions[symbol]
        shares = int(pos.shares * sell_pct / 100) * 100
        if shares <= 0:
            shares = pos.shares  # 全卖
        shares = min(shares, pos.shares)

        actual_amount = shares * price
        costs = self._calc_commission(actual_amount, "SELL")
        total_cost = costs["commission"] + costs["stamp_tax"] + costs["slippage"]

        self.cash += actual_amount - total_cost

        pos.shares -= shares
        if pos.shares <= 0:
            del self.positions[symbol]

        trade = Trade(
            symbol=symbol, direction="SELL", price=price,
            shares=shares, amount=actual_amount, date=date,
            strategy=strategy, reason=reason,
            commission=costs["commission"], stamp_tax=costs["stamp_tax"],
            slippage_cost=costs["slippage"],
        )
        self.trades.append(trade)
        return trade

    def _check_risk(self, date: str) -> List[TradeSignal]:
        """风控检查 - ROI止盈/止损/追踪止损/最大回撤"""
        from risk.advanced import AdvancedRiskManager
        risk_mgr = AdvancedRiskManager()

        risk_signals = []

        for symbol, pos in self.positions.items():
            # 使用高级风控: ROI + 追踪止损
            highest = max(pos.current_price, pos.avg_cost)  # 简化: 用当前价作为最高价
            should_exit, reason = risk_mgr.check_trailing_stop(
                pos.avg_cost, pos.current_price, highest, pos.holding_days
            )
            if should_exit:
                risk_signals.append(TradeSignal(
                    symbol=symbol, signal=Signal.SELL,
                    price=pos.current_price, date=date,
                    strategy="risk", reason=reason,
                ))

        # 最大回撤检查
        total_equity = self.cash + sum(p.market_value for p in self.positions.values())
        if total_equity < self.peak_equity * (1 - self.risk_config.max_drawdown_pct):
            for symbol in list(self.positions.keys()):
                pos = self.positions[symbol]
                risk_signals.append(TradeSignal(
                    symbol=symbol, signal=Signal.SELL,
                    price=pos.current_price, date=date,
                    strategy="risk_max_drawdown",
                    reason=f"最大回撤触发: {self.max_drawdown*100:.1f}%",
                ))

        return risk_signals

    def _update_equity(self, date: str, benchmark_price: float = None):
        """更新权益曲线"""
        market_value = sum(p.market_value for p in self.positions.values())
        total_equity = self.cash + market_value

        # 更新峰值
        if total_equity > self.peak_equity:
            self.peak_equity = total_equity

        # 计算回撤
        drawdown = (self.peak_equity - total_equity) / self.peak_equity
        self.max_drawdown = max(self.max_drawdown, drawdown)

        # 日收益率
        if self.equity_curve:
            prev_equity = self.equity_curve[-1].total_equity
            daily_ret = (total_equity - prev_equity) / prev_equity if prev_equity else 0
            self.daily_returns.append(daily_ret)

        # 基准
        benchmark_value = benchmark_price * (self.initial_capital / benchmark_price) if benchmark_price else total_equity

        self.equity_curve.append(EquityCurve(
            date=date, cash=self.cash, market_value=market_value,
            total_equity=total_equity, benchmark_value=benchmark_value,
            drawdown=drawdown, positions_count=len(self.positions),
        ))

    def run(self, df: pd.DataFrame, strategy: BaseStrategy,
            benchmark_df: pd.DataFrame = None) -> dict:
        """
        运行回测

        Args:
            df: 带因子的股票数据
            strategy: 策略实例
            benchmark_df: 基准指数数据

        Returns:
            回测结果统计
        """
        # 生成信号
        signals = strategy.run(df)
        signal_map = {}
        for sig in signals:
            key = sig.date[:10]
            signal_map.setdefault(key, []).append(sig)

        # 获取基准价格映射
        benchmark_map = {}
        if benchmark_df is not None and not benchmark_df.empty:
            for _, row in benchmark_df.iterrows():
                benchmark_map[str(row["date"])[:10]] = row["close"]

        # 逐日回测
        for i in range(len(df)):
            row = df.iloc[i]
            date = str(row["date"])[:10]
            current_price = row["close"]

            # 更新持仓价格
            for pos in self.positions.values():
                pos.current_price = current_price
                pos.holding_days += 1

            # T+1: 执行昨日挂单
            executed_orders = []
            for order in self.pending_orders:
                if order["direction"] == "BUY":
                    self._execute_buy(
                        order["symbol"], order["price"], date,
                        order["strategy"], order["reason"],
                        order.get("target_pct", 0),
                    )
                else:
                    self._execute_sell(
                        order["symbol"], order["price"], date,
                        order["strategy"], order["reason"],
                    )
            self.pending_orders = []

            # 风控检查
            risk_signals = self._check_risk(date)
            for sig in risk_signals:
                self._execute_sell(
                    sig.symbol, sig.price, date,
                    sig.strategy, sig.reason,
                )

            # 策略信号
            day_signals = signal_map.get(date, [])
            for sig in day_signals:
                if sig.signal == Signal.BUY:
                    if sig.symbol not in self.positions:
                        # 检查总仓位
                        total_pos = sum(p.market_value for p in self.positions.values())
                        total_equity = self.cash + total_pos
                        if total_pos / total_equity < self.risk_config.max_total_position_pct:
                            self.pending_orders.append({
                                "symbol": sig.symbol,
                                "direction": "BUY",
                                "price": sig.price,
                                "strategy": sig.strategy,
                                "reason": sig.reason,
                                "target_pct": sig.target_pct if sig.target_pct > 0 else 0.2,
                            })
                elif sig.signal == Signal.SELL:
                    if sig.symbol in self.positions:
                        self.pending_orders.append({
                            "symbol": sig.symbol,
                            "direction": "SELL",
                            "price": sig.price,
                            "strategy": sig.strategy,
                            "reason": sig.reason,
                        })

            # 更新权益
            bm_price = benchmark_map.get(date)
            self._update_equity(date, bm_price)

        # 强制平仓
        final_price = df.iloc[-1]["close"]
        final_date = str(df.iloc[-1]["date"])[:10]
        for symbol in list(self.positions.keys()):
            self._execute_sell(symbol, final_price, final_date, "force_close", "回测结束强平")

        return self.get_statistics()

    def get_statistics(self) -> dict:
        """计算回测统计指标"""
        if not self.equity_curve:
            return {}

        equities = [e.total_equity for e in self.equity_curve]
        returns = np.array(self.daily_returns) if self.daily_returns else np.array([0])

        # 基础指标
        total_return = (equities[-1] - self.initial_capital) / self.initial_capital
        n_days = len(equities)
        annual_return = (1 + total_return) ** (252 / max(n_days, 1)) - 1

        # 夏普比率 (无风险利率3%)
        rf_daily = 0.03 / 252
        excess_returns = returns - rf_daily
        sharpe = np.mean(excess_returns) / (np.std(excess_returns) + 1e-10) * np.sqrt(252)

        # 最大回撤
        peak = np.maximum.accumulate(equities)
        drawdowns = (peak - equities) / peak
        max_dd = np.max(drawdowns)

        # 卡尔玛比率
        calmar = annual_return / (max_dd + 1e-10)

        # 胜率
        buy_trades = [t for t in self.trades if t.direction == "BUY"]
        sell_trades = [t for t in self.trades if t.direction == "SELL"]

        # 简单盈亏配对
        wins, losses = 0, 0
        for sell in sell_trades:
            buy_price = None
            for buy in buy_trades:
                if buy.symbol == sell.symbol and buy.date <= sell.date:
                    buy_price = buy.price
            if buy_price and sell.price > buy_price:
                wins += 1
            elif buy_price:
                losses += 1

        win_rate = wins / max(wins + losses, 1)

        # 交易成本
        total_cost = sum(t.total_cost for t in self.trades)

        stats = {
            "初始资金": self.initial_capital,
            "最终资金": equities[-1],
            "总收益率": f"{total_return*100:.2f}%",
            "年化收益率": f"{annual_return*100:.2f}%",
            "夏普比率": f"{sharpe:.3f}",
            "最大回撤": f"{max_dd*100:.2f}%",
            "卡尔玛比率": f"{calmar:.3f}",
            "交易次数": len(self.trades),
            "胜率": f"{win_rate*100:.1f}%",
            "总交易成本": f"{total_cost:.2f}",
            "回测天数": n_days,
            "总盈亏": f"{equities[-1] - self.initial_capital:.2f}",
        }

        return stats

    def get_equity_df(self) -> pd.DataFrame:
        """权益曲线转DataFrame"""
        return pd.DataFrame([{
            "date": e.date, "cash": e.cash,
            "market_value": e.market_value,
            "total_equity": e.total_equity,
            "benchmark_value": e.benchmark_value,
            "drawdown": e.drawdown,
            "positions": e.positions_count,
        } for e in self.equity_curve])

    def get_trades_df(self) -> pd.DataFrame:
        """交易记录转DataFrame"""
        return pd.DataFrame([{
            "date": t.date, "symbol": t.symbol,
            "direction": t.direction, "price": t.price,
            "shares": t.shares, "amount": t.amount,
            "commission": t.commission, "stamp_tax": t.stamp_tax,
            "slippage": t.slippage_cost,
            "strategy": t.strategy, "reason": t.reason,
        } for t in self.trades])
