"""
数据预下载脚本 - 一次性下载所有股票数据到本地SQLite
运行一次即可，之后API全部走本地数据库（零延迟）
"""
import sys
import time
import logging
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("quant.preload")

from data.local_db import get_local_db
from data.baostock_provider import BaostockProvider


def preload_all():
    """预下载所有数据到本地SQLite"""
    db = get_local_db()
    bs = BaostockProvider()

    stats_before = db.stats()
    log.info("当前数据库状态: %s", stats_before)

    # 1. 下载股票列表
    log.info("=" * 50)
    log.info("[1/3] 下载股票列表...")
    start = time.time()
    try:
        stock_list = bs.get_stock_list()
        if not stock_list.empty:
            db.save_stock_list(stock_list)
            log.info("✅ 股票列表: %d只 (%.1f秒)", len(stock_list), time.time() - start)
        else:
            log.warning("⚠️ 股票列表为空")
            return
    except Exception as e:
        log.error("❌ 股票列表下载失败: %s", e)
        return

    # 2. 下载所有股票的日K线（最近1年）
    log.info("=" * 50)
    log.info("[2/3] 下载日K线数据（最近1年）...")
    from datetime import datetime, timedelta
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')

    total = len(stock_list)
    success = 0
    failed = 0
    start = time.time()

    for i, (_, row) in enumerate(stock_list.iterrows()):
        symbol = row['symbol']
        try:
            # 检查是否已有最新数据
            latest = db.get_latest_date(symbol)
            if latest and latest >= datetime.now().strftime('%Y-%m-%d'):
                success += 1
                continue  # 已有今天数据，跳过

            df = bs.get_stock_daily(symbol, start_date=start_date, end_date=end_date)
            if not df.empty:
                db.save_daily(symbol, df)
                success += 1
            else:
                failed += 1

            if (i + 1) % 100 == 0:
                elapsed = time.time() - start
                rate = (i + 1) / elapsed
                eta = (total - i - 1) / rate if rate > 0 else 0
                log.info("  进度: %d/%d (%.0f%%), 成功: %d, 失败: %d, 速率: %.1f只/秒, ETA: %.0f秒",
                        i + 1, total, (i + 1) / total * 100, success, failed, rate, eta)

            # baostock请求间隔，避免被限速
            time.sleep(0.05)

        except Exception as e:
            failed += 1
            if failed <= 5:
                log.warning("  %s 下载失败: %s", symbol, e)

    elapsed = time.time() - start
    log.info("✅ 日K线下载完成: 成功 %d, 失败 %d, 耗时 %.1f秒", success, failed, elapsed)

    # 3. 下载热门股的分钟线（前100只）
    log.info("=" * 50)
    log.info("[3/3] 下载热门股分钟线（前100只）...")
    hot_stocks = stock_list.head(100)
    start = time.time()
    m_success = 0

    for i, (_, row) in enumerate(hot_stocks.iterrows()):
        symbol = row['symbol']
        try:
            df = bs.get_stock_minute(symbol, period='5', days=5)
            if not df.empty:
                db.save_minute(symbol, '5', df)
                m_success += 1
            time.sleep(0.05)
        except Exception as e:
            pass  # 分钟线失败不影响主流程

    log.info("✅ 分钟线下载完成: %d/%d只 (%.1f秒)", m_success, len(hot_stocks), time.time() - start)

    # 最终统计
    stats = db.stats()
    log.info("=" * 50)
    log.info("🎉 预下载完成!")
    log.info("  股票数量: %d", stats['stocks'])
    log.info("  日K线记录: %d", stats['daily_records'])
    log.info("  分钟线记录: %d", stats['minute_records'])
    log.info("  最新日期: %s", stats['latest_date'])
    log.info("  数据库大小: %.2f MB", stats['db_size_mb'])


if __name__ == "__main__":
    preload_all()
