"""
东方财富数据提供者
通过HTTP接口获取A股行情数据，无需登录，稳定性最好
"""
import json
import logging
import urllib.request
import urllib.parse
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger("quant.data.eastmoney")


class EastMoneyProvider:
    """东方财富数据源"""

    BASE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    SPOT_URL = "https://push2.eastmoney.com/api/qt/clist/get"

    @staticmethod
    def _secid(symbol: str) -> str:
        """股票代码转东方财富secid格式"""
        if symbol.startswith("6"):
            return f"1.{symbol}"  # 上海
        else:
            return f"0.{symbol}"  # 深圳

    @staticmethod
    def get_stock_daily(symbol: str, start: str = None, end: str = None) -> pd.DataFrame:
        """
        获取日K线数据

        Args:
            symbol: 股票代码 (如 "600519")
            start: 开始日期 "YYYYMMDD"
            end: 结束日期 "YYYYMMDD"

        Returns:
            DataFrame with columns: date, open, high, low, close, volume, amount, pct_change, change, amplitude
        """
        if not start:
            start = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
        if not end:
            end = datetime.now().strftime("%Y%m%d")

        secid = EastMoneyProvider._secid(symbol)
        params = {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101",  # 日K
            "fqt": "1",    # 前复权
            "beg": start,
            "end": end,
            "lmt": "1000",
        }

        url = f"{EastMoneyProvider.BASE_URL}?{urllib.parse.urlencode(params)}"

        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())

            klines = data.get("data", {})
            if not klines:
                return pd.DataFrame()

            kline_list = klines.get("klines", [])
            if not kline_list:
                return pd.DataFrame()

            records = []
            for line in kline_list:
                parts = line.split(",")
                if len(parts) >= 11:
                    records.append({
                        "date": parts[0],
                        "open": float(parts[1]),
                        "close": float(parts[2]),
                        "high": float(parts[3]),
                        "low": float(parts[4]),
                        "volume": int(float(parts[5])),
                        "amount": float(parts[6]),
                        "amplitude": float(parts[7]),
                        "pct_change": float(parts[8]),
                        "change": float(parts[9]),
                        "turnover": float(parts[10]),
                    })

            df = pd.DataFrame(records)
            if not df.empty:
                df["date"] = pd.to_datetime(df["date"])
                df["symbol"] = symbol
            return df

        except Exception as e:
            log.warning("东方财富获取 %s 失败: %s", symbol, e)
            return pd.DataFrame()

    @staticmethod
    def get_stock_list() -> pd.DataFrame:
        """获取A股列表（实时行情快照）"""
        url = ("https://push2.eastmoney.com/api/qt/clist/get?"
               "pn=1&pz=6000&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281"
               "&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
               "&fields=f12,f14,f2,f3,f4,f5,f6,f7,f15,f16,f17,f18")

        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())

            diff = data.get("data", {}).get("diff", [])
            if not diff:
                return pd.DataFrame()

            records = []
            for item in diff:
                records.append({
                    "symbol": str(item.get("f12", "")),
                    "name": str(item.get("f14", "")),
                    "close": item.get("f2"),
                    "pct_change": item.get("f3"),
                    "change": item.get("f4"),
                    "volume": item.get("f5"),
                    "amount": item.get("f6"),
                    "amplitude": item.get("f7"),
                    "high": item.get("f15"),
                    "low": item.get("f16"),
                    "open": item.get("f17"),
                    "prev_close": item.get("f18"),
                })

            df = pd.DataFrame(records)
            # 过滤无效数据
            df = df[df["symbol"].str.len() == 6]
            df = df[df["close"].notna() & (df["close"] > 0)]
            return df

        except Exception as e:
            log.warning("东方财富获取股票列表失败: %s", e)
            return pd.DataFrame()

    @staticmethod
    def search_stock(keyword: str) -> pd.DataFrame:
        """搜索股票"""
        df = EastMoneyProvider.get_stock_list()
        if df.empty:
            return df
        mask = df["symbol"].str.contains(keyword) | df["name"].str.contains(keyword, na=False)
        return df[mask].head(20)
