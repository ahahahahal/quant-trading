"""
测试数据获取 + 因子计算 (全部使用 mock, 不依赖网络)
"""
import pytest
import time
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock
from pathlib import Path

from data.provider import AShareDataProvider, DataCache, FactorCalculator


class TestDataCache:
    """本地缓存测试"""

    def test_cache_key_generation(self, tmp_path):
        cache = DataCache(str(tmp_path))
        key = cache._key("000001", "daily", "qfq")
        assert key.name == "000001_daily_qfq.csv"

    def test_cache_put_and_get(self, tmp_path):
        cache = DataCache(str(tmp_path))
        df = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=5), "close": [1, 2, 3, 4, 5]})
        cache.put("000001", "daily", "qfq", df)

        result = cache.get("000001", "daily", "qfq")
        assert result is not None
        assert len(result) == 5

    def test_cache_miss(self, tmp_path):
        cache = DataCache(str(tmp_path))
        result = cache.get("nonexistent", "daily", "qfq")
        assert result is None


class TestFactorCalculator:
    """技术因子计算测试"""

    def test_add_ma_factors(self, mock_stock_df):
        df = FactorCalculator._add_ma_factors(mock_stock_df.copy())
        for w in [5, 10, 20, 60, 120]:
            assert f"ma{w}" in df.columns
            assert f"ema{w}" in df.columns
        assert "ma_bull" in df.columns

    def test_add_momentum_factors(self, mock_stock_df):
        df = FactorCalculator._add_momentum_factors(mock_stock_df.copy())
        for d in [1, 3, 5, 10, 20, 60]:
            assert f"ret_{d}d" in df.columns
        assert "rsi6" in df.columns
        assert "rsi14" in df.columns
        assert "macd_dif" in df.columns
        assert "macd_dea" in df.columns
        assert "macd_hist" in df.columns
        assert "kdj_k" in df.columns
        assert "kdj_d" in df.columns
        assert "kdj_j" in df.columns

    def test_add_volatility_factors(self, mock_stock_df):
        df = FactorCalculator._add_volatility_factors(mock_stock_df.copy())
        assert "atr14" in df.columns
        assert "atr20" in df.columns
        assert "boll_mid" in df.columns
        assert "boll_upper" in df.columns
        assert "boll_lower" in df.columns

    def test_add_volume_factors(self, mock_stock_df):
        df = FactorCalculator._add_volume_factors(mock_stock_df.copy())
        assert "vol_ma5" in df.columns
        assert "vol_ma20" in df.columns
        assert "vol_ratio" in df.columns
        assert "obv" in df.columns

    def test_add_pattern_factors(self, mock_stock_df):
        df = FactorCalculator._add_pattern_factors(mock_stock_df.copy())
        assert "body" in df.columns
        assert "upper_shadow" in df.columns
        assert "lower_shadow" in df.columns
        assert "is_limit_up" in df.columns
        assert "is_limit_down" in df.columns

    def test_add_all_factors(self, mock_stock_df):
        df = FactorCalculator.add_all_factors(mock_stock_df.copy())
        # 应包含大量因子列
        assert len(df.columns) > len(mock_stock_df.columns) + 20
        # 关键因子都存在
        for col in ["ma5", "rsi14", "macd_hist", "boll_upper", "vol_ratio", "atr14", "kdj_k"]:
            assert col in df.columns, f"缺少因子: {col}"


class TestDataProviderMocked:
    """使用 mock 测试数据提供者 (不依赖网络)"""

    def _make_akshare_mock(self):
        """创建 akshare mock 数据"""
        mock_ak = MagicMock()
        # 模拟 stock_zh_a_hist 返回数据
        n = 30
        dates = pd.date_range("2025-01-01", periods=n, freq="B")
        np.random.seed(42)
        close = 50 + np.cumsum(np.random.normal(0, 0.5, n))
        mock_df = pd.DataFrame({
            "日期": dates.strftime("%Y-%m-%d"),
            "开盘": close * 0.99,
            "收盘": close,
            "最高": close * 1.02,
            "最低": close * 0.98,
            "成交量": np.random.randint(100000, 1000000, n),
            "成交额": close * np.random.randint(100000, 1000000, n),
            "换手率": np.random.uniform(0.5, 5, n),
            "涨跌幅": np.random.normal(0, 2, n),
            "涨跌额": np.random.normal(0, 1, n),
            "振幅": np.random.uniform(1, 5, n),
        })
        mock_ak.stock_zh_a_hist.return_value = mock_df
        return mock_ak

    def test_get_stock_daily_with_mock(self, tmp_path):
        provider = AShareDataProvider(cache=False)
        provider._ak = self._make_akshare_mock()

        df = provider.get_stock_daily("000001", "20250101", "20250201")
        assert not df.empty
        assert "date" in df.columns
        assert "close" in df.columns
        assert "open" in df.columns
        assert "symbol" in df.columns
        assert df["symbol"].iloc[0] == "000001"

    def test_column_rename(self, tmp_path):
        provider = AShareDataProvider(cache=False)
        provider._ak = self._make_akshare_mock()

        df = provider.get_stock_daily("000001", "20250101", "20250201")
        # 中文列名应被重命名
        assert "日期" not in df.columns
        assert "date" in df.columns

    def test_date_sorted(self, tmp_path):
        provider = AShareDataProvider(cache=False)
        provider._ak = self._make_akshare_mock()

        df = provider.get_stock_daily("000001", "20250101", "20250201")
        dates = pd.to_datetime(df["date"])
        assert dates.is_monotonic_increasing

    def test_empty_result_on_error(self):
        """akshare 抛异常时应返回空 DataFrame"""
        provider = AShareDataProvider(cache=False)
        mock_ak = MagicMock()
        mock_ak.stock_zh_a_hist.side_effect = Exception("网络错误")
        provider._ak = mock_ak
        # mock掉baostock让它也返回空
        provider._bs_provider._logged_in = False
        provider._bs_provider._startup_check_done = True
        provider._bs_provider._login_fail_time = time.time()
        provider._bs_provider._LOGIN_FAIL_COOLDOWN = 30

        df = provider.get_stock_daily("000001", "20250101", "20250201")
        assert df.empty

    def test_retry_mechanism(self):
        """验证重试机制: 前2次失败, 第3次成功"""
        provider = AShareDataProvider(cache=False)
        call_count = 0

        mock_ak = MagicMock()

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("临时错误")
            n = 10
            dates = pd.date_range("2025-01-01", periods=n, freq="B")
            return pd.DataFrame({
                "日期": dates.strftime("%Y-%m-%d"),
                "开盘": [50] * n, "收盘": [50] * n,
                "最高": [51] * n, "最低": [49] * n,
                "成交量": [100000] * n, "成交额": [5000000] * n,
                "换手率": [1.0] * n, "涨跌幅": [0.0] * n,
                "涨跌额": [0.0] * n, "振幅": [2.0] * n,
            })

        mock_ak.stock_zh_a_hist.side_effect = side_effect
        provider._ak = mock_ak
        # mock掉baostock让它返回空，强制走akshare
        provider._bs_provider._logged_in = False
        provider._bs_provider._startup_check_done = True
        provider._bs_provider._login_fail_time = time.time()
        provider._bs_provider._LOGIN_FAIL_COOLDOWN = 30

        df = provider.get_stock_daily("000001", "20250101", "20250201")
        assert not df.empty
        assert call_count == 3
