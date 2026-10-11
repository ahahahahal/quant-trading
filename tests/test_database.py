"""
测试 SQLite 数据库模块: 建表/存储/查询
"""
import pytest
import json
import os
import tempfile
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.database import (
    init_db, save_trades, save_signals, save_daily_picks,
    query_trades, query_signals, query_daily_picks, get_connection,
)


@pytest.fixture
def tmp_db(tmp_path):
    """创建临时数据库路径"""
    return str(tmp_path / "test.db")


@pytest.fixture
def sample_trades():
    """示例交易记录"""
    return [
        {
            "symbol": "600519", "direction": "BUY", "price": 1800.0,
            "shares": 100, "amount": 180000.0, "commission": 54.0,
            "stamp_tax": 0, "slippage": 360.0, "date": "2025-03-01",
            "strategy": "dual_ma", "reason": "MA金叉",
        },
        {
            "symbol": "600519", "direction": "SELL", "price": 1900.0,
            "shares": 100, "amount": 190000.0, "commission": 57.0,
            "stamp_tax": 190.0, "slippage": 380.0, "date": "2025-04-01",
            "strategy": "dual_ma", "reason": "MA死叉",
        },
        {
            "symbol": "000858", "direction": "BUY", "price": 150.0,
            "shares": 200, "amount": 30000.0, "commission": 9.0,
            "stamp_tax": 0, "slippage": 60.0, "date": "2025-03-15",
            "strategy": "macd", "reason": "MACD金叉",
        },
    ]


@pytest.fixture
def sample_signals():
    """示例信号记录"""
    return [
        {"symbol": "600519", "signal": "BUY", "price": 1800.0, "score": 0.8, "date": "2025-03-01", "strategy": "dual_ma", "reason": "MA金叉"},
        {"symbol": "000858", "signal": "SELL", "price": 150.0, "score": 0.6, "date": "2025-03-15", "strategy": "macd", "reason": "MACD死叉"},
    ]


@pytest.fixture
def sample_picks():
    """示例选股记录"""
    return [
        {
            "symbol": "600519", "name": "贵州茅台", "score": 85.5,
            "price": 1800.0, "pct_change": 1.5, "market_cap": 22000.0,
            "pe": 35.0, "reasons": ["均线多头排列", "MACD柱放大"],
            "prediction": {"direction": "看涨", "confidence": 0.75, "target_5d": 1850.0},
        },
        {
            "symbol": "000858", "name": "五粮液", "score": 72.0,
            "price": 150.0, "pct_change": -0.5, "market_cap": 5800.0,
            "pe": 25.0, "reasons": ["RSI适中"],
            "prediction": {"direction": "震荡", "confidence": 0.4},
        },
    ]


class TestInitDb:
    """数据库初始化测试"""

    def test_init_creates_tables(self, tmp_db):
        init_db(tmp_db)
        conn = get_connection(tmp_db)
        cursor = conn.cursor()
        # 检查三张表是否存在
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]
        assert "trades" in tables
        assert "signals" in tables
        assert "daily_picks" in tables
        conn.close()

    def test_init_creates_indexes(self, tmp_db):
        init_db(tmp_db)
        conn = get_connection(tmp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = [row[0] for row in cursor.fetchall()]
        assert "idx_trades_date" in indexes
        assert "idx_trades_symbol" in indexes
        assert "idx_signals_date" in indexes
        assert "idx_picks_date" in indexes
        conn.close()

    def test_init_idempotent(self, tmp_db):
        """重复初始化不应报错"""
        init_db(tmp_db)
        init_db(tmp_db)
        conn = get_connection(tmp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM trades")
        assert cursor.fetchone()[0] == 0
        conn.close()


class TestSaveTrades:
    """交易记录存储测试"""

    def test_save_single_trade(self, tmp_db, sample_trades):
        init_db(tmp_db)
        count = save_trades([sample_trades[0]], db_path=tmp_db)
        assert count == 1

    def test_save_multiple_trades(self, tmp_db, sample_trades):
        init_db(tmp_db)
        count = save_trades(sample_trades, db_path=tmp_db)
        assert count == 3

    def test_save_empty_list(self, tmp_db):
        init_db(tmp_db)
        count = save_trades([], db_path=tmp_db)
        assert count == 0

    def test_save_with_backtest_id(self, tmp_db, sample_trades):
        init_db(tmp_db)
        save_trades(sample_trades, backtest_id="test_001", db_path=tmp_db)
        rows = query_trades(db_path=tmp_db)
        assert all(r["backtest_id"] == "test_001" for r in rows)

    def test_saved_trade_fields(self, tmp_db, sample_trades):
        init_db(tmp_db)
        save_trades([sample_trades[0]], db_path=tmp_db)
        rows = query_trades(db_path=tmp_db)
        assert len(rows) == 1
        r = rows[0]
        assert r["symbol"] == "600519"
        assert r["direction"] == "BUY"
        assert r["price"] == 1800.0
        assert r["shares"] == 100
        assert r["amount"] == 180000.0
        assert r["commission"] == 54.0
        assert r["strategy"] == "dual_ma"


class TestSaveSignals:
    """信号记录存储测试"""

    def test_save_signals(self, tmp_db, sample_signals):
        init_db(tmp_db)
        count = save_signals(sample_signals, db_path=tmp_db)
        assert count == 2

    def test_save_empty_signals(self, tmp_db):
        init_db(tmp_db)
        count = save_signals([], db_path=tmp_db)
        assert count == 0


class TestSavePicks:
    """选股结果存储测试"""

    def test_save_picks(self, tmp_db, sample_picks):
        init_db(tmp_db)
        count = save_daily_picks(sample_picks, date="2025-06-01", db_path=tmp_db)
        assert count == 2

    def test_save_picks_dedup_by_date(self, tmp_db, sample_picks):
        """同一天重复保存应覆盖"""
        init_db(tmp_db)
        save_daily_picks(sample_picks, date="2025-06-01", db_path=tmp_db)
        save_daily_picks(sample_picks[:1], date="2025-06-01", db_path=tmp_db)
        rows = query_daily_picks(date="2025-06-01", db_path=tmp_db)
        assert len(rows) == 1  # 只剩1条

    def test_picks_json_fields(self, tmp_db, sample_picks):
        """prediction 和 reasons 应正确序列化/反序列化"""
        init_db(tmp_db)
        save_daily_picks(sample_picks, date="2025-06-01", db_path=tmp_db)
        rows = query_daily_picks(date="2025-06-01", db_path=tmp_db)
        r = rows[0]
        assert isinstance(r["prediction"], dict)
        assert isinstance(r["reasons"], list)


class TestQueryTrades:
    """交易记录查询测试"""

    def test_query_all(self, tmp_db, sample_trades):
        init_db(tmp_db)
        save_trades(sample_trades, db_path=tmp_db)
        rows = query_trades(db_path=tmp_db)
        assert len(rows) == 3

    def test_query_by_symbol(self, tmp_db, sample_trades):
        init_db(tmp_db)
        save_trades(sample_trades, db_path=tmp_db)
        rows = query_trades(symbol="600519", db_path=tmp_db)
        assert len(rows) == 2
        assert all(r["symbol"] == "600519" for r in rows)

    def test_query_by_date_range(self, tmp_db, sample_trades):
        init_db(tmp_db)
        save_trades(sample_trades, db_path=tmp_db)
        rows = query_trades(start_date="2025-03-10", end_date="2025-04-30", db_path=tmp_db)
        assert len(rows) == 2  # 000858 BUY on 03-15, 600519 SELL on 04-01

    def test_query_with_limit(self, tmp_db, sample_trades):
        init_db(tmp_db)
        save_trades(sample_trades, db_path=tmp_db)
        rows = query_trades(limit=1, db_path=tmp_db)
        assert len(rows) == 1

    def test_query_empty(self, tmp_db):
        init_db(tmp_db)
        rows = query_trades(db_path=tmp_db)
        assert len(rows) == 0


class TestQuerySignals:
    """信号记录查询测试"""

    def test_query_signals(self, tmp_db, sample_signals):
        init_db(tmp_db)
        save_signals(sample_signals, db_path=tmp_db)
        rows = query_signals(db_path=tmp_db)
        assert len(rows) == 2

    def test_query_signals_by_symbol(self, tmp_db, sample_signals):
        init_db(tmp_db)
        save_signals(sample_signals, db_path=tmp_db)
        rows = query_signals(symbol="600519", db_path=tmp_db)
        assert len(rows) == 1


class TestQueryPicks:
    """选股记录查询测试"""

    def test_query_picks_by_date(self, tmp_db, sample_picks):
        init_db(tmp_db)
        save_daily_picks(sample_picks, date="2025-06-01", db_path=tmp_db)
        save_daily_picks(sample_picks[:1], date="2025-06-02", db_path=tmp_db)
        rows = query_daily_picks(date="2025-06-01", db_path=tmp_db)
        assert len(rows) == 2

    def test_query_picks_by_symbol(self, tmp_db, sample_picks):
        init_db(tmp_db)
        save_daily_picks(sample_picks, date="2025-06-01", db_path=tmp_db)
        rows = query_daily_picks(symbol="600519", db_path=tmp_db)
        assert len(rows) == 1
        assert rows[0]["symbol"] == "600519"

    def test_query_picks_ordered_by_score(self, tmp_db, sample_picks):
        init_db(tmp_db)
        save_daily_picks(sample_picks, date="2025-06-01", db_path=tmp_db)
        rows = query_daily_picks(date="2025-06-01", db_path=tmp_db)
        # 按 score 降序
        assert rows[0]["score"] >= rows[1]["score"]
