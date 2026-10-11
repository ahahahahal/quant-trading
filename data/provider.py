"""
数据模块 - A股行情数据获取与管理
参考 qlib 的数据层设计
支持: baostock (优先) / akshare / curl备用通道
"""
import time
import logging
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List
import hashlib
import json

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import CONFIG
from data.alpha158 import Alpha158
from data.curl_provider import CurlDataProvider
from data.baostock_provider import BaostockProvider

log = logging.getLogger("quant.data")


def retry_with_backoff(func, max_retries: int = 3, base_delay: float = 0.5):
    """指数退避重试装饰器"""
    def wrapper(*args, **kwargs):
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                delay = base_delay * (2 ** attempt)
                log.warning("第%d次重试失败: %s, %0.1f秒后重试", attempt + 1, e, delay)
                time.sleep(delay)
    return wrapper


class DataCache:
    """本地CSV缓存，避免重复请求"""

    def __init__(self, cache_dir: str = None):
        self.cache_dir = Path(cache_dir or CONFIG.data.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _key(self, symbol: str, freq: str, adjust: str) -> Path:
        fname = f"{symbol}_{freq}_{adjust}.csv"
        return self.cache_dir / fname

    def get(self, symbol: str, freq: str, adjust: str) -> Optional[pd.DataFrame]:
        """获取缓存数据，如果未过期(1天内)则返回，否则返回None"""
        path = self._key(symbol, freq, adjust)
        if path.exists():
            df = pd.read_csv(path, parse_dates=["date"] if "date" in pd.read_csv(path, nrows=0).columns else [0])
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
            if (datetime.now() - mtime).days < 1:
                return df
        return None

    def get_stale(self, symbol: str, freq: str, adjust: str) -> Optional[pd.DataFrame]:
        """获取缓存数据(不管是否过期)，用于网络失败时的回退"""
        path = self._key(symbol, freq, adjust)
        if path.exists():
            df = pd.read_csv(path, parse_dates=["date"] if "date" in pd.read_csv(path, nrows=0).columns else [0])
            if not df.empty:
                log.info("使用过期缓存数据: %s (%d条)", path.name, len(df))
                return df
        return None

    def put(self, symbol: str, freq: str, adjust: str, df: pd.DataFrame):
        path = self._key(symbol, freq, adjust)
        df.to_csv(path, index=False)


class AShareDataProvider:
    """
    A股数据源 - 多源融合
    优先使用东方财富(最稳定)，失败时回退到baostock、akshare、curl
    """

    # 类级别股票列表缓存(所有实例共享, 1小时有效)
    _stock_list_cache: Optional[pd.DataFrame] = None
    _stock_list_cache_time: float = 0
    _STOCK_LIST_TTL: int = 3600  # 1小时

    def __init__(self, source: str = None, cache: bool = None):
        self.source = source or CONFIG.data.source
        self.cache_enabled = cache if cache is not None else CONFIG.data.cache_enabled
        self.cache = DataCache() if self.cache_enabled else None
        self._ak = None
        self._em_provider = None  # 东方财富数据源
        self._bs_provider = BaostockProvider()  # baostock备用
        self._bs_provider.startup_check()
        self._curl_provider = CurlDataProvider()  # curl备用

    @property
    def ak(self):
        if self._ak is None:
            import akshare as ak
            self._ak = ak
        return self._ak

    @property
    def em(self):
        if self._em_provider is None:
            from data.eastmoney_provider import EastMoneyProvider
            self._em_provider = EastMoneyProvider
        return self._em_provider

    def get_stock_daily(self, symbol: str, start_date: str = None,
                        end_date: str = None, adjust: str = None) -> pd.DataFrame:
        """
        获取个股日线数据

        Args:
            symbol: 股票代码 如 "000001", "600519"
            start_date: 开始日期 "20230101"
            end_date: 结束日期 "20251231"
            adjust: 复权类型 qfq/hfq/""

        Returns:
            DataFrame: date, open, high, low, close, volume, amount, turnover
        """
        adjust = adjust or CONFIG.data.adjust
        start_date = start_date or CONFIG.backtest.start_date
        end_date = end_date or CONFIG.backtest.end_date

        # 尝试缓存
        if self.cache:
            cached = self.cache.get(symbol, "daily", adjust)
            if cached is not None:
                mask = (cached["date"] >= pd.Timestamp(start_date)) & \
                       (cached["date"] <= pd.Timestamp(end_date))
                result = cached.loc[mask].copy()
                if len(result) > 0:
                    return result.reset_index(drop=True)

        # 数据源优先级：东方财富 → baostock → akshare → curl
        df = None

        # 1. 优先尝试东方财富（最稳定）
        try:
            df = self.em.get_stock_daily(symbol, start_date, end_date)
            if not df.empty:
                log.info("东方财富成功获取 %s 数据 (%d条)", symbol, len(df))
            else:
                log.warning("东方财富返回空数据: %s", symbol)
        except Exception as e_em:
            log.warning("东方财富获取 %s 数据失败: %s", symbol, e_em)

        # 2. 东方财富失败，尝试baostock
        if df is None or df.empty:
            try:
                df = self._bs_provider.get_stock_daily(symbol, start_date, end_date, adjust)
                if not df.empty:
                    log.info("baostock成功获取 %s 数据", symbol)
            except Exception as e_bs:
                log.warning("baostock获取 %s 数据失败: %s", symbol, e_bs)

        # 3. baostock也失败，尝试akshare
        if df is None or df.empty:
            if not self._bs_provider._logged_in and self.cache:
                stale = self.cache.get_stale(symbol, "daily", adjust)
                if stale is not None:
                    mask = (stale["date"] >= pd.Timestamp(start_date)) & \
                           (stale["date"] <= pd.Timestamp(end_date))
                    result = stale.loc[mask].sort_values("date").reset_index(drop=True)
                    if not result.empty:
                        log.info("使用过期缓存数据: %s (%d条)", symbol, len(result))
                        return result
                return pd.DataFrame()
            try:
                @retry_with_backoff
                def _fetch():
                    return self.ak.stock_zh_a_hist(
                        symbol=symbol,
                        period="daily",
                        start_date=start_date,
                        end_date=end_date,
                        adjust=adjust,
                    )
                df = _fetch()
            except Exception as e:
                log.warning("akshare获取 %s 数据失败: %s，尝试curl备用通道...", symbol, e)
                try:
                    df = self._curl_provider.get_stock_daily(symbol, start_date, end_date, adjust)
                    if df.empty:
                        log.error("curl备用通道获取 %s 数据也失败", symbol)
                        # 回退到过期缓存
                        if self.cache:
                            stale = self.cache.get_stale(symbol, "daily", adjust)
                            if stale is not None:
                                mask = (stale["date"] >= pd.Timestamp(start_date)) & \
                                       (stale["date"] <= pd.Timestamp(end_date))
                                return stale.loc[mask].sort_values("date").reset_index(drop=True)
                        return pd.DataFrame()
                    log.info("curl备用通道成功获取 %s 数据", symbol)
                except Exception as e2:
                    log.error("curl备用通道获取 %s 数据失败: %s", symbol, e2)
                    # 回退到过期缓存
                    if self.cache:
                        stale = self.cache.get_stale(symbol, "daily", adjust)
                        if stale is not None:
                            mask = (stale["date"] >= pd.Timestamp(start_date)) & \
                                   (stale["date"] <= pd.Timestamp(end_date))
                            return stale.loc[mask].sort_values("date").reset_index(drop=True)
                    return pd.DataFrame()

        # 如果是baostock返回的数据（已经是标准列名），直接使用
        if 'pct_change' in df.columns and 'symbol' in df.columns:
            # baostock返回的数据已经是标准格式，只需缓存
            if self.cache:
                self.cache.put(symbol, "daily", adjust, df)
            return df

        if df.empty:
            return df

        # akshare返回的数据需要标准化列名（中文列名）
        col_map = {
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
            "成交额": "amount", "换手率": "turnover",
            "涨跌幅": "pct_change", "涨跌额": "change",
            "振幅": "amplitude",
        }
        df = df.rename(columns=col_map)

        # 确保日期格式
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)

        # 添加股票代码列
        df["symbol"] = symbol

        # 缓存
        if self.cache:
            self.cache.put(symbol, "daily", adjust, df)

        return df

    def get_index_daily(self, symbol: str = "000300",
                        start_date: str = None,
                        end_date: str = None) -> pd.DataFrame:
        """获取指数日线 (沪深300=000300, 上证50=000016, 中证500=000905)"""
        start_date = start_date or CONFIG.backtest.start_date
        end_date = end_date or CONFIG.backtest.end_date

        # 数据源优先级：baostock → akshare → curl
        try:
            df = self._bs_provider.get_index_daily(symbol, start_date, end_date)
            if df.empty:
                raise ValueError("baostock返回空数据")
            log.info("baostock成功获取指数 %s 数据", symbol)
            return df
        except Exception as e_bs:
            log.warning("baostock获取指数 %s 失败: %s，尝试akshare...", symbol, e_bs)
            try:
                df = self.ak.stock_zh_index_daily(symbol=f"sh{symbol}")
            except Exception:
                try:
                    df = self.ak.stock_zh_index_daily(symbol=f"sz{symbol}")
                except Exception as e:
                    log.warning("akshare获取指数 %s 失败: %s，尝试curl备用通道...", symbol, e)
                    try:
                        df = self._curl_provider.get_index_daily(symbol, start_date, end_date)
                        if df.empty:
                            log.error("curl备用通道获取指数 %s 也失败", symbol)
                            return pd.DataFrame()
                        log.info("curl备用通道成功获取指数 %s 数据", symbol)
                        # curl返回的已经是标准列名，直接过滤日期返回
                        mask = (df["date"] >= pd.Timestamp(start_date)) & \
                               (df["date"] <= pd.Timestamp(end_date))
                        return df.loc[mask].sort_values("date").reset_index(drop=True)
                    except Exception as e2:
                        log.error("curl备用通道获取指数 %s 失败: %s", symbol, e2)
                        return pd.DataFrame()

        df = df.rename(columns={"date": "date", "open": "open", "high": "high",
                                "low": "low", "close": "close", "volume": "volume"})
        df["date"] = pd.to_datetime(df["date"])
        mask = (df["date"] >= pd.Timestamp(start_date)) & \
               (df["date"] <= pd.Timestamp(end_date))
        return df.loc[mask].sort_values("date").reset_index(drop=True)

    def get_stock_list(self) -> pd.DataFrame:
        """获取全部A股列表(带1小时内存缓存)"""
        now = time.time()
        if (AShareDataProvider._stock_list_cache is not None
                and now - AShareDataProvider._stock_list_cache_time < self._STOCK_LIST_TTL):
            return AShareDataProvider._stock_list_cache

        # 数据源优先级：东方财富 → baostock → akshare → curl

        # 1. 优先尝试东方财富
        try:
            df = self.em.get_stock_list()
            if not df.empty:
                log.info("东方财富成功获取股票列表 (%d只)", len(df))
                AShareDataProvider._stock_list_cache = df
                AShareDataProvider._stock_list_cache_time = now
                return df
        except Exception as e_em:
            log.warning("东方财富获取股票列表失败: %s", e_em)

        # 2. 东方财富失败，尝试baostock
        try:
            df = self._bs_provider.get_stock_list()
            if df.empty:
                raise ValueError("baostock返回空数据")
            log.info("baostock成功获取股票列表")
            AShareDataProvider._stock_list_cache = df
            AShareDataProvider._stock_list_cache_time = now
            return df
        except Exception as e_bs:
            log.warning("baostock获取股票列表失败: %s，尝试akshare...", e_bs)
            try:
                df = self.ak.stock_zh_a_spot_em()
            except Exception as e:
                log.warning("akshare获取股票列表失败: %s，尝试curl备用通道...", e)
                try:
                    df = self._curl_provider.get_stock_list()
                    if df.empty:
                        log.error("curl备用通道获取股票列表也失败，尝试过期缓存...")
                        # 回退到过期缓存
                        if self.cache:
                            stale = self.cache.get_stale("_stock_list", "", "")
                            if stale is not None:
                                AShareDataProvider._stock_list_cache = stale
                                AShareDataProvider._stock_list_cache_time = now
                                return stale
                        return pd.DataFrame()
                    log.info("curl备用通道成功获取股票列表")
                    AShareDataProvider._stock_list_cache = df
                    AShareDataProvider._stock_list_cache_time = now
                    return df
                except Exception as e2:
                    log.error("curl备用通道获取股票列表失败: %s，尝试过期缓存...", e2)
                    # 回退到过期缓存
                    if self.cache:
                        stale = self.cache.get_stale("_stock_list", "", "")
                        if stale is not None:
                            AShareDataProvider._stock_list_cache = stale
                            AShareDataProvider._stock_list_cache_time = now
                            return stale
                    return pd.DataFrame()

        # akshare返回的数据，标准化列名
        col_map = {
            "代码": "symbol", "名称": "name", "最新价": "price",
            "涨跌幅": "pct_change", "成交量": "volume",
            "成交额": "amount", "总市值": "market_cap",
            "流通市值": "float_cap", "市盈率-动态": "pe_ttm",
            "市净率": "pb",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        AShareDataProvider._stock_list_cache = df
        AShareDataProvider._stock_list_cache_time = now
        return df

    def get_sector_stocks(self, sector: str = "白酒") -> List[str]:
        """获取板块成分股"""
        try:
            df = self.ak.stock_board_concept_name_em()
            row = df[df["板块名称"].str.contains(sector)]
            if row.empty:
                return []
            board_name = row.iloc[0]["板块名称"]
            detail = self.ak.stock_board_concept_cons_em(symbol=board_name)
            return detail["代码"].tolist()
        except Exception as e:
            log.error("获取板块 %s 失败: %s", sector, e)
            return []

    def batch_download(self, symbols: List[str], start_date: str = None,
                       end_date: str = None) -> dict:
        """批量下载多只股票数据"""
        result = {}
        for i, sym in enumerate(symbols):
            log.info("下载 %s (%d/%d)", sym, i + 1, len(symbols))
            df = self.get_stock_daily(sym, start_date, end_date)
            if not df.empty:
                result[sym] = df
        return result


class FactorCalculator:
    """
    技术因子计算引擎
    参考 qlib Alpha158 因子库
    """

    @staticmethod
    def add_all_factors(df: pd.DataFrame) -> pd.DataFrame:
        """添加全部技术因子"""
        df = df.copy()
        df = FactorCalculator._add_ma_factors(df)
        df = FactorCalculator._add_momentum_factors(df)
        df = FactorCalculator._add_volatility_factors(df)
        df = FactorCalculator._add_volume_factors(df)
        df = FactorCalculator._add_pattern_factors(df)
        return df

    @staticmethod
    def _add_ma_factors(df: pd.DataFrame) -> pd.DataFrame:
        """均线因子"""
        for w in [5, 10, 20, 60, 120]:
            df[f"ma{w}"] = df["close"].rolling(w).mean()
            df[f"ema{w}"] = df["close"].ewm(span=w, adjust=False).mean()
        # 均线斜率
        for w in [5, 10, 20]:
            df[f"ma{w}_slope"] = df[f"ma{w}"].pct_change(5)
        # 均线多头排列信号
        df["ma_bull"] = ((df["ma5"] > df["ma10"]) &
                         (df["ma10"] > df["ma20"]) &
                         (df["ma20"] > df["ma60"])).astype(int)
        return df

    @staticmethod
    def _add_momentum_factors(df: pd.DataFrame) -> pd.DataFrame:
        """动量因子"""
        for d in [1, 3, 5, 10, 20, 60]:
            df[f"ret_{d}d"] = df["close"].pct_change(d)
        # RSI
        for w in [6, 14]:
            delta = df["close"].diff()
            gain = delta.where(delta > 0, 0).rolling(w).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(w).mean()
            rs = gain / loss.replace(0, np.nan)
            df[f"rsi{w}"] = 100 - 100 / (1 + rs)
        # MACD
        ema12 = df["close"].ewm(span=12, adjust=False).mean()
        ema26 = df["close"].ewm(span=26, adjust=False).mean()
        df["macd_dif"] = ema12 - ema26
        df["macd_dea"] = df["macd_dif"].ewm(span=9, adjust=False).mean()
        df["macd_hist"] = 2 * (df["macd_dif"] - df["macd_dea"])
        # KDJ
        low_9 = df["low"].rolling(9).min()
        high_9 = df["high"].rolling(9).max()
        rsv = (df["close"] - low_9) / (high_9 - low_9).replace(0, np.nan) * 100
        df["kdj_k"] = rsv.ewm(com=2, adjust=False).mean()
        df["kdj_d"] = df["kdj_k"].ewm(com=2, adjust=False).mean()
        df["kdj_j"] = 3 * df["kdj_k"] - 2 * df["kdj_d"]
        return df

    @staticmethod
    def _add_volatility_factors(df: pd.DataFrame) -> pd.DataFrame:
        """波动率因子"""
        for w in [5, 10, 20]:
            df[f"vol_{w}d"] = df["ret_1d"].rolling(w).std() * np.sqrt(252)
        # ATR
        for w in [14, 20]:
            tr = pd.concat([
                df["high"] - df["low"],
                (df["high"] - df["close"].shift(1)).abs(),
                (df["low"] - df["close"].shift(1)).abs(),
            ], axis=1).max(axis=1)
            df[f"atr{w}"] = tr.rolling(w).mean()
        # 布林带
        df["boll_mid"] = df["close"].rolling(20).mean()
        boll_std = df["close"].rolling(20).std()
        df["boll_upper"] = df["boll_mid"] + 2 * boll_std
        df["boll_lower"] = df["boll_mid"] - 2 * boll_std
        df["boll_width"] = (df["boll_upper"] - df["boll_lower"]) / df["boll_mid"]
        return df

    @staticmethod
    def _add_volume_factors(df: pd.DataFrame) -> pd.DataFrame:
        """量能因子"""
        df["vol_ma5"] = df["volume"].rolling(5).mean()
        df["vol_ma20"] = df["volume"].rolling(20).mean()
        df["vol_ratio"] = df["volume"] / df["vol_ma5"].replace(0, np.nan)
        df["vol_trend"] = df["vol_ma5"] / df["vol_ma20"].replace(0, np.nan)
        # 量价关系
        df["price_vol_corr"] = df["close"].rolling(20).corr(df["volume"])
        # OBV
        obv = (np.sign(df["close"].diff()) * df["volume"]).fillna(0).cumsum()
        df["obv"] = obv
        df["obv_ma"] = obv.rolling(20).mean()
        return df

    @staticmethod
    def _add_pattern_factors(df: pd.DataFrame) -> pd.DataFrame:
        """形态因子"""
        # K线实体和影线
        df["body"] = (df["close"] - df["open"]).abs()
        df["upper_shadow"] = df["high"] - df[["open", "close"]].max(axis=1)
        df["lower_shadow"] = df[["open", "close"]].min(axis=1) - df["low"]
        # 涨停/跌停
        df["is_limit_up"] = (df.get("pct_change", df["close"].pct_change() * 100) >= 9.9).astype(int)
        df["is_limit_down"] = (df.get("pct_change", df["close"].pct_change() * 100) <= -9.9).astype(int)
        # N日新高新低
        for w in [20, 60]:
            df[f"high_{w}d"] = df["high"].rolling(w).max()
            df[f"low_{w}d"] = df["low"].rolling(w).min()
            df[f"near_high_{w}d"] = (df["close"] - df[f"high_{w}d"]) / df[f"high_{w}d"]
            df[f"near_low_{w}d"] = (df["close"] - df[f"low_{w}d"]) / df[f"low_{w}d"]
        return df


# 便捷函数
def get_data(symbol: str, start: str = None, end: str = None,
             with_factors: bool = True) -> pd.DataFrame:
    """一步获取数据+因子 (使用Alpha158因子库)"""
    provider = AShareDataProvider()
    df = provider.get_stock_daily(symbol, start, end)
    if with_factors and not df.empty:
        df = Alpha158.compute_all(df)
    return df
