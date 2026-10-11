"""
Baostock数据源 - A股行情数据获取
通过baostock库获取稳定的A股数据，替代不稳定的akshare
"""
import logging
import time
import pandas as pd
from typing import Optional, List

log = logging.getLogger("quant.data.baostock")


class BaostockProvider:
    """
    Baostock数据提供器
    
    baostock需要先login才能查询，本类自动管理登录状态：
    - 首次使用时自动login
    - 连接失败时自动重连
    - 提供logout方法用于程序退出时清理
    """

    def __init__(self):
        self._bs = None
        self._logged_in = False
        self._login_time = 0
        self._LOGIN_TTL = 3600  # 登录状态1小时后需要重新登录
        self._login_fail_time = 0
        self._LOGIN_FAIL_COOLDOWN = 30  # 登录失败后30秒冷却
        self._startup_check_done = False  # 启动检查是否完成
        self._startup_event = None  # threading.Event, 用于等待启动完成

    def _ensure_bs(self):
        """确保baostock模块已导入"""
        if self._bs is None:
            import baostock as bs
            self._bs = bs

    def startup_check(self):
        """
        启动时在后台线程中检查baostock连接
        如果连接失败，立即设置冷却期，避免首次请求阻塞
        """
        import threading
        self._startup_event = threading.Event()
        def _check():
            try:
                self._ensure_bs()
                result = self._bs.login()
                if result.error_code == '0':
                    self._logged_in = True
                    self._login_time = time.time()
                    log.info("baostock启动检查: 连接成功")
                else:
                    self._logged_in = False
                    self._login_fail_time = time.time()
                    log.warning("baostock启动检查: 连接失败，%d秒内使用本地缓存", self._LOGIN_FAIL_COOLDOWN)
            except Exception as e:
                self._logged_in = False
                self._login_fail_time = time.time()
                log.warning("baostock启动检查: 网络异常，%d秒内使用本地缓存: %s", self._LOGIN_FAIL_COOLDOWN, str(e)[:100])
            finally:
                self._startup_check_done = True
                self._startup_event.set()
        threading.Thread(target=_check, daemon=True).start()

    def _ensure_login(self):
        """确保已登录，如果未登录或登录已过期则自动登录"""
        self._ensure_bs()
        now = time.time()
        # 如果启动检查还在进行中，等待其完成（最多10秒）
        if not self._startup_check_done and self._startup_event is not None:
            self._startup_event.wait(timeout=10)
        # 如果之前登录失败且还在冷却期内，直接跳过
        if not self._logged_in and self._login_fail_time > 0 and (now - self._login_fail_time) < self._LOGIN_FAIL_COOLDOWN:
            return
        # 如果未登录或登录已过期，重新登录
        if not self._logged_in or (now - self._login_time) > self._LOGIN_TTL:
            if self._logged_in:
                try:
                    self._bs.logout()
                except Exception:
                    pass
            self.login()

    def login(self):
        """登录baostock"""
        self._ensure_bs()
        try:
            result = self._bs.login()
            if result.error_code == '0':
                self._logged_in = True
                self._login_time = time.time()
                log.info("baostock登录成功")
            else:
                log.error("baostock登录失败: %s", result.error_msg)
                self._logged_in = False
                self._login_fail_time = time.time()
        except Exception as e:
            log.error("baostock登录异常: %s", e)
            self._logged_in = False
            self._login_fail_time = time.time()

    def logout(self):
        """登出baostock"""
        if self._bs and self._logged_in:
            try:
                self._bs.logout()
                log.info("baostock已登出")
            except Exception as e:
                log.warning("baostock登出异常: %s", e)
            finally:
                self._logged_in = False

    def _convert_symbol(self, symbol: str) -> str:
        """
        转换股票代码格式
        600519 → sh.600519（上海主板）
        000001 → sz.000001（深圳）
        300xxx → sz.300xxx（创业板）
        688xxx → sh.688xxx（科创板）
        """
        # 如果已经是baostock格式（sh.600519），直接返回
        if '.' in symbol:
            return symbol
        
        # 根据代码前缀判断交易所
        if symbol.startswith(('6', '9')):
            return f"sh.{symbol}"
        elif symbol.startswith(('0', '2', '3')):
            return f"sz.{symbol}"
        else:
            # 默认尝试上海
            log.warning("无法判断股票 %s 的交易所，默认使用上海", symbol)
            return f"sh.{symbol}"

    def _convert_adjust(self, adjust: str) -> str:
        """
        转换复权类型
        qfq → '2'（前复权）
        hfq → '1'（后复权）
        '' 或其他 → '3'（不复权）
        """
        adjust_map = {
            'qfq': '2',  # 前复权
            'hfq': '1',  # 后复权
            '': '3',     # 不复权
        }
        return adjust_map.get(adjust, '3')

    def _format_date(self, date_str: str) -> str:
        """
        格式化日期为baostock要求的格式 YYYY-MM-DD
        支持输入: 20230101, 2023-01-01, 2023/01/01
        """
        if date_str is None:
            return None
        # 移除常见分隔符
        date_str = str(date_str).replace('-', '').replace('/', '')
        if len(date_str) == 8:
            return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        return date_str

    def _query_with_retry(self, query_func, max_retries: int = 2, base_delay: float = 0.5):
        """
        带重试机制的查询封装
        
        Args:
            query_func: 执行查询的函数（无参数）
            max_retries: 最大重试次数
            base_delay: 基础延迟秒数
            
        Returns:
            查询结果列表
        """
        # 等待启动检查完成
        if not self._startup_check_done and self._startup_event is not None:
            self._startup_event.wait(timeout=10)
        # 快速失败：如果登录失败且在冷却期内，直接返回空
        now = time.time()
        if not self._logged_in and self._login_fail_time > 0 and (now - self._login_fail_time) < self._LOGIN_FAIL_COOLDOWN:
            return []
        for attempt in range(max_retries):
            try:
                self._ensure_login()
                # 如果登录失败（冷却期内），直接返回空
                if not self._logged_in:
                    return []
                rs = query_func()
                
                if rs.error_code != '0':
                    log.warning("baostock查询错误(第%d次): %s", attempt + 1, rs.error_msg)
                    if attempt < max_retries - 1:
                        # 可能是登录过期，重新登录
                        self._logged_in = False
                        delay = base_delay * (2 ** attempt)
                        time.sleep(delay)
                        continue
                    return []

                data = []
                while rs.next():
                    data.append(rs.get_row_data())
                return data

            except Exception as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    log.warning("baostock查询异常(第%d次): %s，%0.1f秒后重试", 
                              attempt + 1, e, delay)
                    # 可能是连接断开，标记为未登录
                    self._logged_in = False
                    time.sleep(delay)
                else:
                    log.error("baostock查询最终失败: %s", e)
                    raise

        return []

    def get_stock_daily(self, symbol: str, start_date: str = None,
                        end_date: str = None, adjust: str = 'qfq') -> pd.DataFrame:
        """
        获取个股日线数据

        Args:
            symbol: 股票代码 如 "000001", "600519"
            start_date: 开始日期 "20230101" 或 "2023-01-01"
            end_date: 结束日期 "20251231" 或 "2025-12-31"
            adjust: 复权类型 qfq/hfq/""

        Returns:
            DataFrame: date, open, high, low, close, volume, amount, turnover, pct_change
        """
        bs_symbol = self._convert_symbol(symbol)
        adjust_flag = self._convert_adjust(adjust)
        start = self._format_date(start_date)
        end = self._format_date(end_date)

        log.info("baostock获取 %s 日线数据 (%s ~ %s, adjust=%s)", 
                bs_symbol, start, end, adjust)

        fields = "date,open,high,low,close,volume,amount,turn,pctChg"

        def do_query():
            return self._bs.query_history_k_data_plus(
                bs_symbol,
                fields,
                start_date=start or '',
                end_date=end or '',
                frequency='d',
                adjustflag=adjust_flag,
            )

        data = self._query_with_retry(do_query)

        if not data:
            log.warning("baostock获取 %s 数据为空", symbol)
            return pd.DataFrame()

        # 构建DataFrame
        df = pd.DataFrame(data, columns=['date', 'open', 'high', 'low', 'close',
                                          'volume', 'amount', 'turnover', 'pct_change'])

        # 转换数据类型（baostock返回的都是字符串）
        df['date'] = pd.to_datetime(df['date'])
        for col in ['open', 'high', 'low', 'close', 'volume', 'amount', 'turnover', 'pct_change']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        # 过滤无效数据行（有些行可能是空的）
        df = df.dropna(subset=['close'])
        df = df[df['close'] > 0]

        # 添加股票代码列
        df['symbol'] = symbol

        df = df.sort_values('date').reset_index(drop=True)
        log.info("baostock成功获取 %s 数据，共 %d 条", symbol, len(df))
        return df

    def get_stock_list(self) -> pd.DataFrame:
        """
        获取A股股票列表（只返回在市股票）

        Returns:
            DataFrame: symbol, name
        """
        log.info("baostock获取A股股票列表")

        # 获取最新的交易日（使用最近的日期）
        from datetime import datetime, timedelta
        today = datetime.now().strftime('%Y-%m-%d')
        # 往前找一个工作日，确保有数据
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')

        def do_query():
            return self._bs.query_stock_basic()

        data = self._query_with_retry(do_query)

        if not data:
            log.warning("baostock获取股票列表为空")
            return pd.DataFrame()

        # 构建DataFrame
        df = pd.DataFrame(data, columns=['code', 'code_name', 'ipoDate', 'outDate', 
                                          'type', 'status'])

        # 只保留股票（type=1）且在市（status=1）
        df = df[(df['type'] == '1') & (df['status'] == '1')]

        # 转换代码格式：sh.600519 → 600519
        df['symbol'] = df['code'].apply(lambda x: x.split('.')[1] if '.' in str(x) else x)
        df['name'] = df['code_name']

        result = df[['symbol', 'name']].reset_index(drop=True)
        log.info("baostock获取股票列表成功，共 %d 只", len(result))
        return result

    def get_index_daily(self, symbol: str, start_date: str = None,
                        end_date: str = None) -> pd.DataFrame:
        """
        获取指数日线数据

        Args:
            symbol: 指数代码，如 "sh.000001"(上证指数), "sh.000300"(沪深300),
                    "sz.399001"(深证成指), "sz.399006"(创业板指)
                    也支持纯数字格式如 "000001", "000300"
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            DataFrame: date, open, high, low, close, volume, amount
        """
        # 如果是纯数字，根据代码判断交易所
        if '.' not in symbol:
            if symbol.startswith(('0', '39')):
                # 指数代码：0开头通常是上证指数，39开头是深证指数
                if symbol.startswith('39'):
                    bs_symbol = f"sz.{symbol}"
                else:
                    bs_symbol = f"sh.{symbol}"
            else:
                bs_symbol = f"sh.{symbol}"
        else:
            bs_symbol = symbol

        start = self._format_date(start_date)
        end = self._format_date(end_date)

        log.info("baostock获取指数 %s 日线数据 (%s ~ %s)", bs_symbol, start, end)

        fields = "date,open,high,low,close,volume,amount"

        def do_query():
            return self._bs.query_history_k_data_plus(
                bs_symbol,
                fields,
                start_date=start or '',
                end_date=end or '',
                frequency='d',
                adjustflag='3',  # 指数不需要复权
            )

        data = self._query_with_retry(do_query)

        if not data:
            log.warning("baostock获取指数 %s 数据为空", symbol)
            return pd.DataFrame()

        # 构建DataFrame
        df = pd.DataFrame(data, columns=['date', 'open', 'high', 'low', 'close',
                                          'volume', 'amount'])

        # 转换数据类型
        df['date'] = pd.to_datetime(df['date'])
        for col in ['open', 'high', 'low', 'close', 'volume', 'amount']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        # 过滤无效数据
        df = df.dropna(subset=['close'])
        df = df[df['close'] > 0]

        df = df.sort_values('date').reset_index(drop=True)
        log.info("baostock成功获取指数 %s 数据，共 %d 条", symbol, len(df))
        return df

    def get_stock_minute(self, symbol: str, period: str = '5',
                        start_date: str = None, end_date: str = None,
                        days: int = 30) -> pd.DataFrame:
        """
        获取个股分钟线数据

        Args:
            symbol: 股票代码 如 "000001", "600519"
            period: 分钟级别 '5'/'15'/'30'/'60'
            start_date: 开始日期（可选，格式YYYY-MM-DD或YYYYMMDD）
            end_date: 结束日期（可选）
            days: 未指定start_date时，获取最近N天数据（默认30天）

        Returns:
            DataFrame: date, time, open, high, low, close, volume, amount
        """
        from pathlib import Path
        from datetime import datetime, timedelta
        import os

        # 验证period参数
        valid_periods = ['5', '15', '30', '60']
        if period not in valid_periods:
            log.warning("无效的分钟级别 %s，使用默认5分钟", period)
            period = '5'

        # 检查缓存
        cache_dir = Path(os.path.dirname(__file__)) / 'cache'
        cache_dir.mkdir(exist_ok=True)
        cache_file = cache_dir / f"{symbol}_{period}min.csv"

        # 缓存有效期：当天内有效（分钟线数据当天更新）
        if cache_file.exists():
            cache_mtime = datetime.fromtimestamp(cache_file.stat().st_mtime)
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            if cache_mtime >= today:
                try:
                    cached_df = pd.read_csv(cache_file)
                    if not cached_df.empty:
                        log.info("从缓存读取 %s %s分钟线数据，共 %d 条",
                                symbol, period, len(cached_df))
                        return cached_df
                except Exception as e:
                    log.warning("读取缓存失败: %s", e)

        # 计算日期范围
        if not end_date:
            end_date = datetime.now().strftime('%Y-%m-%d')
        else:
            end_date = self._format_date(end_date)

        if not start_date:
            start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        else:
            start_date = self._format_date(start_date)

        bs_symbol = self._convert_symbol(symbol)
        log.info("baostock获取 %s %s分钟线数据 (%s ~ %s)",
                bs_symbol, period, start_date, end_date)

        # baostock分钟线字段
        fields = "date,time,open,high,low,close,volume,amount"

        def do_query():
            return self._bs.query_history_k_data_plus(
                bs_symbol,
                fields,
                start_date=start_date,
                end_date=end_date,
                frequency=period,  # '5'/'15'/'30'/'60'
                adjustflag='3',    # 分钟线不复权
            )

        data = self._query_with_retry(do_query)

        if not data:
            log.warning("baostock获取 %s %s分钟线数据为空", symbol, period)
            return pd.DataFrame()

        # 构建DataFrame
        df = pd.DataFrame(data, columns=['date', 'time', 'open', 'high', 'low',
                                          'close', 'volume', 'amount'])

        # 转换数据类型
        for col in ['open', 'high', 'low', 'close', 'volume', 'amount']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        # 过滤无效数据
        df = df.dropna(subset=['close'])
        df = df[df['close'] > 0]

        df = df.sort_values(['date', 'time']).reset_index(drop=True)

        # 保存缓存
        try:
            df.to_csv(cache_file, index=False)
            log.info("分钟线数据已缓存到 %s", cache_file)
        except Exception as e:
            log.warning("缓存分钟线数据失败: %s", e)

        log.info("baostock成功获取 %s %s分钟线数据，共 %d 条",
                symbol, period, len(df))
        return df

    def __del__(self):
        """析构时登出"""
        try:
            self.logout()
        except Exception:
            pass
