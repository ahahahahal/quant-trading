"""
Pytest 配置 - 共享 fixture 和 mock 数据
"""
import sys
from pathlib import Path

# 将项目根目录加入 sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import pytest
import pandas as pd
import numpy as np


@pytest.fixture
def mock_stock_df():
    """生成带技术因子的模拟股票数据 (200行)"""
    n = 200
    np.random.seed(42)
    dates = pd.date_range("2025-01-01", periods=n, freq="B")

    # 模拟价格序列: 起始价 50, 随机游走
    base_price = 50.0
    returns = np.random.normal(0.0005, 0.02, n)
    close = base_price * np.cumprod(1 + returns)

    df = pd.DataFrame({
        "date": dates,
        "open": close * (1 + np.random.uniform(-0.01, 0.01, n)),
        "high": close * (1 + np.random.uniform(0, 0.03, n)),
        "low": close * (1 - np.random.uniform(0, 0.03, n)),
        "close": close,
        "volume": np.random.randint(100000, 5000000, n).astype(float),
        "amount": close * np.random.randint(100000, 5000000, n),
        "symbol": "000001",
        "pct_change": np.concatenate([[0], np.diff(close) / close[:-1] * 100]),
    })

    # 均线因子
    for w in [5, 10, 20, 60]:
        df[f"ma{w}"] = df["close"].rolling(w).mean()
        df[f"ema{w}"] = df["close"].ewm(span=w, adjust=False).mean()

    # 均线多头排列
    df["ma_bull"] = ((df["ma5"] > df["ma10"]) &
                     (df["ma10"] > df["ma20"]) &
                     (df["ma20"] > df["ma60"])).astype(int)

    # MACD
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd_dif"] = ema12 - ema26
    df["macd_dea"] = df["macd_dif"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = 2 * (df["macd_dif"] - df["macd_dea"])

    # RSI
    for w in [6, 14]:
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0).rolling(w).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(w).mean()
        rs = gain / loss.replace(0, np.nan)
        df[f"rsi{w}"] = 100 - 100 / (1 + rs)

    # KDJ
    low_9 = df["low"].rolling(9).min()
    high_9 = df["high"].rolling(9).max()
    rsv = (df["close"] - low_9) / (high_9 - low_9).replace(0, np.nan) * 100
    df["kdj_k"] = rsv.ewm(com=2, adjust=False).mean()
    df["kdj_d"] = df["kdj_k"].ewm(com=2, adjust=False).mean()
    df["kdj_j"] = 3 * df["kdj_k"] - 2 * df["kdj_d"]

    # 布林带
    df["boll_mid"] = df["close"].rolling(20).mean()
    boll_std = df["close"].rolling(20).std()
    df["boll_upper"] = df["boll_mid"] + 2 * boll_std
    df["boll_lower"] = df["boll_mid"] - 2 * boll_std
    df["boll_width"] = (df["boll_upper"] - df["boll_lower"]) / df["boll_mid"]

    # 量比
    df["vol_ma5"] = df["volume"].rolling(5).mean()
    df["vol_ratio"] = df["volume"] / df["vol_ma5"].replace(0, np.nan)

    # N日新高/新低
    for w in [10, 20]:
        df[f"high_{w}d"] = df["high"].rolling(w).max()
        df[f"low_{w}d"] = df["low"].rolling(w).min()

    # ATR
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    df["atr14"] = tr.rolling(14).mean()

    # 动量因子
    for d in [1, 3, 5, 10, 20, 60]:
        df[f"ret_{d}d"] = df["close"].pct_change(d)

    # Alpha158 兼容字段
    df["RET5"] = df["ret_5d"]
    df["RET60"] = df["ret_60d"]
    df["RSI6"] = df["rsi6"]
    df["RSI14"] = df["rsi14"]
    df["ADX"] = 30.0  # 模拟 ADX
    df["HIGH_20D"] = df["high_20d"]
    df["LOW_20D"] = df["low_20d"]
    df["BOLL_UPPER"] = df["boll_upper"]
    df["BOLL_MID"] = df["boll_mid"]
    df["BOLL_LOWER"] = df["boll_lower"]
    df["BOLL_WIDTH"] = df["boll_width"]
    df["VRATIO5"] = df["vol_ratio"]
    df["MONEY_FLOW"] = df["close"] * df["volume"]
    df["MF_MA20"] = df["MONEY_FLOW"].rolling(20).mean()
    df["OBV_SLOPE"] = df["close"].diff().rolling(5).mean()

    # 均线斜率
    for w in [5, 10, 20]:
        df[f"ma{w}_slope"] = df[f"ma{w}"].pct_change(5)

    return df


@pytest.fixture
def mock_returns():
    """模拟收益率序列"""
    np.random.seed(42)
    return pd.Series(np.random.normal(0.0005, 0.02, 500), name="returns")


@pytest.fixture
def mock_config():
    """临时测试配置"""
    from config import SystemConfig, BacktestConfig, RiskConfig
    return SystemConfig(
        backtest=BacktestConfig(
            initial_capital=1000000,
            commission_rate=0.0003,
            stamp_tax_rate=0.001,
            slippage=0.002,
            min_commission=5.0,
        ),
        risk=RiskConfig(
            max_position_pct=0.25,
            max_total_position_pct=0.8,
            stop_loss_pct=0.08,
            take_profit_pct=0.20,
            trailing_stop_pct=0.05,
        ),
    )
