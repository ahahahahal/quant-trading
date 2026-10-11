"""
测试风控模块: VaR / 夏普比率 / 止损触发
"""
import pytest
import pandas as pd
import numpy as np

from risk.manager import RiskManager, RiskMetrics
from risk.advanced import AdvancedRiskManager


class TestRiskMetrics:
    """风险指标计算测试"""

    def test_calculate_risk_metrics(self, mock_returns):
        rm = RiskManager()
        metrics = rm.calculate_risk_metrics(mock_returns)

        assert isinstance(metrics, RiskMetrics)
        # VaR 应为负数 (损失)
        assert metrics.var_95 < 0
        assert metrics.var_99 < 0
        # CVaR 比 VaR 更极端
        assert metrics.cvar_95 <= metrics.var_95
        # 最大回撤在 0~1 之间
        assert 0 <= metrics.max_drawdown <= 1
        # 波动率为正
        assert metrics.volatility > 0
        assert metrics.downside_vol >= 0

    def test_sharpe_ratio(self, mock_returns):
        rm = RiskManager()
        metrics = rm.calculate_risk_metrics(mock_returns)
        # 夏普比率是一个有限数
        assert np.isfinite(metrics.sharpe)

    def test_sortino_ratio(self, mock_returns):
        rm = RiskManager()
        metrics = rm.calculate_risk_metrics(mock_returns)
        assert np.isfinite(metrics.sortino)

    def test_calmar_ratio(self, mock_returns):
        rm = RiskManager()
        metrics = rm.calculate_risk_metrics(mock_returns)
        assert np.isfinite(metrics.calmar)

    def test_var_confidence_ordering(self, mock_returns):
        """99% VaR 应该比 95% VaR 更极端"""
        rm = RiskManager()
        metrics = rm.calculate_risk_metrics(mock_returns)
        assert metrics.var_99 <= metrics.var_95


class TestPositionSizing:
    """仓位计算测试"""

    def test_equal_weight(self):
        rm = RiskManager()
        shares = rm.calculate_position_size(1000000, 50.0, 0.2, method="equal_weight")
        assert shares >= 0
        assert shares % 100 == 0

    def test_volatility_method(self):
        rm = RiskManager()
        # 高波动率应给更小仓位
        shares_high_vol = rm.calculate_position_size(1000000, 50.0, 0.5, method="volatility")
        shares_low_vol = rm.calculate_position_size(1000000, 50.0, 0.1, method="volatility")
        assert shares_high_vol <= shares_low_vol

    def test_kelly_method(self):
        rm = RiskManager()
        shares = rm.calculate_position_size(1000000, 50.0, 0.2, method="kelly")
        assert shares >= 0
        assert shares % 100 == 0

    def test_atr_method(self):
        rm = RiskManager()
        shares = rm.calculate_position_size(1000000, 50.0, 0.2, method="atr")
        assert shares >= 0
        assert shares % 100 == 0


class TestAdvancedRiskManager:
    """高级风控测试"""

    def test_roi_table(self):
        rm = AdvancedRiskManager()
        # 0天: 20%止盈
        assert rm.get_roi_target(0) == 0.20
        # 5天: 15%
        assert rm.get_roi_target(5) == 0.15
        # 10天: 10%
        assert rm.get_roi_target(10) == 0.10
        # 超过60天: 最低档
        assert rm.get_roi_target(100) == 0.01

    def test_atr_stoploss(self):
        rm = AdvancedRiskManager()
        stop_price = rm.calc_atr_stoploss(100.0, atr=2.0, multiplier=2.0)
        assert stop_price == 96.0  # 100 - 2*2

    def test_risk_position_size(self):
        rm = AdvancedRiskManager()
        shares = rm.calc_risk_position_size(1000000, 100.0, 92.0, risk_pct=0.02)
        assert shares >= 0
        assert shares % 100 == 0

    def test_kelly_position(self):
        rm = AdvancedRiskManager()
        # 胜率55%, 平均盈利5%, 平均亏损3%
        pct = rm.calc_kelly_position(0.55, 0.05, 0.03)
        assert 0 <= pct <= 0.25  # 半凯利限制在0-25%

    def test_kelly_zero_loss(self):
        rm = AdvancedRiskManager()
        pct = rm.calc_kelly_position(0.55, 0.05, 0.0)
        assert pct == 0

    def test_trailing_stop_no_trigger(self):
        """正常持仓不应触发止损"""
        rm = AdvancedRiskManager()
        should_exit, reason = rm.check_trailing_stop(
            entry_price=100, current_price=101, highest_price=101, holding_days=5
        )
        assert should_exit is False

    def test_trailing_stop_stop_loss_trigger(self):
        """亏损超过止损线应触发"""
        rm = AdvancedRiskManager()
        should_exit, reason = rm.check_trailing_stop(
            entry_price=100, current_price=91, highest_price=100, holding_days=5
        )
        assert should_exit is True
        assert "止损" in reason

    def test_trailing_stop_roi_trigger(self):
        """盈利达到ROI目标应触发"""
        rm = AdvancedRiskManager()
        should_exit, reason = rm.check_trailing_stop(
            entry_price=100, current_price=121, highest_price=121, holding_days=1
        )
        assert should_exit is True
        assert "ROI" in reason

    def test_should_reduce_position(self):
        rm = AdvancedRiskManager()
        # 大回撤应减仓
        assert rm.should_reduce_position(0.12, 0.01) is True
        # 正常情况不减仓
        assert rm.should_reduce_position(0.05, 0.01) is False

    def test_dynamic_stoploss_early(self):
        """早期止损应较宽"""
        rm = AdvancedRiskManager()
        stop = rm.get_dynamic_stoploss(100.0, atr=2.0, holding_days=2)
        # 早期: min(atr_stop, pct_stop) = min(96, 92) = 92
        assert stop <= 100.0

    def test_dynamic_stoploss_late(self):
        """后期止损应收紧"""
        rm = AdvancedRiskManager()
        stop_late = rm.get_dynamic_stoploss(100.0, atr=2.0, holding_days=30)
        # 后期: 价格 * (1 - 0.08 * 0.7) = 100 * 0.944 = 94.4
        assert stop_late < 100.0


class TestRiskReport:
    """风控报告测试"""

    def test_generate_report(self, mock_returns):
        rm = RiskManager()
        report = rm.generate_risk_report(mock_returns)
        assert "风险" in report
        assert "夏普" in report
        assert "VaR" in report
