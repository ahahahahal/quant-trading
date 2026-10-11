"""
本地SQLite数据库 - 预下载所有数据，API请求走本地（零延迟）
解决baostock网络延迟导致的页面加载慢问题
"""
import sqlite3
import logging
import time
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List
import pandas as pd

log = logging.getLogger("quant.data.local_db")

DB_PATH = Path(__file__).parent.parent / "cache" / "quant_local.db"


class LocalDatabase:
    """
    本地SQLite数据库管理器
    - 启动时预下载股票列表 + 热门股K线
    - API读取全部走本地（<1ms）
    - 后台定时刷新（每小时）
    """

    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(DB_PATH)
        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self):
        """获取数据库连接（每次新建，避免线程问题）"""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")  # 写入不阻塞读取
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=10000")
        return conn

    def _init_db(self):
        """初始化数据库表结构"""
        conn = self._get_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS stock_list (
                    symbol TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS stock_daily (
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    amount REAL,
                    turnover REAL,
                    pct_change REAL,
                    change REAL,
                    amplitude REAL,
                    PRIMARY KEY (symbol, date)
                );

                CREATE TABLE IF NOT EXISTS stock_minute (
                    symbol TEXT NOT NULL,
                    datetime TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    amount REAL,
                    period TEXT NOT NULL DEFAULT '5',
                    PRIMARY KEY (symbol, datetime, period)
                );

                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_daily_symbol ON stock_daily(symbol);
                CREATE INDEX IF NOT EXISTS idx_daily_date ON stock_daily(date);
                CREATE INDEX IF NOT EXISTS idx_minute_symbol ON stock_minute(symbol);
                CREATE INDEX IF NOT EXISTS idx_minute_period ON stock_minute(period);
            """)
            conn.commit()
            log.info("本地数据库初始化完成: %s", self.db_path)
        finally:
            conn.close()

    # ======================== 股票列表 ========================

    def save_stock_list(self, df: pd.DataFrame):
        """保存股票列表到本地"""
        if df.empty:
            return
        conn = self._get_conn()
        try:
            with conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO stock_list (symbol, name, updated_at) VALUES (?, ?, ?)",
                    [(row['symbol'], row['name'], datetime.now().isoformat()) for _, row in df.iterrows()]
                )
            log.info("股票列表已保存到本地: %d只", len(df))
        finally:
            conn.close()

    def get_stock_list(self) -> pd.DataFrame:
        """从本地读取股票列表（零延迟）"""
        conn = self._get_conn()
        try:
            df = pd.read_sql_query("SELECT symbol, name FROM stock_list ORDER BY symbol", conn)
            return df
        finally:
            conn.close()

    # ======================== 日K线 ========================

    def save_daily(self, symbol: str, df: pd.DataFrame):
        """保存日K线数据"""
        if df.empty:
            return
        conn = self._get_conn()
        try:
            with conn:
                records = []
                for _, row in df.iterrows():
                    records.append((
                        symbol,
                        str(row.get('date', ''))[:10],
                        row.get('open'), row.get('high'), row.get('low'), row.get('close'),
                        row.get('volume'), row.get('amount'), row.get('turnover'),
                        row.get('pct_change'), row.get('change'), row.get('amplitude')
                    ))
                conn.executemany(
                    "INSERT OR REPLACE INTO stock_daily (symbol, date, open, high, low, close, volume, amount, turnover, pct_change, change, amplitude) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    records
                )
            log.debug("保存 %s 日K线: %d条", symbol, len(records))
        finally:
            conn.close()

    def get_daily(self, symbol: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """从本地读取日K线（零延迟）"""
        conn = self._get_conn()
        try:
            query = "SELECT * FROM stock_daily WHERE symbol = ?"
            params = [symbol]
            if start_date:
                query += " AND date >= ?"
                params.append(start_date)
            if end_date:
                query += " AND date <= ?"
                params.append(end_date)
            query += " ORDER BY date"
            df = pd.read_sql_query(query, conn, params=params)
            if not df.empty:
                df['date'] = pd.to_datetime(df['date'])
            return df
        finally:
            conn.close()

    def get_latest_date(self, symbol: str) -> Optional[str]:
        """获取某只股票最新数据日期"""
        conn = self._get_conn()
        try:
            cur = conn.execute("SELECT MAX(date) FROM stock_daily WHERE symbol = ?", (symbol,))
            row = cur.fetchone()
            return row[0] if row and row[0] else None
        finally:
            conn.close()

    # ======================== 分钟线 ========================

    def save_minute(self, symbol: str, period: str, df: pd.DataFrame):
        """保存分钟线数据"""
        if df.empty:
            return
        conn = self._get_conn()
        try:
            with conn:
                records = []
                for _, row in df.iterrows():
                    records.append((
                        symbol,
                        str(row.get('datetime', row.get('date', ''))),
                        row.get('open'), row.get('high'), row.get('low'), row.get('close'),
                        row.get('volume'), row.get('amount'), period
                    ))
                conn.executemany(
                    "INSERT OR REPLACE INTO stock_minute (symbol, datetime, open, high, low, close, volume, amount, period) VALUES (?,?,?,?,?,?,?,?,?)",
                    records
                )
        finally:
            conn.close()

    def get_minute(self, symbol: str, period: str = '5', days: int = 30) -> pd.DataFrame:
        """从本地读取分钟线（零延迟）"""
        conn = self._get_conn()
        try:
            cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            df = pd.read_sql_query(
                "SELECT * FROM stock_minute WHERE symbol = ? AND period = ? AND datetime >= ? ORDER BY datetime",
                conn, params=[symbol, period, cutoff]
            )
            return df
        finally:
            conn.close()

    # ======================== 元数据 ========================

    def set_meta(self, key: str, value: str):
        conn = self._get_conn()
        try:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, value, datetime.now().isoformat())
                )
        finally:
            conn.close()

    def get_meta(self, key: str) -> Optional[str]:
        conn = self._get_conn()
        try:
            cur = conn.execute("SELECT value FROM meta WHERE key = ?", (key,))
            row = cur.fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    # ======================== 统计 ========================

    def stats(self) -> dict:
        """数据库统计信息"""
        conn = self._get_conn()
        try:
            stock_count = conn.execute("SELECT COUNT(*) FROM stock_list").fetchone()[0]
            daily_count = conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0]
            minute_count = conn.execute("SELECT COUNT(*) FROM stock_minute").fetchone()[0]
            symbols_with_daily = conn.execute("SELECT COUNT(DISTINCT symbol) FROM stock_daily").fetchone()[0]
            latest = conn.execute("SELECT MAX(date) FROM stock_daily").fetchone()[0]
            return {
                "stocks": stock_count,
                "daily_records": daily_count,
                "minute_records": minute_count,
                "symbols_with_data": symbols_with_daily,
                "latest_date": latest,
                "db_size_mb": round(Path(self.db_path).stat().st_size / 1024 / 1024, 2) if Path(self.db_path).exists() else 0
            }
        finally:
            conn.close()


# 全局单例
_db = None

def get_local_db() -> LocalDatabase:
    global _db
    if _db is None:
        _db = LocalDatabase()
    return _db
