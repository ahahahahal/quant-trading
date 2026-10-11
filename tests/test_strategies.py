"""
测试所有策略能正常生成信号
覆盖 10 个策略: dual_ma, macd, boll_mr, vol_breakout, multi_factor,
               momentum_reversal, turtle, money_flow, boll_breakout, dual_thrust
"""
import pytest
import pandas as pd
import numpy as np

from strategy.engine import (
    STRATEGY_REGISTRY, create_strategy, Signal,
    DualMAStrategy, MACDStrategy, BollingerMeanReversion,
    VolumeBreakoutStrategy, MultiFactorStrategy,
)
import strategy.advanced  # 注册新策略
from strategy.advanced import (
    MomentumReversalStrategy, TurtleTradingStrategy,
    MoneyFlowStrategy, BollingerBreakoutStrategy, DualThrustStrategy,
)


# ==================== 策略注册表完整性 ====================

class TestStrategyRegistry:
    """测试策略注册表完整性"""

    def test_registry_has_10_strategies(self):
        assert len(STRATEGY_REGISTRY) == 10

    def test_all_expected_names_present(self):
        expected = {
            "dual_ma", "macd", "boll_mr", "vol_breakout", "multi_factor",
            "momentum_reversal", "turtle", "money_flow", "boll_breakout", "dual_thrust",
        }
        assert set(STRATEGY_REGISTRY.keys()) == expected

    def test_create_strategy_factory(self):
        for name in STRATEGY_REGISTRY:
            s = create_strategy(name)
            assert s.name == name or s.name.replace("_", "") == name.replace("_", "")


# ==================== 各策略信号生成测试 ====================

class TestDualMAStrategy:
    def test_generate_signals(self, mock_stock_df):
        s = DualMAStrategy(short_window=5, long_window=20)
        signals = s.run(mock_stock_df)
        assert isinstance(signals, list)
        # 双均线策略至少应该产生一些信号
        for sig in signals:
            assert sig.signal in (Signal.BUY, Signal.SELL)
            assert sig.price > 0
            assert sig.strategy == "dual_ma"

    def test_generate_signal_returns_enum(self, mock_stock_df):
        s = DualMAStrategy()
        sig = s.generate_signal(mock_stock_df, 50)
        assert isinstance(sig, Signal)


class TestMACDStrategy:
    def test_generate_signals(self, mock_stock_df):
        s = MACDStrategy()
        signals = s.run(mock_stock_df)
        assert isinstance(signals, list)
        for sig in signals:
            assert sig.signal in (Signal.BUY, Signal.SELL)
            assert sig.strategy == "macd"


class TestBollingerMeanReversion:
    def test_generate_signals(self, mock_stock_df):
        s = BollingerMeanReversion(rsi_threshold_low=30, rsi_threshold_high=70)
        signals = s.run(mock_stock_df)
        assert isinstance(signals, list)
        for sig in signals:
            assert sig.signal in (Signal.BUY, Signal.SELL)
            assert sig.strategy == "boll_mr"


class TestVolumeBreakoutStrategy:
    def test_generate_signals(self, mock_stock_df):
        s = VolumeBreakoutStrategy(breakout_window=20, vol_ratio_min=2.0)
        signals = s.run(mock_stock_df)
        assert isinstance(signals, list)
        for sig in signals:
            assert sig.signal in (Signal.BUY, Signal.SELL)
            assert sig.strategy == "vol_breakout"


class TestMultiFactorStrategy:
    def test_generate_signals(self, mock_stock_df):
        s = MultiFactorStrategy()
        signals = s.run(mock_stock_df)
        assert isinstance(signals, list)
        for sig in signals:
            assert sig.signal in (Signal.BUY, Signal.SELL)
            assert sig.strategy == "multi_factor"


class TestMomentumReversalStrategy:
    def test_generate_signals(self, mock_stock_df):
        s = MomentumReversalStrategy(short_lookback=5, long_lookback=60)
        signals = s.run(mock_stock_df)
        assert isinstance(signals, list)
        for sig in signals:
            assert sig.signal in (Signal.BUY, Signal.SELL)
            assert sig.strategy == "momentum_reversal"


class TestTurtleTradingStrategy:
    def test_generate_signals(self, mock_stock_df):
        s = TurtleTradingStrategy(entry_window=20, exit_window=10)
        signals = s.run(mock_stock_df)
        assert isinstance(signals, list)
        for sig in signals:
            assert sig.signal in (Signal.BUY, Signal.SELL)
            assert sig.strategy == "turtle"


class TestMoneyFlowStrategy:
    def test_generate_signals(self, mock_stock_df):
        s = MoneyFlowStrategy()
        signals = s.run(mock_stock_df)
        assert isinstance(signals, list)
        for sig in signals:
            assert sig.signal in (Signal.BUY, Signal.SELL)
            assert sig.strategy == "money_flow"


class TestBollingerBreakoutStrategy:
    def test_generate_signals(self, mock_stock_df):
        s = BollingerBreakoutStrategy()
        signals = s.run(mock_stock_df)
        assert isinstance(signals, list)
        for sig in signals:
            assert sig.signal in (Signal.BUY, Signal.SELL)
            assert sig.strategy == "boll_breakout"


class TestDualThrustStrategy:
    def test_generate_signals(self, mock_stock_df):
        s = DualThrustStrategy(lookback=4, k1=0.5, k2=0.5)
        signals = s.run(mock_stock_df)
        assert isinstance(signals, list)
        for sig in signals:
            assert sig.signal in (Signal.BUY, Signal.SELL)
            assert sig.strategy == "dual_thrust"


# ==================== 通用信号质量测试 ====================

class TestSignalQuality:
    """所有策略通用的信号质量检查"""

    @pytest.mark.parametrize("strategy_name", list(STRATEGY_REGISTRY.keys()))
    def test_signal_score_in_range(self, strategy_name, mock_stock_df):
        s = create_strategy(strategy_name)
        signals = s.run(mock_stock_df)
        for sig in signals:
            assert 0 <= sig.score <= 1, f"{strategy_name} score {sig.score} out of range"

    @pytest.mark.parametrize("strategy_name", list(STRATEGY_REGISTRY.keys()))
    def test_empty_data_no_crash(self, strategy_name):
        s = create_strategy(strategy_name)
        empty_df = pd.DataFrame(columns=[
            "date", "open", "high", "low", "close", "volume",
            "ma5", "ma10", "ma20", "ma60", "macd_dif", "macd_dea", "macd_hist",
            "rsi6", "rsi14", "kdj_k", "kdj_d", "kdj_j",
            "boll_upper", "boll_lower", "boll_mid", "boll_width",
            "vol_ratio", "vol_ma5", "ma_bull", "high_20d", "low_20d",
            "atr14", "ADX", "HIGH_20D", "LOW_20D",
            "BOLL_UPPER", "BOLL_MID", "BOLL_LOWER", "BOLL_WIDTH",
            "VRATIO5", "MONEY_FLOW", "MF_MA20", "OBV_SLOPE",
            "RET5", "RET60", "RSI6", "RSI14",
        ])
        # 空数据不应崩溃
        signals = s.run(empty_df)
        assert isinstance(signals, list)
