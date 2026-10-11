"""
定时任务调度器 - 基于APScheduler
支持: 每日选股、盘中信号扫描、收盘分析、数据更新
任务结果持久化到SQLite，任务执行日志
"""
import logging
import json
import time
import sqlite3
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR

log = logging.getLogger("quant.scheduler")

# 项目根目录
ROOT = Path(__file__).parent.parent
SCHEDULER_DB = ROOT / "cache" / "scheduler.db"


def _get_db_connection() -> sqlite3.Connection:
    """获取调度器数据库连接"""
    SCHEDULER_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(SCHEDULER_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_scheduler_db():
    """初始化调度器数据库表"""
    conn = _get_db_connection()
    cursor = conn.cursor()

    # 任务执行日志表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS task_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            start_time TEXT NOT NULL,
            end_time TEXT,
            duration_sec REAL DEFAULT 0,
            result_summary TEXT DEFAULT '',
            error_msg TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # 每日选股结果表（调度器专用，与utils/database.py中的daily_picks表配合）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scheduler_picks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT DEFAULT '',
            strategy TEXT DEFAULT '',
            signal_strength REAL DEFAULT 0,
            price REAL DEFAULT 0,
            target_price REAL DEFAULT 0,
            stop_loss REAL DEFAULT 0,
            reasons TEXT DEFAULT '[]',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # 盘中信号表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scheduler_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            symbol TEXT NOT NULL,
            signal_type TEXT NOT NULL,
            price REAL DEFAULT 0,
            score REAL DEFAULT 0,
            strategy TEXT DEFAULT '',
            reason TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # 每日报告表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            report_html TEXT DEFAULT '',
            report_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # 任务配置表（启用/禁用状态）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS task_config (
            task_name TEXT PRIMARY KEY,
            enabled INTEGER DEFAULT 1,
            last_modified TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # 创建索引
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_logs_name ON task_logs(task_name)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_task_logs_time ON task_logs(start_time)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_picks_date ON scheduler_picks(date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_date ON scheduler_signals(date)")

    conn.commit()
    conn.close()


# 初始化数据库
_init_scheduler_db()


def _is_trading_day() -> bool:
    """判断今天是否为交易日（简单判断：周一到周五）"""
    now = datetime.now()
    return now.weekday() < 5


def _is_trading_time() -> bool:
    """判断当前是否为盘中交易时间 (09:30-11:30, 13:00-15:00)"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    hm = now.hour * 100 + now.minute
    return (930 <= hm <= 1130) or (1300 <= hm < 1500)


class TaskScheduler:
    """
    基于APScheduler的量化交易任务调度器

    支持的任务：
    - daily_stock_pick:   每日选股任务（每天09:00）
    - intraday_scan:      盘中信号扫描（每30分钟，09:30-15:00）
    - closing_analysis:   收盘分析任务（每天15:30）
    - data_update:        数据更新任务（每天08:00）
    """

    # 任务名称映射
    TASK_NAMES = {
        "daily_stock_pick": "每日选股",
        "intraday_scan": "盘中信号扫描",
        "closing_analysis": "收盘分析",
        "data_update": "数据更新",
    }

    def __init__(self):
        self.scheduler = BackgroundScheduler(
            timezone="Asia/Shanghai",
            job_defaults={
                "coalesce": True,        # 错过的任务合并执行
                "max_instances": 1,       # 同一任务最多1个实例
                "misfire_grace_time": 300, # 错过5分钟内仍执行
            }
        )
        self._running = False
        self._task_funcs: Dict[str, Callable] = {}
        self._setup_jobs()
        self._setup_listeners()

    def _setup_listeners(self):
        """设置任务执行监听器"""
        self.scheduler.add_listener(self._on_job_executed, EVENT_JOB_EXECUTED)
        self.scheduler.add_listener(self._on_job_error, EVENT_JOB_ERROR)

    def _on_job_executed(self, event):
        """任务执行成功回调"""
        log.info("任务 %s 执行完成", event.job_id)

    def _on_job_error(self, event):
        """任务执行失败回调"""
        log.error("任务 %s 执行失败: %s", event.job_id, event.exception)

    def _setup_jobs(self):
        """配置所有定时任务"""

        # 1. 每日选股任务 - 每天09:00
        self.scheduler.add_job(
            self._run_task_wrapper,
            trigger=CronTrigger(hour=9, minute=0, day_of_week="mon-fri"),
            id="daily_stock_pick",
            name="每日选股",
            args=["daily_stock_pick", self._task_daily_stock_pick],
            replace_existing=True,
        )

        # 2. 盘中信号扫描 - 每30分钟，交易时间内
        self.scheduler.add_job(
            self._run_task_wrapper,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour="9-14",
                minute="*/30",
            ),
            id="intraday_scan",
            name="盘中信号扫描",
            args=["intraday_scan", self._task_intraday_scan],
            replace_existing=True,
        )

        # 3. 收盘分析任务 - 每天15:30
        self.scheduler.add_job(
            self._run_task_wrapper,
            trigger=CronTrigger(hour=15, minute=30, day_of_week="mon-fri"),
            id="closing_analysis",
            name="收盘分析",
            args=["closing_analysis", self._task_closing_analysis],
            replace_existing=True,
        )

        # 4. 数据更新任务 - 每天08:00
        self.scheduler.add_job(
            self._run_task_wrapper,
            trigger=CronTrigger(hour=8, minute=0, day_of_week="mon-fri"),
            id="data_update",
            name="数据更新",
            args=["data_update", self._task_data_update],
            replace_existing=True,
        )

    def start(self):
        """启动调度器"""
        if not self._running:
            self.scheduler.start()
            self._running = True
            log.info("任务调度器已启动")

    def stop(self):
        """停止调度器"""
        if self._running:
            self.scheduler.shutdown(wait=False)
            self._running = False
            log.info("任务调度器已停止")

    @property
    def is_running(self) -> bool:
        return self._running

    # ==================== 任务执行包装器 ====================

    def _run_task_wrapper(self, task_name: str, task_func: Callable):
        """
        任务执行包装器：检查启用状态、记录日志、异常处理
        """
        # 检查任务是否启用
        if not self._is_task_enabled(task_name):
            log.info("任务 %s 已禁用，跳过执行", task_name)
            return

        # 记录开始
        start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_id = self._log_task_start(task_name, start_time)

        try:
            result = task_func()
            end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            duration = (datetime.now() - datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")).total_seconds()
            summary = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)[:500]
            self._log_task_end(log_id, "success", end_time, duration, summary)
            log.info("任务 %s 执行成功 (%.1f秒): %s", task_name, duration, summary[:100])
        except Exception as e:
            end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            duration = (datetime.now() - datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")).total_seconds()
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
            self._log_task_end(log_id, "error", end_time, duration, "", error_msg)
            log.error("任务 %s 执行失败: %s", task_name, str(e))

    # ==================== 任务实现 ====================

    def _task_daily_stock_pick(self) -> dict:
        """
        每日选股任务
        扫描全市场，用多因子策略选出20只股票，保存到数据库
        """
        from strategy.stock_picker import DailyStockSelector
        from utils.database import save_daily_picks

        today = datetime.now().strftime("%Y-%m-%d")
        log.info("[每日选股] 开始执行，日期: %s", today)

        # 执行选股
        selector = DailyStockSelector()
        picks = selector.select_daily(n=20)

        if not picks:
            log.warning("[每日选股] 未选出任何股票")
            return {"date": today, "count": 0, "picks": []}

        # 保存到调度器专用表
        conn = _get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM scheduler_picks WHERE date = ?", (today,))

        pick_records = []
        for pick in picks:
            # 计算目标价和止损价
            target_price = round(pick.price * 1.15, 2)  # 目标涨幅15%
            stop_loss = round(pick.price * 0.93, 2)      # 止损线7%

            # 获取主要策略
            main_strategy = ""
            if pick.prediction:
                signals = pick.prediction.get("signals", [])
                if signals:
                    main_strategy = signals[0] if isinstance(signals[0], str) else str(signals[0])

            cursor.execute("""
                INSERT INTO scheduler_picks (date, symbol, name, strategy, signal_strength,
                                            price, target_price, stop_loss, reasons)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                today,
                pick.symbol,
                pick.name,
                main_strategy,
                pick.score / 100.0,  # 归一化到0-1
                pick.price,
                target_price,
                stop_loss,
                json.dumps(pick.reasons, ensure_ascii=False),
            ))

            pick_records.append({
                "symbol": pick.symbol,
                "name": pick.name,
                "strategy": main_strategy,
                "signal_strength": round(pick.score / 100.0, 2),
                "price": pick.price,
                "target_price": target_price,
                "stop_loss": stop_loss,
                "score": pick.score,
            })

        conn.commit()
        conn.close()

        # 同时保存到通用数据库
        try:
            save_daily_picks(
                [{"symbol": p.symbol, "name": p.name, "score": p.score,
                  "price": p.price, "pct_change": p.pct_change,
                  "market_cap": p.market_cap, "pe": p.pe,
                  "prediction": p.prediction, "reasons": p.reasons}
                 for p in picks],
                date=today,
            )
        except Exception as e:
            log.warning("[每日选股] 保存到通用数据库失败: %s", e)

        result = {"date": today, "count": len(pick_records), "picks": pick_records}
        log.info("[每日选股] 完成，选出 %d 只股票", len(pick_records))
        return result

    def _task_intraday_scan(self) -> dict:
        """
        盘中信号扫描 + 自动执行模拟交易
        对监控列表中的股票扫描交易信号（仅在交易时间执行）
        """
        # 额外检查：非交易时间不执行
        if not _is_trading_time():
            log.info("[盘中扫描] 当前非交易时间，跳过执行")
            return {"skipped": True, "reason": "非交易时间"}

        from signals.aggregator import SignalAggregator
        from data.provider import get_data
        from data.local_db import get_local_db
        from trading.paper_trader import PaperTradingManager

        now = datetime.now()
        scan_time = now.strftime("%H:%M")
        today = now.strftime("%Y-%m-%d")
        log.info("[盘中扫描] 开始执行，时间: %s", scan_time)

        # 获取监控列表（使用热门股票 + 今日选股）
        watch_symbols = self._get_watch_list()

        if not watch_symbols:
            log.info("[盘中扫描] 监控列表为空")
            return {"date": today, "time": scan_time, "signals": [], "count": 0}

        aggregator = SignalAggregator()
        signals_found = []
        trades_executed = []

        # 获取模拟交易管理器
        paper_manager = PaperTradingManager()
        trader = paper_manager.get_trader()

        # 信号强度阈值（低于此分数不触发交易）
        MIN_TRADE_SCORE = 0.6

        for sym_info in watch_symbols:
            symbol = sym_info["symbol"]
            try:
                # 优先从本地读取 + 计算技术指标
                local_db = get_local_db()
                df = local_db.get_daily(symbol)
                if df.empty or len(df) < 30:
                    # 本地数据不足时才联网
                    try:
                        df = get_data(symbol)
                    except Exception:
                        continue
                    if df.empty:
                        continue
                else:
                    # 本地数据需要计算技术指标
                    try:
                        from data.alpha158 import Alpha158
                        df = Alpha158.compute_all(df)
                    except Exception as e:
                        log.debug("[盘中扫描] %s 计算因子失败: %s", symbol, e)
                        continue

                signals = aggregator.aggregate(df)
                if signals:
                    # 取最新信号
                    latest = signals[-1]
                    sig_data = {
                        "date": today,
                        "time": scan_time,
                        "symbol": symbol,
                        "name": sym_info.get("name", ""),
                        "signal_type": latest.signal.name,
                        "price": latest.price,
                        "score": latest.score,
                        "strategy": latest.strategy,
                        "reason": latest.reason,
                    }
                    signals_found.append(sig_data)

                    # 保存到数据库
                    conn = _get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO scheduler_signals
                        (date, time, symbol, signal_type, price, score, strategy, reason)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        today, scan_time, symbol, latest.signal.name,
                        latest.price, latest.score, latest.strategy, latest.reason,
                    ))
                    conn.commit()
                    conn.close()

                    # 如果模拟交易正在运行，且信号强度足够，执行交易
                    if trader and trader.status == "running" and latest.score >= MIN_TRADE_SCORE:
                        signal_type = latest.signal.name  # BUY or SELL
                        trade = trader.execute_signal(
                            symbol=symbol,
                            signal=signal_type,
                            price=latest.price,
                            name=sym_info.get("name", ""),
                            reason=f"{latest.strategy}: {latest.reason}",
                            score=latest.score,
                        )
                        if trade:
                            trades_executed.append({
                                "symbol": symbol,
                                "signal": signal_type,
                                "price": latest.price,
                                "shares": trade.shares,
                                "amount": trade.amount,
                            })
                            log.info("[盘中扫描] 自动%s %s @ %.2f x %d股",
                                     "买入" if signal_type == "BUY" else "卖出",
                                     symbol, latest.price, trade.shares)

            except Exception as e:
                log.debug("[盘中扫描] %s 扫描跳过: %s", symbol, e)
                continue

        result = {
            "date": today,
            "time": scan_time,
            "count": len(signals_found),
            "signals": signals_found,
            "scanned": len(watch_symbols),
            "trades_executed": len(trades_executed),
            "trades": trades_executed,
        }
        log.info("[盘中扫描] 完成，扫描 %d 只，发现 %d 个信号，执行 %d 笔交易",
                 len(watch_symbols), len(signals_found), len(trades_executed))
        return result

    def _task_closing_analysis(self) -> dict:
        """
        收盘分析任务
        生成当日交易报告，分析持仓盈亏
        """
        from scheduler.daily_report import DailyReporter

        today = datetime.now().strftime("%Y-%m-%d")
        log.info("[收盘分析] 开始执行，日期: %s", today)

        reporter = DailyReporter()
        report = reporter.generate_report()

        # 保存报告到数据库
        conn = _get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO daily_reports (date, report_html, report_json)
            VALUES (?, ?, ?)
        """, (
            today,
            report.get("html_path", ""),
            json.dumps(report.get("data", {}), ensure_ascii=False),
        ))
        conn.commit()
        conn.close()

        log.info("[收盘分析] 完成，报告已保存")
        return {"date": today, "report": report}

    def _task_data_update(self) -> dict:
        """
        数据更新任务
        更新股票列表和近期K线数据缓存
        """
        from data.provider import AShareDataProvider

        log.info("[数据更新] 开始执行")
        start = time.time()

        provider = AShareDataProvider()

        # 1. 更新股票列表（强制刷新缓存）
        AShareDataProvider._stock_list_cache = None
        AShareDataProvider._stock_list_cache_time = 0
        stock_list = provider.get_stock_list()
        stock_count = len(stock_list) if not stock_list.empty else 0
        log.info("[数据更新] 股票列表已更新: %d 只", stock_count)

        # 2. 更新热门股票和监控列表的K线缓存
        watch_symbols = self._get_watch_list()
        updated_count = 0
        error_count = 0

        # 计算日期范围（最近60个交易日）
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=120)).strftime("%Y%m%d")

        for sym_info in watch_symbols:
            symbol = sym_info["symbol"]
            try:
                df = provider.get_stock_daily(symbol, start_date, end_date)
                if not df.empty:
                    updated_count += 1
            except Exception as e:
                error_count += 1
                log.debug("[数据更新] %s 更新失败: %s", symbol, e)

        duration = time.time() - start
        result = {
            "stock_count": stock_count,
            "cache_updated": updated_count,
            "cache_errors": error_count,
            "duration_sec": round(duration, 1),
        }
        log.info("[数据更新] 完成: 股票列表 %d 只, K线更新 %d 只, 耗时 %.1f秒",
                 stock_count, updated_count, duration)
        return result

    # ==================== 辅助方法 ====================

    def _get_watch_list(self) -> List[dict]:
        """
        获取监控列表：今日选股结果 + 热门股票
        """
        watch = []
        today = datetime.now().strftime("%Y-%m-%d")

        # 从数据库读取今日选股
        try:
            conn = _get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT symbol, name FROM scheduler_picks WHERE date = ?",
                (today,)
            )
            rows = cursor.fetchall()
            conn.close()
            for row in rows:
                watch.append({"symbol": row["symbol"], "name": row["name"]})
        except Exception:
            pass

        # 补充热门股票（如果选股结果不足）
        if len(watch) < 10:
            hot_stocks = [
                {"symbol": "600519", "name": "贵州茅台"},
                {"symbol": "000858", "name": "五粮液"},
                {"symbol": "600036", "name": "招商银行"},
                {"symbol": "002594", "name": "比亚迪"},
                {"symbol": "300750", "name": "宁德时代"},
                {"symbol": "601318", "name": "中国平安"},
                {"symbol": "000001", "name": "平安银行"},
                {"symbol": "000333", "name": "美的集团"},
                {"symbol": "601888", "name": "中国中免"},
                {"symbol": "002415", "name": "海康威视"},
            ]
            existing = {w["symbol"] for w in watch}
            for s in hot_stocks:
                if s["symbol"] not in existing:
                    watch.append(s)

        return watch

    def _is_task_enabled(self, task_name: str) -> bool:
        """检查任务是否启用"""
        try:
            conn = _get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT enabled FROM task_config WHERE task_name = ?", (task_name,))
            row = cursor.fetchone()
            conn.close()
            if row is None:
                return True  # 默认启用
            return bool(row["enabled"])
        except Exception:
            return True

    def toggle_task(self, task_name: str) -> dict:
        """切换任务启用/禁用状态"""
        try:
            conn = _get_db_connection()
            cursor = conn.cursor()

            cursor.execute("SELECT enabled FROM task_config WHERE task_name = ?", (task_name,))
            row = cursor.fetchone()

            if row is None:
                # 首次设置，设为禁用
                cursor.execute(
                    "INSERT INTO task_config (task_name, enabled) VALUES (?, 0)",
                    (task_name,)
                )
                enabled = False
            else:
                new_state = 0 if row["enabled"] else 1
                cursor.execute(
                    "UPDATE task_config SET enabled = ?, last_modified = datetime('now', 'localtime') WHERE task_name = ?",
                    (new_state, task_name)
                )
                enabled = bool(new_state)

            conn.commit()
            conn.close()
            return {"task_name": task_name, "enabled": enabled}
        except Exception as e:
            log.error("切换任务状态失败: %s", e)
            return {"task_name": task_name, "error": str(e)}

    # ==================== 日志记录 ====================

    def _log_task_start(self, task_name: str, start_time: str) -> int:
        """记录任务开始"""
        try:
            conn = _get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO task_logs (task_name, status, start_time) VALUES (?, 'running', ?)",
                (task_name, start_time)
            )
            log_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return log_id
        except Exception as e:
            log.error("记录任务日志失败: %s", e)
            return -1

    def _log_task_end(self, log_id: int, status: str, end_time: str,
                      duration: float, summary: str, error: str = ""):
        """记录任务结束"""
        try:
            conn = _get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE task_logs
                SET status = ?, end_time = ?, duration_sec = ?,
                    result_summary = ?, error_msg = ?
                WHERE id = ?
            """, (status, end_time, duration, summary[:1000], error[:2000], log_id))
            conn.commit()
            conn.close()
        except Exception as e:
            log.error("更新任务日志失败: %s", e)

    # ==================== 查询接口 ====================

    def get_jobs_status(self) -> List[dict]:
        """获取所有任务状态"""
        jobs = []
        for job in self.scheduler.get_jobs():
            task_name = job.id
            enabled = self._is_task_enabled(task_name)
            next_run_time = getattr(job, 'next_run_time', None)
            next_run = str(next_run_time) if next_run_time else "无"

            # 获取最近一次执行记录
            last_run = self._get_last_run(task_name)

            jobs.append({
                "task_name": task_name,
                "display_name": self.TASK_NAMES.get(task_name, task_name),
                "enabled": enabled,
                "next_run": next_run,
                "trigger": str(job.trigger),
                "last_run": last_run,
            })
        return jobs

    def _get_last_run(self, task_name: str) -> dict:
        """获取任务最近一次执行记录"""
        try:
            conn = _get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT status, start_time, end_time, duration_sec, result_summary, error_msg
                FROM task_logs
                WHERE task_name = ?
                ORDER BY id DESC
                LIMIT 1
            """, (task_name,))
            row = cursor.fetchone()
            conn.close()
            if row:
                return {
                    "status": row["status"],
                    "start_time": row["start_time"],
                    "end_time": row["end_time"],
                    "duration_sec": row["duration_sec"],
                    "summary": row["result_summary"][:200] if row["result_summary"] else "",
                    "error": row["error_msg"][:200] if row["error_msg"] else "",
                }
        except Exception:
            pass
        return {}

    def get_latest_picks(self, date: str = None) -> List[dict]:
        """获取最新每日选股结果"""
        target_date = date or datetime.now().strftime("%Y-%m-%d")
        try:
            conn = _get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM scheduler_picks
                WHERE date = ?
                ORDER BY signal_strength DESC
            """, (target_date,))
            rows = [dict(row) for row in cursor.fetchall()]
            conn.close()
            # 解析JSON字段
            for row in rows:
                try:
                    row["reasons"] = json.loads(row.get("reasons", "[]"))
                except (json.JSONDecodeError, TypeError):
                    row["reasons"] = []
            return rows
        except Exception as e:
            log.error("查询选股结果失败: %s", e)
            return []

    def get_latest_report(self, date: str = None) -> dict:
        """获取最新每日报告"""
        target_date = date or datetime.now().strftime("%Y-%m-%d")
        try:
            conn = _get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM daily_reports WHERE date = ? ORDER BY id DESC LIMIT 1",
                (target_date,)
            )
            row = cursor.fetchone()
            conn.close()
            if row:
                result = dict(row)
                try:
                    result["report_json"] = json.loads(result.get("report_json", "{}"))
                except (json.JSONDecodeError, TypeError):
                    result["report_json"] = {}
                return result
        except Exception as e:
            log.error("查询日报失败: %s", e)
        return {}

    def get_task_logs(self, task_name: str = None, limit: int = 20) -> List[dict]:
        """获取任务执行日志"""
        try:
            conn = _get_db_connection()
            cursor = conn.cursor()
            if task_name:
                cursor.execute("""
                    SELECT * FROM task_logs
                    WHERE task_name = ?
                    ORDER BY id DESC LIMIT ?
                """, (task_name, limit))
            else:
                cursor.execute("""
                    SELECT * FROM task_logs
                    ORDER BY id DESC LIMIT ?
                """, (limit,))
            rows = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return rows
        except Exception as e:
            log.error("查询任务日志失败: %s", e)
            return []

    def run_task_now(self, task_name: str) -> dict:
        """手动触发执行某个任务"""
        task_map = {
            "daily_stock_pick": self._task_daily_stock_pick,
            "intraday_scan": self._task_intraday_scan,
            "closing_analysis": self._task_closing_analysis,
            "data_update": self._task_data_update,
        }

        if task_name not in task_map:
            return {"error": f"未知任务: {task_name}，可选: {list(task_map.keys())}"}

        log.info("手动触发任务: %s", task_name)
        self._run_task_wrapper(task_name, task_map[task_name])
        return {"task_name": task_name, "status": "triggered"}
