"""
模拟交易引擎 - Paper Trading
功能：
- 基于实时行情执行策略信号（模拟下单）
- 持仓管理：买入/卖出/加仓/减仓
- 交易日志：记录每笔交易的时间/价格/数量/盈亏
- 账户快照：每日保存账户状态（持仓+市值+现金+收益率）
- 持久化：使用SQLite存储交易记录和持仓
- 风控集成：复用 AdvancedRiskManager 的止损止盈逻辑
"""
import sqlite3
import json
import time
import logging
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import CONFIG

log = logging.getLogger("quant.trading")

# 数据库路径
DB_PATH = Path(__file__).parent.parent / "cache" / "paper_trading.db"


def _get_connection(db_path: str = None) -> sqlite3.Connection:
    """获取数据库连接（复用 utils/database.py 的模式）"""
    path = db_path or str(DB_PATH)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_paper_db(db_path: str = None) -> None:
    """初始化模拟交易数据库表结构"""
    conn = _get_connection(db_path)
    cursor = conn.cursor()

    # 模拟交易记录表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT DEFAULT '',
            direction TEXT NOT NULL,
            price REAL NOT NULL,
            shares INTEGER NOT NULL,
            amount REAL NOT NULL,
            commission REAL DEFAULT 0,
            stamp_tax REAL DEFAULT 0,
            pnl REAL DEFAULT 0,
            strategy TEXT DEFAULT '',
            reason TEXT DEFAULT '',
            date TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # 持仓表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS paper_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT DEFAULT '',
            shares INTEGER NOT NULL,
            avg_cost REAL NOT NULL,
            current_price REAL DEFAULT 0,
            highest_price REAL DEFAULT 0,
            entry_date TEXT NOT NULL,
            last_update TEXT DEFAULT (datetime('now', 'localtime')),
            UNIQUE(session_id, symbol)
        )
    """)

    # 账户快照表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS paper_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            date TEXT NOT NULL,
            cash REAL NOT NULL,
            market_value REAL DEFAULT 0,
            total_equity REAL NOT NULL,
            total_pnl REAL DEFAULT 0,
            total_return_pct REAL DEFAULT 0,
            positions_json TEXT DEFAULT '[]',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # 交易会话表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS paper_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL UNIQUE,
            strategy TEXT NOT NULL,
            initial_capital REAL NOT NULL,
            current_capital REAL NOT NULL,
            status TEXT DEFAULT 'running',
            started_at TEXT NOT NULL,
            stopped_at TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # 创建索引
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pt_session ON paper_trades(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pt_symbol ON paper_trades(symbol)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pt_date ON paper_trades(date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pp_session ON paper_positions(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ps_session ON paper_snapshots(session_id)")

    conn.commit()
    conn.close()


# 数据库初始化
init_paper_db()


@dataclass
class PaperPosition:
    """模拟持仓"""
    symbol: str
    name: str = ""
    shares: int = 0
    avg_cost: float = 0.0
    current_price: float = 0.0
    highest_price: float = 0.0
    entry_date: str = ""

    @property
    def market_value(self) -> float:
        return self.shares * self.current_price

    @property
    def pnl(self) -> float:
        return (self.current_price - self.avg_cost) * self.shares

    @property
    def pnl_pct(self) -> float:
        if self.avg_cost == 0:
            return 0.0
        return (self.current_price - self.avg_cost) / self.avg_cost

    @property
    def holding_days(self) -> int:
        if not self.entry_date:
            return 0
        try:
            entry = datetime.strptime(self.entry_date[:10], "%Y-%m-%d")
            return (datetime.now() - entry).days
        except Exception:
            return 0


@dataclass
class PaperTrade:
    """模拟交易记录"""
    symbol: str
    name: str = ""
    direction: str = "BUY"       # BUY / SELL
    price: float = 0.0
    shares: int = 0
    amount: float = 0.0
    commission: float = 0.0
    stamp_tax: float = 0.0
    pnl: float = 0.0
    strategy: str = ""
    reason: str = ""
    date: str = ""


class PaperTrader:
    """
    模拟交易引擎

    功能：
    1. 接收策略信号，执行模拟买卖
    2. 管理持仓（买入/卖出/加仓/减仓）
    3. 风控检查（止损止盈）
    4. 账户快照（每日收盘后保存）
    5. 交易日志持久化到SQLite
    """

    def __init__(self, session_id: str, strategy: str = "dual_ma",
                 initial_capital: float = 1000000.0, db_path: str = None):
        self.session_id = session_id
        self.strategy = strategy
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.db_path = db_path

        # 持仓: {symbol: PaperPosition}
        self.positions: Dict[str, PaperPosition] = {}

        # 交易记录
        self.trades: List[PaperTrade] = []

        # 盈亏曲线记录
        self.equity_curve: List[dict] = []

        # 状态
        self.status = "running"     # running / stopped
        self.started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.stopped_at = ""

        # 风控配置（从 CONFIG 读取）
        self.max_position_pct = CONFIG.risk.max_position_pct     # 单只最大仓位
        self.stop_loss_pct = CONFIG.risk.stop_loss_pct           # 止损线
        self.take_profit_pct = CONFIG.risk.take_profit_pct       # 止盈线

        # 线程锁（保护持仓和交易记录的并发访问）
        self._lock = threading.Lock()

        # 从数据库恢复（如果存在）
        self._load_state()

        log.info("PaperTrader 会话 %s 已创建, 策略=%s, 初始资金=%.0f",
                 session_id, strategy, initial_capital)

    def _db_conn(self) -> sqlite3.Connection:
        """获取数据库连接"""
        return _get_connection(self.db_path)

    def _save_session(self) -> None:
        """保存/更新会话信息"""
        conn = self._db_conn()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO paper_sessions
                (session_id, strategy, initial_capital, current_capital, status, started_at, stopped_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (self.session_id, self.strategy, self.initial_capital,
                  self.cash, self.status, self.started_at, self.stopped_at))
            conn.commit()
        finally:
            conn.close()

    def _load_state(self) -> None:
        """从数据库恢复会话状态"""
        conn = self._db_conn()
        try:
            # 恢复持仓
            rows = conn.execute(
                "SELECT * FROM paper_positions WHERE session_id = ?",
                (self.session_id,)
            ).fetchall()
            for r in rows:
                self.positions[r["symbol"]] = PaperPosition(
                    symbol=r["symbol"],
                    name=r["name"],
                    shares=r["shares"],
                    avg_cost=r["avg_cost"],
                    current_price=r["current_price"],
                    highest_price=r["highest_price"],
                    entry_date=r["entry_date"],
                )

            # 恢复交易记录
            trade_rows = conn.execute(
                "SELECT * FROM paper_trades WHERE session_id = ? ORDER BY id",
                (self.session_id,)
            ).fetchall()
            self.trades = [
                PaperTrade(
                    symbol=r["symbol"], name=r["name"],
                    direction=r["direction"], price=r["price"],
                    shares=r["shares"], amount=r["amount"],
                    commission=r["commission"], stamp_tax=r["stamp_tax"],
                    pnl=r["pnl"], strategy=r["strategy"],
                    reason=r["reason"], date=r["date"],
                ) for r in trade_rows
            ]

            # 恢复盈亏曲线
            snap_rows = conn.execute(
                "SELECT * FROM paper_snapshots WHERE session_id = ? ORDER BY date",
                (self.session_id,)
            ).fetchall()
            self.equity_curve = [
                {"date": r["date"], "total_equity": r["total_equity"],
                 "cash": r["cash"], "market_value": r["market_value"],
                 "total_pnl": r["total_pnl"], "total_return_pct": r["total_return_pct"]}
                for r in snap_rows
            ]
        finally:
            conn.close()

    def _calc_commission(self, amount: float, direction: str) -> Tuple[float, float]:
        """
        计算交易费用（佣金 + 印花税）
        A股规则：佣金万三（最低5元），印花税千一（仅卖出）
        """
        commission = max(amount * CONFIG.backtest.commission_rate, CONFIG.backtest.min_commission)
        stamp_tax = amount * CONFIG.backtest.stamp_tax_rate if direction == "SELL" else 0.0
        return round(commission, 2), round(stamp_tax, 2)

    def _calc_slippage_price(self, price: float, direction: str) -> float:
        """计算含滑点的实际成交价"""
        slippage = CONFIG.backtest.slippage
        if direction == "BUY":
            return round(price * (1 + slippage), 2)
        else:
            return round(price * (1 - slippage), 2)

    def execute_signal(self, symbol: str, signal: str, price: float,
                       name: str = "", reason: str = "",
                       score: float = 0.5) -> Optional[PaperTrade]:
        """
        执行策略信号

        Args:
            symbol: 股票代码
            signal: "BUY" / "SELL"
            price: 信号价格
            name: 股票名称
            reason: 信号原因
            score: 信号强度

        Returns:
            交易记录（如果执行了交易）
        """
        with self._lock:
            if self.status != "running":
                return None

            if signal == "BUY":
                return self._execute_buy(symbol, name, price, reason, score)
            elif signal == "SELL":
                return self._execute_sell(symbol, name, price, reason)
            return None

    def _execute_buy(self, symbol: str, name: str, price: float,
                     reason: str, score: float) -> Optional[PaperTrade]:
        """执行买入"""
        # 计算可买数量（考虑风控仓位限制）
        total_equity = self._calc_total_equity()
        max_position_value = total_equity * self.max_position_pct

        # 已有持仓的市值
        existing_value = 0
        if symbol in self.positions:
            existing_value = self.positions[symbol].market_value

        # 可用于加仓的金额
        available_for_position = max(0, max_position_value - existing_value)
        # 可用现金
        available_cash = min(self.cash * 0.95, available_for_position)  # 保留5%现金

        if available_cash < price * 100:  # 至少买1手（100股）
            log.debug("资金不足, 无法买入 %s", symbol)
            return None

        # 实际成交价（含滑点）
        exec_price = self._calc_slippage_price(price, "BUY")
        # 计算买入数量（100股整数倍）
        shares = int(available_cash / exec_price / 100) * 100
        if shares <= 0:
            return None

        amount = round(exec_price * shares, 2)
        commission, stamp_tax = self._calc_commission(amount, "BUY")
        total_cost = amount + commission + stamp_tax

        # 检查现金是否充足
        if total_cost > self.cash:
            shares -= 100
            if shares <= 0:
                return None
            amount = round(exec_price * shares, 2)
            commission, stamp_tax = self._calc_commission(amount, "BUY")
            total_cost = amount + commission + stamp_tax

        # 更新现金
        self.cash -= total_cost

        # 更新持仓（加仓逻辑：合并持仓计算平均成本）
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if symbol in self.positions:
            pos = self.positions[symbol]
            total_shares = pos.shares + shares
            pos.avg_cost = round(
                (pos.avg_cost * pos.shares + exec_price * shares) / total_shares, 4
            )
            pos.shares = total_shares
            pos.current_price = exec_price
            pos.highest_price = max(pos.highest_price, exec_price)
        else:
            self.positions[symbol] = PaperPosition(
                symbol=symbol,
                name=name,
                shares=shares,
                avg_cost=exec_price,
                current_price=exec_price,
                highest_price=exec_price,
                entry_date=now_str[:10],
            )

        # 记录交易
        trade = PaperTrade(
            symbol=symbol, name=name,
            direction="BUY", price=exec_price,
            shares=shares, amount=amount,
            commission=commission, stamp_tax=stamp_tax,
            pnl=0.0,  # 买入时盈亏为0
            strategy=self.strategy, reason=reason,
            date=now_str,
        )
        self.trades.append(trade)

        # 持久化
        self._persist_trade(trade)
        self._persist_position(self.positions[symbol])

        log.info("买入 %s %s %d股 @ %.2f, 金额=%.2f, 原因=%s",
                 symbol, name, shares, exec_price, amount, reason)

        return trade

    def _execute_sell(self, symbol: str, name: str, price: float,
                      reason: str) -> Optional[PaperTrade]:
        """执行卖出（全仓卖出）"""
        if symbol not in self.positions:
            log.debug("无持仓, 无法卖出 %s", symbol)
            return None

        pos = self.positions[symbol]
        if pos.shares <= 0:
            return None

        # 实际成交价（含滑点）
        exec_price = self._calc_slippage_price(price, "SELL")
        shares = pos.shares
        amount = round(exec_price * shares, 2)
        commission, stamp_tax = self._calc_commission(amount, "SELL")
        net_amount = amount - commission - stamp_tax

        # 计算盈亏
        pnl = round((exec_price - pos.avg_cost) * shares - commission - stamp_tax, 2)

        # 更新现金
        self.cash += net_amount

        # 删除持仓
        del self.positions[symbol]

        # 记录交易
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        trade = PaperTrade(
            symbol=symbol, name=name,
            direction="SELL", price=exec_price,
            shares=shares, amount=amount,
            commission=commission, stamp_tax=stamp_tax,
            pnl=pnl,
            strategy=self.strategy, reason=reason,
            date=now_str,
        )
        self.trades.append(trade)

        # 持久化
        self._persist_trade(trade)
        self._remove_position(symbol)

        log.info("卖出 %s %s %d股 @ %.2f, 金额=%.2f, 盈亏=%.2f, 原因=%s",
                 symbol, name, shares, exec_price, amount, pnl, reason)

        return trade

    def update_prices(self, prices: Dict[str, float]) -> None:
        """
        更新持仓实时价格

        Args:
            prices: {symbol: current_price}
        """
        with self._lock:
            for symbol, price in prices.items():
                if symbol in self.positions:
                    pos = self.positions[symbol]
                    pos.current_price = price
                    pos.highest_price = max(pos.highest_price, price)

    def check_risk_control(self) -> List[PaperTrade]:
        """
        风控检查：止损/止盈

        Returns:
            触发风控导致的卖出交易列表
        """
        risk_trades = []
        with self._lock:
            if self.status != "running":
                return risk_trades

            symbols_to_sell = []
            for symbol, pos in self.positions.items():
                if pos.shares <= 0:
                    continue

                pnl_pct = pos.pnl_pct

                # 止损检查
                if pnl_pct <= -self.stop_loss_pct:
                    log.info("触发止损: %s 亏损 %.2f%%", symbol, pnl_pct * 100)
                    symbols_to_sell.append((symbol, f"止损: 亏损{pnl_pct*100:.1f}%"))

                # 止盈检查
                elif pnl_pct >= self.take_profit_pct:
                    log.info("触发止盈: %s 盈利 %.2f%%", symbol, pnl_pct * 100)
                    symbols_to_sell.append((symbol, f"止盈: 盈利{pnl_pct*100:.1f}%"))

                # 追踪止损（从最高点回撤超过阈值）
                elif pos.highest_price > pos.avg_cost:
                    drawdown = (pos.highest_price - pos.current_price) / pos.highest_price
                    if drawdown >= CONFIG.risk.trailing_stop_pct and pnl_pct > 0:
                        log.info("触发追踪止损: %s 从高点回撤 %.2f%%",
                                 symbol, drawdown * 100)
                        symbols_to_sell.append(
                            (symbol, f"追踪止损: 从高点回撤{drawdown*100:.1f}%")
                        )

            # 执行风控卖出
            for symbol, reason in symbols_to_sell:
                pos = self.positions.get(symbol)
                if pos:
                    trade = self._execute_sell(symbol, pos.name, pos.current_price, reason)
                    if trade:
                        risk_trades.append(trade)

        return risk_trades

    def take_snapshot(self, date: str = None) -> dict:
        """
        保存账户快照（每日收盘后调用）

        Args:
            date: 快照日期，默认为今天

        Returns:
            快照数据
        """
        with self._lock:
            snap_date = date or datetime.now().strftime("%Y-%m-%d")
            total_equity = self._calc_total_equity()
            market_value = sum(p.market_value for p in self.positions.values())
            total_pnl = total_equity - self.initial_capital
            total_return_pct = round(total_pnl / self.initial_capital * 100, 4)

            # 持仓列表序列化
            positions_data = [
                {
                    "symbol": p.symbol, "name": p.name,
                    "shares": p.shares, "avg_cost": p.avg_cost,
                    "current_price": p.current_price,
                    "market_value": round(p.market_value, 2),
                    "pnl": round(p.pnl, 2),
                    "pnl_pct": round(p.pnl_pct * 100, 2),
                } for p in self.positions.values()
            ]

            snapshot = {
                "date": snap_date,
                "cash": round(self.cash, 2),
                "market_value": round(market_value, 2),
                "total_equity": round(total_equity, 2),
                "total_pnl": round(total_pnl, 2),
                "total_return_pct": total_return_pct,
                "positions": positions_data,
            }

            # 更新盈亏曲线
            self.equity_curve.append({
                "date": snap_date,
                "total_equity": round(total_equity, 2),
                "cash": round(self.cash, 2),
                "market_value": round(market_value, 2),
                "total_pnl": round(total_pnl, 2),
                "total_return_pct": total_return_pct,
            })

            # 持久化
            self._persist_snapshot(snapshot)

            log.info("账户快照 %s: 总资产=%.2f, 现金=%.2f, 市值=%.2f, 盈亏=%.2f (%.2f%%)",
                     snap_date, total_equity, self.cash, market_value,
                     total_pnl, total_return_pct)

            return snapshot

    def _calc_total_equity(self) -> float:
        """计算账户总资产"""
        market_value = sum(p.market_value for p in self.positions.values())
        return self.cash + market_value

    def get_status(self) -> dict:
        """获取当前交易状态"""
        with self._lock:
            total_equity = self._calc_total_equity()
            market_value = sum(p.market_value for p in self.positions.values())
            total_pnl = total_equity - self.initial_capital
            today_pnl = self._calc_today_pnl()

            return {
                "session_id": self.session_id,
                "strategy": self.strategy,
                "status": self.status,
                "started_at": self.started_at,
                "stopped_at": self.stopped_at,
                "initial_capital": self.initial_capital,
                "cash": round(self.cash, 2),
                "market_value": round(market_value, 2),
                "total_equity": round(total_equity, 2),
                "total_pnl": round(total_pnl, 2),
                "total_return_pct": round(total_pnl / self.initial_capital * 100, 2) if self.initial_capital else 0,
                "today_pnl": round(today_pnl, 2),
                "positions_count": len(self.positions),
                "trades_count": len(self.trades),
            }

    def get_positions(self) -> List[dict]:
        """获取当前持仓列表"""
        with self._lock:
            return [
                {
                    "symbol": p.symbol,
                    "name": p.name,
                    "shares": p.shares,
                    "avg_cost": round(p.avg_cost, 4),
                    "current_price": round(p.current_price, 2),
                    "highest_price": round(p.highest_price, 2),
                    "market_value": round(p.market_value, 2),
                    "pnl": round(p.pnl, 2),
                    "pnl_pct": round(p.pnl_pct * 100, 2),
                    "holding_days": p.holding_days,
                    "entry_date": p.entry_date,
                } for p in self.positions.values()
            ]

    def get_trade_history(self, limit: int = 200) -> List[dict]:
        """获取交易历史记录"""
        with self._lock:
            trades = self.trades[-limit:]
            return [
                {
                    "symbol": t.symbol,
                    "name": t.name,
                    "direction": t.direction,
                    "price": round(t.price, 2),
                    "shares": t.shares,
                    "amount": round(t.amount, 2),
                    "commission": round(t.commission, 2),
                    "stamp_tax": round(t.stamp_tax, 2),
                    "pnl": round(t.pnl, 2),
                    "strategy": t.strategy,
                    "reason": t.reason,
                    "date": t.date,
                } for t in reversed(trades)
            ]

    def get_equity_curve(self) -> List[dict]:
        """获取盈亏曲线数据"""
        with self._lock:
            return list(self.equity_curve)

    def stop(self) -> None:
        """停止交易"""
        with self._lock:
            self.status = "stopped"
            self.stopped_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._save_session()
            log.info("PaperTrader 会话 %s 已停止", self.session_id)

    def _calc_today_pnl(self) -> float:
        """计算今日盈亏"""
        today = datetime.now().strftime("%Y-%m-%d")
        today_pnl = 0.0
        for t in self.trades:
            if t.date.startswith(today):
                today_pnl += t.pnl
        return today_pnl

    # ==================== 持久化方法 ====================

    def _persist_trade(self, trade: PaperTrade) -> None:
        """保存交易记录到数据库"""
        conn = self._db_conn()
        try:
            conn.execute("""
                INSERT INTO paper_trades
                (session_id, symbol, name, direction, price, shares, amount,
                 commission, stamp_tax, pnl, strategy, reason, date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                self.session_id, trade.symbol, trade.name,
                trade.direction, trade.price, trade.shares, trade.amount,
                trade.commission, trade.stamp_tax, trade.pnl,
                trade.strategy, trade.reason, trade.date,
            ))
            conn.commit()
        finally:
            conn.close()

    def _persist_position(self, pos: PaperPosition) -> None:
        """保存/更新持仓到数据库"""
        conn = self._db_conn()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO paper_positions
                (session_id, symbol, name, shares, avg_cost, current_price,
                 highest_price, entry_date, last_update)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
            """, (
                self.session_id, pos.symbol, pos.name,
                pos.shares, pos.avg_cost, pos.current_price,
                pos.highest_price, pos.entry_date,
            ))
            conn.commit()
        finally:
            conn.close()

    def _remove_position(self, symbol: str) -> None:
        """从数据库删除持仓"""
        conn = self._db_conn()
        try:
            conn.execute(
                "DELETE FROM paper_positions WHERE session_id = ? AND symbol = ?",
                (self.session_id, symbol)
            )
            conn.commit()
        finally:
            conn.close()

    def _persist_snapshot(self, snapshot: dict) -> None:
        """保存账户快照到数据库"""
        conn = self._db_conn()
        try:
            conn.execute("""
                INSERT INTO paper_snapshots
                (session_id, date, cash, market_value, total_equity,
                 total_pnl, total_return_pct, positions_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                self.session_id, snapshot["date"],
                snapshot["cash"], snapshot["market_value"],
                snapshot["total_equity"], snapshot["total_pnl"],
                snapshot["total_return_pct"],
                json.dumps(snapshot["positions"], ensure_ascii=False),
            ))
            conn.commit()
        finally:
            conn.close()


# ==================== 全局管理器 ====================

class PaperTradingManager:
    """
    模拟交易管理器（单例）
    管理多个交易会话
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.active_trader: Optional[PaperTrader] = None
        self._lock = threading.Lock()

    def start_trading(self, strategy: str = "dual_ma",
                      initial_capital: float = 1000000.0) -> dict:
        """
        启动模拟交易

        Args:
            strategy: 策略名称
            initial_capital: 初始资金

        Returns:
            交易状态
        """
        with self._lock:
            # 如果已有运行中的交易，先停止
            if self.active_trader and self.active_trader.status == "running":
                self.active_trader.stop()

            # 创建新会话
            session_id = f"paper_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            self.active_trader = PaperTrader(
                session_id=session_id,
                strategy=strategy,
                initial_capital=initial_capital,
            )
            self.active_trader._save_session()

            return self.active_trader.get_status()

    def stop_trading(self) -> dict:
        """停止模拟交易"""
        with self._lock:
            if self.active_trader and self.active_trader.status == "running":
                self.active_trader.stop()
                return self.active_trader.get_status()
            return {"status": "not_running", "message": "无运行中的模拟交易"}

    def get_trader(self) -> Optional[PaperTrader]:
        """获取当前活跃的交易引擎"""
        return self.active_trader

    def get_status(self) -> dict:
        """获取交易状态"""
        if self.active_trader:
            return self.active_trader.get_status()
        return {
            "status": "not_started",
            "session_id": "",
            "strategy": "",
            "initial_capital": 0,
            "cash": 0,
            "market_value": 0,
            "total_equity": 0,
            "total_pnl": 0,
            "total_return_pct": 0,
            "today_pnl": 0,
            "positions_count": 0,
            "trades_count": 0,
        }
