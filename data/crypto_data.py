"""
加密货币数据模块 - 基于ccxt框架
支持: OKX交易所 (通过代理)
提供: K线/Ticker/资金费率/持仓量/爆仓数据
"""
import logging
import os
import time
import pandas as pd
from typing import Optional, List, Dict
from datetime import datetime, timedelta

log = logging.getLogger("quant.crypto")

# 支持的交易对
DEFAULT_SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "SNDK/USDT",
    "BNB/USDT", "XRP/USDT", "DOGE/USDT", "ADA/USDT",
    "AVAX/USDT", "DOT/USDT", "MATIC/USDT", "LINK/USDT",
    "UNI/USDT", "AAVE/USDT", "ATOM/USDT", "NEAR/USDT",
]

# K线周期映射
INTERVAL_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "4h": "4h", "1d": "1d", "1w": "1w", "1M": "1M",
}

# 代理配置 (OKX 在大陆需要代理) —— 通过环境变量注入, 勿硬编码真实地址
_PROXY = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
PROXY_CONFIG = {
    "http": _PROXY,
    "https": _PROXY,
}


class CryptoDataProvider:
    """加密货币数据提供者 (OKX)"""

    def __init__(self, exchange_id: str = "okx", use_proxy: bool = True):
        self.exchange_id = exchange_id
        self.use_proxy = use_proxy
        self._exchange = None
        self._init_exchange()

    def _init_exchange(self):
        """初始化交易所连接"""
        try:
            import ccxt
            exchange_class = getattr(ccxt, self.exchange_id)
            config = {
                "enableRateLimit": True,
                "timeout": 30000,
                "options": {
                    "defaultType": "spot",
                },
            }
            if self.use_proxy:
                config["proxies"] = PROXY_CONFIG

            self._exchange = exchange_class(config)
            log.info("交易所 %s 初始化成功 (代理: %s)", self.exchange_id, self.use_proxy)
        except Exception as e:
            log.error("交易所初始化失败: %s", e)
            raise

    def get_exchange_info(self) -> dict:
        """获取交易所信息"""
        try:
            markets = self._exchange.load_markets()
            symbols = [s for s in markets.keys() if "/USDT" in s]
            return {
                "exchange": self.exchange_id,
                "total_symbols": len(symbols),
                "symbols": sorted(symbols)[:100],
            }
        except Exception as e:
            log.error("获取交易所信息失败: %s", e)
            return {"exchange": self.exchange_id, "error": str(e)}

    def get_available_pairs(self) -> List[dict]:
        """获取可用交易对列表"""
        try:
            markets = self._exchange.load_markets()
            pairs = []
            for symbol, market in markets.items():
                if "/USDT" in symbol:
                    pairs.append({
                        "symbol": symbol,
                        "base": market.get("base", ""),
                        "quote": market.get("quote", "USDT"),
                        "active": market.get("active", True),
                        "price_precision": market.get("precision", {}).get("price", 8),
                        "amount_precision": market.get("precision", {}).get("amount", 8),
                    })
            # 把常用的排前面
            priority = {s: i for i, s in enumerate(DEFAULT_SYMBOLS)}
            pairs.sort(key=lambda x: priority.get(x["symbol"], 999))
            return pairs
        except Exception as e:
            log.error("获取交易对列表失败: %s", e)
            return []

    def get_kline(self, symbol: str, interval: str = "1h",
                  limit: int = 500, since: Optional[int] = None) -> pd.DataFrame:
        """
        获取K线数据

        Args:
            symbol: 交易对 如 "BTC/USDT"
            interval: K线周期 如 "1h", "4h", "1d"
            limit: 返回条数 (最大1000)
            since: 起始时间戳 (毫秒)

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        try:
            ccxt_interval = INTERVAL_MAP.get(interval, interval)
            ohlcv = self._exchange.fetch_ohlcv(
                symbol, ccxt_interval, since=since, limit=min(limit, 1000)
            )
            if not ohlcv:
                return pd.DataFrame()

            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df["date"] = df["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")
            df["symbol"] = symbol

            # 计算涨跌幅
            df["pct_change"] = df["close"].pct_change() * 100

            return df
        except Exception as e:
            log.error("获取K线失败 %s %s: %s", symbol, interval, e)
            return pd.DataFrame()

    def get_ticker(self, symbol: str) -> dict:
        """获取实时行情"""
        try:
            ticker = self._exchange.fetch_ticker(symbol)
            return {
                "symbol": symbol,
                "last": ticker.get("last", 0),
                "bid": ticker.get("bid", 0),
                "ask": ticker.get("ask", 0),
                "high": ticker.get("high", 0),
                "low": ticker.get("low", 0),
                "volume": ticker.get("baseVolume", 0),
                "quote_volume": ticker.get("quoteVolume", 0),
                "change": ticker.get("percentage", 0),
                "timestamp": ticker.get("timestamp", 0),
                "datetime": ticker.get("datetime", ""),
            }
        except Exception as e:
            log.error("获取Ticker失败 %s: %s", symbol, e)
            return {"symbol": symbol, "error": str(e)}

    def get_tickers(self, symbols: Optional[List[str]] = None) -> List[dict]:
        """批量获取实时行情"""
        if symbols is None:
            symbols = DEFAULT_SYMBOLS
        results = []
        for sym in symbols:
            try:
                ticker = self.get_ticker(sym)
                if "error" not in ticker:
                    results.append(ticker)
            except Exception:
                pass
            time.sleep(0.1)  # 避免频率限制
        return results

    def get_orderbook(self, symbol: str, limit: int = 20) -> dict:
        """获取订单簿"""
        try:
            ob = self._exchange.fetch_order_book(symbol, limit)
            return {
                "symbol": symbol,
                "bids": ob.get("bids", [])[:limit],
                "asks": ob.get("asks", [])[:limit],
                "timestamp": ob.get("timestamp", 0),
            }
        except Exception as e:
            log.error("获取订单簿失败 %s: %s", symbol, e)
            return {"symbol": symbol, "error": str(e)}

    def get_funding_rate(self, symbol: str) -> dict:
        """获取资金费率 (永续合约)"""
        try:
            # 切换到swap市场
            if hasattr(self._exchange, 'fetch_funding_rate'):
                rate = self._exchange.fetch_funding_rate(symbol)
                return {
                    "symbol": symbol,
                    "funding_rate": rate.get("fundingRate", 0),
                    "funding_timestamp": rate.get("fundingTimestamp", 0),
                    "next_funding_rate": rate.get("nextFundingRate", 0),
                    "next_funding_timestamp": rate.get("nextFundingTimestamp", 0),
                }
            return {"symbol": symbol, "error": "不支持资金费率查询"}
        except Exception as e:
            log.error("获取资金费率失败 %s: %s", symbol, e)
            return {"symbol": symbol, "error": str(e)}

    def get_recent_trades(self, symbol: str, limit: int = 50) -> List[dict]:
        """获取最近成交"""
        try:
            trades = self._exchange.fetch_trades(symbol, limit=limit)
            return [{
                "id": t.get("id", ""),
                "price": t.get("price", 0),
                "amount": t.get("amount", 0),
                "side": t.get("side", ""),
                "timestamp": t.get("timestamp", 0),
                "datetime": t.get("datetime", ""),
            } for t in trades]
        except Exception as e:
            log.error("获取最近成交失败 %s: %s", symbol, e)
            return []


# 全局实例 (延迟初始化)
_crypto_provider = None


def get_crypto_provider() -> CryptoDataProvider:
    """获取全局加密货币数据提供者"""
    global _crypto_provider
    if _crypto_provider is None:
        _crypto_provider = CryptoDataProvider()
    return _crypto_provider
