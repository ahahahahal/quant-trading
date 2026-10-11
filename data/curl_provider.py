"""
Curl数据提供器 - 通过subprocess调用curl获取东方财富数据
解决Python requests/urllib无法连接东方财富的问题
作为akshare的备用数据通道
"""
import subprocess
import json
import time
import logging
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List

log = logging.getLogger("quant.data.curl")


class CurlDataProvider:
    """
    通过curl命令获取东方财富数据的备用数据提供器
    当akshare等Python库网络连接失败时使用
    """

    # 东方财富API地址
    STOCK_LIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
    KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

    # curl超时时间（秒）
    CURL_TIMEOUT = 15

    # 最大重试次数
    MAX_RETRIES = 3

    @staticmethod
    def _run_curl(url: str, timeout: int = None) -> Optional[str]:
        """
        执行curl命令获取数据

        Args:
            url: 请求地址
            timeout: 超时时间（秒）

        Returns:
            响应内容字符串，失败返回None
        """
        timeout = timeout or CurlDataProvider.CURL_TIMEOUT

        for attempt in range(CurlDataProvider.MAX_RETRIES):
            try:
                # 构建curl命令，包含常见浏览器头避免被拦截
                cmd = [
                    "curl", "-s", "-L",
                    "--max-time", str(timeout),
                    "--compressed",
                    "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "-H", "Accept: application/json, text/plain, */*",
                    "-H", "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8",
                    url
                ]

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout + 5,  # 给subprocess额外5秒超时
                    encoding="utf-8"
                )

                if result.returncode == 0 and result.stdout.strip():
                    # 验证返回的是有效JSON
                    content = result.stdout.strip()
                    try:
                        json.loads(content)
                        return content
                    except json.JSONDecodeError:
                        log.warning("curl返回非JSON数据 (第%d次): %s", attempt + 1, content[:100])
                else:
                    log.warning("curl请求失败 (第%d次): returncode=%d, stderr=%s",
                               attempt + 1, result.returncode, result.stderr[:200] if result.stderr else "")

            except subprocess.TimeoutExpired:
                log.warning("curl请求超时 (第%d次): %s", attempt + 1, url[:100])
            except Exception as e:
                log.warning("curl请求异常 (第%d次): %s", attempt + 1, str(e))

            # 重试前等待
            if attempt < CurlDataProvider.MAX_RETRIES - 1:
                time.sleep(1 * (attempt + 1))  # 递增等待时间

        return None

    def _load_seed_data(self) -> pd.DataFrame:
        """加载本地种子股票数据作为最终兜底"""
        seed_path = Path(__file__).parent / "stock_seeds.json"
        if not seed_path.exists():
            log.error("种子数据文件不存在: %s", seed_path)
            return pd.DataFrame()
        try:
            with open(seed_path, "r", encoding="utf-8") as f:
                records = json.load(f)
            df = pd.DataFrame(records)
            log.info("已加载 %d 条种子股票数据", len(df))
            return df
        except Exception as e:
            log.error("加载种子数据失败: %s", e)
            return pd.DataFrame()

    @staticmethod
    def _determine_market(symbol: str) -> int:
        """
        根据股票代码判断市场
        0=深圳, 1=上海

        Args:
            symbol: 股票代码

        Returns:
            市场代码 (0或1)
        """
        symbol = str(symbol).strip()

        # 上海市场：6开头
        if symbol.startswith("6"):
            return 1
        # 深圳市场：0、3开头
        elif symbol.startswith("0") or symbol.startswith("3"):
            return 0
        # 指数判断：000xxx上证指数，399xxx深圳指数
        elif symbol.startswith("000"):
            return 1
        elif symbol.startswith("399"):
            return 0
        # 默认深圳
        return 0

    def get_stock_list(self) -> pd.DataFrame:
        """
        获取A股股票列表

        Returns:
            DataFrame: symbol, name, price, pct_change, volume, amount
        """
        # 构建请求URL
        params = {
            "pn": "1",
            "pz": "5000",  # 每页数量，5000足够覆盖全部A股
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",  # A股过滤条件
            "fields": "f12,f14,f2,f3,f4,f5,f6,f7,f15,f16,f17,f18"
        }

        param_str = "&".join([f"{k}={v}" for k, v in params.items()])
        url = f"{self.STOCK_LIST_URL}?{param_str}"

        log.info("通过curl获取A股列表...")
        content = self._run_curl(url)

        if not content:
            log.warning("curl获取股票列表失败，尝试加载本地种子数据...")
            return self._load_seed_data()

        try:
            data = json.loads(content)

            # 检查返回数据结构
            if "data" not in data or data["data"] is None:
                log.error("股票列表API返回数据为空")
                return pd.DataFrame()

            diff = data["data"].get("diff", [])
            if not diff:
                log.error("股票列表数据为空")
                return pd.DataFrame()

            # 解析数据
            records = []
            for item in diff:
                records.append({
                    "symbol": str(item.get("f12", "")),
                    "name": str(item.get("f14", "")),
                    "price": item.get("f2"),  # 最新价
                    "pct_change": item.get("f3"),  # 涨跌幅
                    "change": item.get("f4"),  # 涨跌额
                    "volume": item.get("f5"),  # 成交量
                    "amount": item.get("f6"),  # 成交额
                    "amplitude": item.get("f7"),  # 振幅
                    "high": item.get("f15"),  # 最高
                    "low": item.get("f16"),  # 最低
                    "open": item.get("f17"),  # 今开
                    "prev_close": item.get("f18"),  # 昨收
                })

            df = pd.DataFrame(records)

            # 过滤无效数据
            df = df[df["symbol"].str.len() == 6]  # 股票代码应为6位
            df = df[df["price"].notna()]  # 过滤停牌股

            log.info("成功获取 %d 只股票列表", len(df))
            return df.reset_index(drop=True)

        except json.JSONDecodeError as e:
            log.error("JSON解析失败: %s", str(e)[:200])
            return pd.DataFrame()
        except Exception as e:
            log.error("解析股票列表数据失败: %s", str(e))
            return pd.DataFrame()

    def get_stock_daily(self, symbol: str, start_date: str = None,
                        end_date: str = None, adjust: str = "qfq") -> pd.DataFrame:
        """
        获取个股日K线数据

        Args:
            symbol: 股票代码，如 "000001", "600519"
            start_date: 开始日期，格式 "20230101"
            end_date: 结束日期，格式 "20251231"
            adjust: 复权类型 qfq(前复权)/hfq(后复权)/"" (空字符串为不复权)

        Returns:
            DataFrame: date, open, close, high, low, volume, amount, turnover, pct_change, change, amplitude
        """
        # 设置默认日期
        if not start_date:
            start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
        if not end_date:
            end_date = datetime.now().strftime("%Y%m%d")

        # 判断市场
        market = self._determine_market(symbol)

        # 复权类型映射
        fqt_map = {"qfq": "1", "hfq": "2", "": "0"}
        fqt = fqt_map.get(adjust, "1")

        # 构建请求URL
        params = {
            "secid": f"{market}.{symbol}",
            "fields1": "f1,f2,f3",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101",  # 日线
            "fqt": fqt,
            "beg": start_date,
            "end": end_date
        }

        param_str = "&".join([f"{k}={v}" for k, v in params.items()])
        url = f"{self.KLINE_URL}?{param_str}"

        log.info("通过curl获取 %s 日K线数据 (%s ~ %s)...", symbol, start_date, end_date)
        content = self._run_curl(url)

        if not content:
            log.error("curl获取 %s K线数据失败", symbol)
            return pd.DataFrame()

        try:
            data = json.loads(content)

            # 检查返回数据
            if "data" not in data or data["data"] is None:
                log.error("%s K线API返回数据为空", symbol)
                return pd.DataFrame()

            klines = data["data"].get("klines", [])
            if not klines:
                log.warning("%s K线数据为空", symbol)
                return pd.DataFrame()

            # 解析K线数据
            # 格式：日期,开盘,收盘,最高,最低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率
            records = []
            for line in klines:
                parts = line.split(",")
                if len(parts) >= 11:
                    records.append({
                        "date": parts[0],
                        "open": float(parts[1]),
                        "close": float(parts[2]),
                        "high": float(parts[3]),
                        "low": float(parts[4]),
                        "volume": float(parts[5]),
                        "amount": float(parts[6]),
                        "amplitude": float(parts[7]),
                        "pct_change": float(parts[8]),
                        "change": float(parts[9]),
                        "turnover": float(parts[10]),
                    })

            df = pd.DataFrame(records)

            # 转换日期格式
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)

            # 添加股票代码列
            df["symbol"] = symbol

            log.info("成功获取 %s 共 %d 条日K线数据", symbol, len(df))
            return df

        except json.JSONDecodeError as e:
            log.error("JSON解析失败: %s", str(e)[:200])
            return pd.DataFrame()
        except Exception as e:
            log.error("解析 %s K线数据失败: %s", symbol, str(e))
            return pd.DataFrame()

    def get_index_daily(self, symbol: str = "000300",
                        start_date: str = None,
                        end_date: str = None) -> pd.DataFrame:
        """
        获取指数日K线数据

        Args:
            symbol: 指数代码，如 "000300"(沪深300), "000016"(上证50), "000905"(中证500)
            start_date: 开始日期，格式 "20230101"
            end_date: 结束日期，格式 "20251231"

        Returns:
            DataFrame: date, open, close, high, low, volume, amount
        """
        # 设置默认日期
        if not start_date:
            start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
        if not end_date:
            end_date = datetime.now().strftime("%Y%m%d")

        # 判断市场
        # 指数：1A0xxx=上证(1), 399xxx=深圳(0)
        if symbol.startswith("000"):
            market = 1  # 上证指数
        elif symbol.startswith("399"):
            market = 0  # 深证指数
        else:
            market = self._determine_market(symbol)

        # 构建请求URL
        params = {
            "secid": f"{market}.{symbol}",
            "fields1": "f1,f2,f3",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101",  # 日线
            "fqt": "1",
            "beg": start_date,
            "end": end_date
        }

        param_str = "&".join([f"{k}={v}" for k, v in params.items()])
        url = f"{self.KLINE_URL}?{param_str}"

        log.info("通过curl获取指数 %s 日线数据 (%s ~ %s)...", symbol, start_date, end_date)
        content = self._run_curl(url)

        if not content:
            log.error("curl获取指数 %s 数据失败", symbol)
            return pd.DataFrame()

        try:
            data = json.loads(content)

            if "data" not in data or data["data"] is None:
                log.error("指数 %s API返回数据为空", symbol)
                return pd.DataFrame()

            klines = data["data"].get("klines", [])
            if not klines:
                log.warning("指数 %s K线数据为空", symbol)
                return pd.DataFrame()

            # 解析K线数据
            records = []
            for line in klines:
                parts = line.split(",")
                if len(parts) >= 7:
                    records.append({
                        "date": parts[0],
                        "open": float(parts[1]),
                        "close": float(parts[2]),
                        "high": float(parts[3]),
                        "low": float(parts[4]),
                        "volume": float(parts[5]),
                        "amount": float(parts[6]),
                    })

            df = pd.DataFrame(records)
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)

            log.info("成功获取指数 %s 共 %d 条数据", symbol, len(df))
            return df

        except json.JSONDecodeError as e:
            log.error("JSON解析失败: %s", str(e)[:200])
            return pd.DataFrame()
        except Exception as e:
            log.error("解析指数 %s 数据失败: %s", symbol, str(e))
            return pd.DataFrame()


# 测试代码
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    provider = CurlDataProvider()

    print("\n=== 测试获取股票列表 ===")
    stock_list = provider.get_stock_list()
    if not stock_list.empty:
        print(f"获取到 {len(stock_list)} 只股票")
        print(stock_list.head())

    print("\n=== 测试获取600519日K线 ===")
    df = provider.get_stock_daily("600519", "20240101", "20240131")
    if not df.empty:
        print(f"获取到 {len(df)} 条记录")
        print(df.head())

    print("\n=== 测试获取沪深300指数 ===")
    df = provider.get_index_daily("000300", "20240101", "20240131")
    if not df.empty:
        print(f"获取到 {len(df)} 条记录")
        print(df.head())
