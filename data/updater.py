"""
增量数据更新 - 只更新缺失的数据，不重复下载
启动时自动运行，后台静默更新
"""
import time
import logging
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd

log = logging.getLogger("quant.data.updater")


class DataUpdater:
    """
    增量数据更新器
    - 首次启动：下载全部股票列表 + 热门股K线
    - 后续启动：只更新增量数据
    - 后台运行，不阻塞API响应
    """

    def __init__(self, local_db, baostock_provider):
        self.db = local_db
        self.bs = baostock_provider
        self._running = False

    def initial_download(self):
        """首次启动：下载核心数据"""
        stats = self.db.stats()
        if stats['stocks'] > 0:
            log.info("本地已有 %d 只股票数据，跳过全量下载", stats['stocks'])
            return

        log.info("首次启动，开始下载核心数据...")
        start = time.time()

        # 1. 下载股票列表
        try:
            stock_list = self.bs.get_stock_list()
            if not stock_list.empty:
                self.db.save_stock_list(stock_list)
                log.info("✅ 股票列表: %d只", len(stock_list))
            else:
                log.error("❌ 股票列表为空，无法继续")
                return
        except Exception as e:
            log.error("❌ 股票列表下载失败: %s", e)
            return

        # 2. 下载热门股K线（前200只，覆盖主要指数成分股）
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
        hot_count = min(200, len(stock_list))
        success = 0

        for i, (_, row) in enumerate(stock_list.head(hot_count).iterrows()):
            symbol = row['symbol']
            try:
                df = self.bs.get_stock_daily(symbol, start_date=start_date, end_date=end_date)
                if not df.empty:
                    self.db.save_daily(symbol, df)
                    success += 1
            except Exception:
                pass
            time.sleep(0.02)  # 控制请求频率

        elapsed = time.time() - start
        stats = self.db.stats()
        log.info("✅ 首次下载完成: %d只股票, %d条K线, 耗时%.1f秒",
                stats['stocks'], stats['daily_records'], elapsed)

    def background_refresh(self):
        """后台刷新：增量更新已有数据"""
        if self._running:
            return
        self._running = True

        try:
            stats = self.db.stats()
            if stats['stocks'] == 0:
                return  # 无数据，先跑initial_download

            log.info("后台数据刷新开始...")

            # 更新股票列表
            try:
                stock_list = self.bs.get_stock_list()
                if not stock_list.empty:
                    self.db.save_stock_list(stock_list)
            except Exception as e:
                log.warning("刷新股票列表失败: %s", e)

            # 更新已有股票的最新数据（只更新最近5天）
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')

            # 只更新本地已有数据的股票
            local_stocks = self.db.get_stock_list()
            if local_stocks.empty:
                return

            updated = 0
            for _, row in local_stocks.iterrows():
                symbol = row['symbol']
                latest = self.db.get_latest_date(symbol)
                if latest and latest >= datetime.now().strftime('%Y-%m-%d'):
                    continue  # 已经是今天数据

                try:
                    df = self.bs.get_stock_daily(symbol, start_date=start_date, end_date=end_date)
                    if not df.empty:
                        self.db.save_daily(symbol, df)
                        updated += 1
                except Exception:
                    pass
                time.sleep(0.02)

            log.info("后台刷新完成: 更新了 %d 只股票", updated)
        finally:
            self._running = False
