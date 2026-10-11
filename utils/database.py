"""
SQLite 数据库模块 - 存储交易记录、信号记录、每日选股结果
"""
import sqlite3
import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional

DB_PATH = Path(__file__).parent.parent / "cache" / "quant.db"


def get_connection(db_path: str = None) -> sqlite3.Connection:
    """获取数据库连接"""
    path = db_path or str(DB_PATH)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str = None) -> None:
    """初始化数据库表结构"""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            price REAL NOT NULL,
            shares INTEGER NOT NULL,
            amount REAL NOT NULL,
            commission REAL DEFAULT 0,
            stamp_tax REAL DEFAULT 0,
            slippage REAL DEFAULT 0,
            date TEXT NOT NULL,
            strategy TEXT DEFAULT '',
            reason TEXT DEFAULT '',
            backtest_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            signal TEXT NOT NULL,
            price REAL NOT NULL,
            score REAL DEFAULT 0,
            date TEXT NOT NULL,
            strategy TEXT DEFAULT '',
            reason TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_picks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT DEFAULT '',
            score REAL DEFAULT 0,
            price REAL DEFAULT 0,
            pct_change REAL DEFAULT 0,
            market_cap REAL DEFAULT 0,
            pe REAL DEFAULT 0,
            prediction TEXT DEFAULT '{}',
            reasons TEXT DEFAULT '[]',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # 创建索引
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_date ON signals(date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_picks_date ON daily_picks(date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_picks_symbol ON daily_picks(symbol)")

    conn.commit()
    conn.close()


def save_trades(trades: List[Dict], backtest_id: str = "", db_path: str = None) -> int:
    """保存交易记录到数据库

    Args:
        trades: 交易记录列表，每条记录包含 symbol, direction, price, shares, amount, commission, date, strategy, reason 等字段
        backtest_id: 回测标识ID
        db_path: 数据库路径（测试用）

    Returns:
        插入的记录数
    """
    if not trades:
        return 0

    conn = get_connection(db_path)
    cursor = conn.cursor()

    count = 0
    for t in trades:
        cursor.execute("""
            INSERT INTO trades (symbol, direction, price, shares, amount,
                              commission, stamp_tax, slippage, date, strategy, reason, backtest_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            t.get("symbol", ""),
            t.get("direction", ""),
            t.get("price", 0),
            t.get("shares", 0),
            t.get("amount", 0),
            t.get("commission", 0),
            t.get("stamp_tax", 0),
            t.get("slippage", 0),
            t.get("date", ""),
            t.get("strategy", ""),
            t.get("reason", ""),
            backtest_id,
        ))
        count += 1

    conn.commit()
    conn.close()
    return count


def save_signals(signals: List[Dict], db_path: str = None) -> int:
    """保存信号记录到数据库

    Args:
        signals: 信号记录列表
        db_path: 数据库路径（测试用）

    Returns:
        插入的记录数
    """
    if not signals:
        return 0

    conn = get_connection(db_path)
    cursor = conn.cursor()

    count = 0
    for s in signals:
        cursor.execute("""
            INSERT INTO signals (symbol, signal, price, score, date, strategy, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            s.get("symbol", ""),
            s.get("signal", ""),
            s.get("price", 0),
            s.get("score", 0),
            s.get("date", ""),
            s.get("strategy", ""),
            s.get("reason", ""),
        ))
        count += 1

    conn.commit()
    conn.close()
    return count


def save_daily_picks(picks: List[Dict], date: str = None, db_path: str = None) -> int:
    """保存每日选股结果到数据库

    Args:
        picks: 选股结果列表
        date: 选股日期，默认为今天
        db_path: 数据库路径（测试用）

    Returns:
        插入的记录数
    """
    if not picks:
        return 0

    pick_date = date or datetime.now().strftime("%Y-%m-%d")
    conn = get_connection(db_path)
    cursor = conn.cursor()

    # 先删除当日已有记录（避免重复）
    cursor.execute("DELETE FROM daily_picks WHERE date = ?", (pick_date,))

    count = 0
    for p in picks:
        prediction = p.get("prediction", {})
        reasons = p.get("reasons", [])
        cursor.execute("""
            INSERT INTO daily_picks (date, symbol, name, score, price, pct_change,
                                     market_cap, pe, prediction, reasons)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pick_date,
            p.get("symbol", ""),
            p.get("name", ""),
            p.get("score", 0),
            p.get("price", 0),
            p.get("pct_change", 0),
            p.get("market_cap", 0),
            p.get("pe", 0),
            json.dumps(prediction, ensure_ascii=False),
            json.dumps(reasons, ensure_ascii=False),
        ))
        count += 1

    conn.commit()
    conn.close()
    return count


def query_trades(
    symbol: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 100,
    db_path: str = None,
) -> List[Dict]:
    """查询交易记录

    Args:
        symbol: 按股票代码过滤
        start_date: 开始日期
        end_date: 结束日期
        limit: 返回条数上限
        db_path: 数据库路径（测试用）

    Returns:
        交易记录列表
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()

    conditions = []
    params = []

    if symbol:
        conditions.append("symbol = ?")
        params.append(symbol)
    if start_date:
        conditions.append("date >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("date <= ?")
        params.append(end_date)

    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)

    cursor.execute(f"""
        SELECT * FROM trades {where}
        ORDER BY date DESC, id DESC
        LIMIT ?
    """, params + [limit])

    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def query_signals(
    symbol: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 100,
    db_path: str = None,
) -> List[Dict]:
    """查询信号记录"""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    conditions = []
    params = []

    if symbol:
        conditions.append("symbol = ?")
        params.append(symbol)
    if start_date:
        conditions.append("date >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("date <= ?")
        params.append(end_date)

    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)

    cursor.execute(f"""
        SELECT * FROM signals {where}
        ORDER BY date DESC, id DESC
        LIMIT ?
    """, params + [limit])

    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def query_daily_picks(
    date: Optional[str] = None,
    symbol: Optional[str] = None,
    limit: int = 100,
    db_path: str = None,
) -> List[Dict]:
    """查询每日选股记录"""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    conditions = []
    params = []

    if date:
        conditions.append("date = ?")
        params.append(date)
    if symbol:
        conditions.append("symbol = ?")
        params.append(symbol)

    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)

    cursor.execute(f"""
        SELECT * FROM daily_picks {where}
        ORDER BY date DESC, score DESC, id DESC
        LIMIT ?
    """, params + [limit])

    rows = []
    for row in cursor.fetchall():
        r = dict(row)
        # 解析 JSON 字段
        try:
            r["prediction"] = json.loads(r.get("prediction", "{}"))
        except (json.JSONDecodeError, TypeError):
            r["prediction"] = {}
        try:
            r["reasons"] = json.loads(r.get("reasons", "[]"))
        except (json.JSONDecodeError, TypeError):
            r["reasons"] = []
        rows.append(r)

    conn.close()
    return rows


# 模块初始化时自动建表
init_db()
