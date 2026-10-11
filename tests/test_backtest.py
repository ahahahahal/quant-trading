"""
测试回测引擎: 买入/卖出/止损/佣金计算
"""
import pytest
import pandas as pd
import numpy as np

from backtest.engine import BacktestEngine, Position, Trade, EquityCurve
from strategy.engine import Signal, TradeSignal, DualMAStrategy


class TestCommissionCalculation:
    """佣金计算测试"""

    def test_buy_commission(self):
        engine = BacktestEngine()
        costs = engine._calc_commission(100000, "BUY")
        # 万三佣金
        assert costs["commission"] == pytest.approx(30.0, rel=0.01)
        # 买入无印花税
        assert costs["stamp_tax"] == 0
        # 滑点
        assert costs["slippage"] == pytest.approx(200.0, rel=0.01)

    def test_sell_commission_includes_stamp_tax(self):
        engine = BacktestEngine()
        costs = engine._calc_commission(100000, "SELL")
        assert costs["commission"] == pytest.approx(30.0, rel=0.01)
        # 卖出有印花税千一
        assert costs["stamp_tax"] == pytest.approx(100.0, rel=0.01)

    def test_min_commission(self):
        engine = BacktestEngine()
        costs = engine._calc_commission(1000, "BUY")
        # 1000 * 0.0003 = 0.3, 低于最低5元
        assert costs["commission"] == 5.0


class TestBuyExecution:
    """买入执行测试"""

    def test_basic_buy(self):
        engine = BacktestEngine()
        trade = engine._execute_buy("000001", 50.0, "2025-01-01", "test", "测试买入")
        assert trade is not None
        assert trade.direction == "BUY"
        assert trade.symbol == "000001"
        assert trade.shares > 0
        assert trade.shares % 100 == 0  # A股100股整数倍
        assert engine.cash < 1000000  # 资金减少

    def test_buy_creates_position(self):
        engine = BacktestEngine()
        engine._execute_buy("000001", 50.0, "2025-01-01", "test", "测试")
        assert "000001" in engine.positions
        pos = engine.positions["000001"]
        assert pos.shares > 0
        assert pos.avg_cost == 50.0

    def test_buy_with_insufficient_funds(self):
        engine = BacktestEngine()
        engine.cash = 100  # 不够买1手
        trade = engine._execute_buy("000001", 50.0, "2025-01-01", "test", "测试")
        assert trade is None

    def test_add_to_existing_position(self):
        engine = BacktestEngine()
        engine._execute_buy("000001", 50.0, "2025-01-01", "test", "第一次")
        engine._execute_buy("000001", 60.0, "2025-01-02", "test", "第二次")
        pos = engine.positions["000001"]
        # 加仓后均价应介于50和60之间
        assert 50 < pos.avg_cost < 60


class TestSellExecution:
    """卖出执行测试"""

    def test_basic_sell(self):
        engine = BacktestEngine()
        engine._execute_buy("000001", 50.0, "2025-01-01", "test", "买入")
        cash_before = engine.cash
        trade = engine._execute_sell("000001", 55.0, "2025-01-10", "test", "卖出")
        assert trade is not None
        assert trade.direction == "SELL"
        assert engine.cash > cash_before  # 卖出后现金增加

    def test_sell_removes_position(self):
        engine = BacktestEngine()
        engine._execute_buy("000001", 50.0, "2025-01-01", "test", "买入")
        engine._execute_sell("000001", 55.0, "2025-01-10", "test", "卖出")
        assert "000001" not in engine.positions

    def test_sell_nonexistent_position(self):
        engine = BacktestEngine()
        trade = engine._execute_sell("000001", 50.0, "2025-01-01", "test", "测试")
        assert trade is None


class TestStopLoss:
    """止损测试"""

    def test_stop_loss_triggered(self):
        """验证止损逻辑: 亏损超过止损线时应触发"""
        from risk.advanced import AdvancedRiskManager
        rm = AdvancedRiskManager()
        entry = 100.0
        current = 90.0  # 亏损10%
        highest = 100.0
        should_exit, reason = rm.check_trailing_stop(entry, current, highest, holding_days=5)
        # 10% > 8% 止损线
        assert should_exit is True
        assert "止损" in reason

    def test_roi_take_profit(self):
        """验证ROI止盈: 盈利达到目标"""
        from risk.advanced import AdvancedRiskManager
        rm = AdvancedRiskManager()
        entry = 100.0
        current = 125.0  # 盈利25%
        highest = 125.0
        should_exit, reason = rm.check_trailing_stop(entry, current, highest, holding_days=1)
        # 25% > 20% (0天的目标)
        assert should_exit is True
        assert "ROI" in reason

    def test_trailing_stop(self):
        """验证追踪止损: 盈利后回撤触发"""
        from risk.advanced import AdvancedRiskManager
        rm = AdvancedRiskManager()
        entry = 100.0
        current = 103.0  # 盈利3%
        highest = 108.0  # 最高108, 回撤约4.6%
        should_exit, reason = rm.check_trailing_stop(entry, current, highest, holding_days=5)
        # 盈利3% >= trailing_stop_positive(3%), 回撤4.6% < 5% offset → 不触发
        # 但固定止损: -3% 不到 -8% → 不触发
        assert should_exit is False


class TestBacktestRun:
    """回测完整流程测试"""

    def test_full_backtest(self, mock_stock_df):
        engine = BacktestEngine()
        strategy = DualMAStrategy(short_window=5, long_window=20)
        stats = engine.run(mock_stock_df, strategy)

        assert stats is not None
        assert "总收益率" in stats
        assert "夏普比率" in stats
        assert "最大回撤" in stats
        assert "交易次数" in stats

    def test_equity_curve_populated(self, mock_stock_df):
        engine = BacktestEngine()
        strategy = DualMAStrategy()
        engine.run(mock_stock_df, strategy)

        eq_df = engine.get_equity_df()
        assert len(eq_df) > 0
        assert "total_equity" in eq_df.columns
        assert "cash" in eq_df.columns
        assert "drawdown" in eq_df.columns

    def test_trades_recorded(self, mock_stock_df):
        engine = BacktestEngine()
        strategy = DualMAStrategy()
        engine.run(mock_stock_df, strategy)

        trades_df = engine.get_trades_df()
        # 即使没有策略交易, 也会有强平交易
        assert isinstance(trades_df, pd.DataFrame)

    def test_final_positions_closed(self, mock_stock_df):
        """回测结束后应强制平仓"""
        engine = BacktestEngine()
        strategy = DualMAStrategy()
        engine.run(mock_stock_df, strategy)
        assert len(engine.positions) == 0  # 已强平
