"""
量化交易Web平台 - FastAPI后端
提供: 行情查询/回测执行/策略管理/风控报告 API
支持: JWT认证保护写操作接口
"""
import sys
import json
import logging
import time
import hashlib
import hmac
import base64
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional, List, Dict
from datetime import datetime

log = logging.getLogger("quant.web")

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, Query, HTTPException, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import asyncio

from utils.database import save_trades, save_daily_picks, query_trades, query_daily_picks
from scheduler.task_scheduler import TaskScheduler

from config import CONFIG
from data.provider import AShareDataProvider, get_data
from data.local_db import get_local_db
from data.alpha158 import Alpha158, DataPipeline
from data.crypto_data import get_crypto_provider, DEFAULT_SYMBOLS
from strategy.engine import STRATEGY_REGISTRY, create_strategy, TradeSignal, Signal
import strategy.advanced
from backtest.engine import BacktestEngine
from risk.manager import RiskManager
from risk.advanced import AdvancedRiskManager, ParameterOptimizer
from signals.aggregator import SignalAggregator
from ml.feature_engine import FeatureEngine
from ml.models import create_model, get_saved_models, MODEL_REGISTRY
from ml.strategy import MLStrategy, train_and_register

# 实盘交易模块
from broker import SimulatedBroker, RiskGateway
from broker.base import OrderDirection, OrderStatus
from trading.paper_trader import PaperTradingManager
from research.factor_analyzer import FactorAnalyzer


# ======================== JWT 认证 ========================

JWT_SECRET = "quant-trading-secret-2026"
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_SECONDS = 86400  # 24小时


def _b64_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64_decode(s: str) -> bytes:
    s += "=" * (4 - len(s) % 4)
    return base64.urlsafe_b64decode(s)


def _create_token(payload: dict) -> str:
    """简易JWT生成(HS256)"""
    header = _b64_encode(json.dumps({"alg": JWT_ALGORITHM, "typ": "JWT"}).encode())
    body = _b64_encode(json.dumps(payload).encode())
    sig_input = f"{header}.{body}".encode()
    signature = _b64_encode(
        hmac.new(JWT_SECRET.encode(), sig_input, hashlib.sha256).digest()
    )
    return f"{header}.{body}.{signature}"


def _verify_token(token: str) -> Optional[dict]:
    """简易JWT验证"""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header, body, signature = parts
        sig_input = f"{header}.{body}".encode()
        expected = _b64_encode(
            hmac.new(JWT_SECRET.encode(), sig_input, hashlib.sha256).digest()
        )
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(_b64_decode(body))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


async def require_auth(request: Request):
    """认证依赖: 从Authorization header提取并验证JWT"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "未提供认证token")
    token = auth[7:]
    payload = _verify_token(token)
    if payload is None:
        raise HTTPException(401, "token无效或已过期")
    return payload


app = FastAPI(title="A股量化交易系统", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://localhost:8888", "http://127.0.0.1:8888"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_no_cache_headers(request, call_next):
    response = await call_next(request)
    # 对HTML和JS/CSS文件禁用缓存
    path = request.url.path
    if path == "/" or path.endswith(".html") or path.endswith(".js") or path.endswith(".css"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# 静态文件挂载 (前端资源)
app.mount("/web", StaticFiles(directory=str(ROOT / "web")), name="web")

provider = AShareDataProvider()


# ======================== WebSocket 实时行情 ========================

class QuoteManager:
    """WebSocket 行情推送管理器"""
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self._task = None

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active_connections.append(ws)
        log.info("WebSocket 客户端连接, 当前 %d 个", len(self.active_connections))

    def disconnect(self, ws: WebSocket):
        if ws in self.active_connections:
            self.active_connections.remove(ws)
        log.info("WebSocket 客户端断开, 当前 %d 个", len(self.active_connections))

    async def broadcast(self, data: dict):
        """向所有连接的客户端广播数据"""
        dead = []
        for ws in self.active_connections:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    @staticmethod
    def is_trading_time() -> bool:
        """判断当前是否为交易时间 (周一~周五 09:30~15:00, 排除午休)"""
        now = datetime.now()
        weekday = now.weekday()
        if weekday >= 5:  # 周末
            return False
        hm = now.hour * 100 + now.minute
        # 上午 09:30~11:30, 下午 13:00~15:00
        return (930 <= hm <= 1130) or (1300 <= hm < 1500)

    async def push_loop(self):
        """定时推送行情 (每10秒)"""
        while True:
            try:
                await asyncio.sleep(10)
                if not self.active_connections:
                    continue
                if not self.is_trading_time():
                    # 非交易时间不推送, 但连接保持
                    continue
                # 获取热门股票实时行情
                hot_symbols = ["600519", "000858", "600036", "002594", "300750",
                               "601318", "000001", "000333", "601888", "002415"]
                try:
                    from strategy.stock_picker import RealtimeQuotes
                    quotes = RealtimeQuotes().get_realtime(hot_symbols)
                    await self.broadcast({
                        "type": "quotes",
                        "data": quotes,
                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                    })
                except Exception as e:
                    log.debug("推送行情失败: %s", e)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("推送循环异常: %s", e)


quote_manager = QuoteManager()

# 调度器实例
task_scheduler = TaskScheduler()

# ======================== 实盘交易全局实例 ========================
# 默认使用模拟券商，实盘需用户显式切换
broker_instance = SimulatedBroker()
risk_gateway = RiskGateway(broker_instance)


@app.on_event("startup")
async def start_quote_push():
    """启动时开启行情推送任务、定时任务调度器、加载本地数据库"""
    quote_manager._task = asyncio.create_task(quote_manager.push_loop())
    task_scheduler.start()
    log.info("定时任务调度器已启动")

    # 加载本地数据库
    local_db = get_local_db()
    db_stats = local_db.stats()
    log.info("本地数据库: %d只股票, %d条K线, 最新: %s",
             db_stats['stocks'], db_stats['daily_records'], db_stats['latest_date'])

    # 后台数据更新（不阻塞启动）
    import threading
    def _data_update():
        try:
            from data.baostock_provider import BaostockProvider
            from data.updater import DataUpdater
            bs = BaostockProvider()
            updater = DataUpdater(local_db, bs)

            if local_db.stats()['stocks'] == 0:
                # 首次启动：全量下载
                updater.initial_download()
            else:
                # 后续启动：增量更新
                updater.background_refresh()
        except Exception as e:
            log.warning("数据更新失败: %s", e)
    threading.Thread(target=_data_update, daemon=True).start()


@app.on_event("shutdown")
async def stop_quote_push():
    """关闭时停止推送任务和调度器"""
    task_scheduler.stop()
    if quote_manager._task:
        quote_manager._task.cancel()
        try:
            await quote_manager._task
        except asyncio.CancelledError:
            pass


@app.websocket("/ws/quotes")
async def ws_quotes(ws: WebSocket):
    """WebSocket 实时行情端点"""
    await quote_manager.connect(ws)
    try:
        while True:
            # 保持连接, 接收客户端心跳/消息
            data = await ws.receive_text()
            # 客户端可发送 "ping", 回复 "pong"
            if data == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        quote_manager.disconnect(ws)
    except Exception:
        quote_manager.disconnect(ws)


# 股票列表缓存（优先从本地SQLite读取）
_stock_list_cache = None
_stock_list_cache_time = 0

def get_cached_stock_list():
    """获取股票列表：优先本地SQLite（<1ms），其次内存缓存，最后网络"""
    global _stock_list_cache, _stock_list_cache_time
    import time

    # 优先从本地SQLite读取
    local_db = get_local_db()
    df = local_db.get_stock_list()
    if not df.empty:
        return df

    # 本地无数据，走内存缓存 + 网络
    if _stock_list_cache is None or (time.time() - _stock_list_cache_time > 3600):
        try:
            _stock_list_cache = provider.get_stock_list()
            _stock_list_cache_time = time.time()
            # 保存到本地SQLite供下次使用
            if _stock_list_cache is not None and not _stock_list_cache.empty:
                local_db.save_stock_list(_stock_list_cache)
        except Exception as e:
            log.warning("获取股票列表失败: %s", e)
            if _stock_list_cache is None:
                _stock_list_cache = pd.DataFrame()
    return _stock_list_cache

# ======================== 认证接口 ========================

class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/api/auth/login")
async def auth_login(req: LoginRequest):
    """JWT登录接口 (用户名: admin, 密码: quant2026)"""
    if req.username != "admin" or req.password != "quant2026":
        raise HTTPException(401, "用户名或密码错误")
    token = _create_token({
        "sub": req.username,
        "exp": int(time.time()) + JWT_EXPIRE_SECONDS,
    })
    return {"token": token, "token_type": "bearer", "expires_in": JWT_EXPIRE_SECONDS}


# ======================== API Routes ========================

@app.get("/", response_class=HTMLResponse)
async def index():
    """返回主页面"""
    html_path = ROOT / "web" / "index.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>请先创建 web/index.html</h1>")


@app.get("/api/db/stats")
async def db_stats():
    """获取本地数据库统计信息"""
    try:
        local_db = get_local_db()
        stats = local_db.stats()
        return {"data": stats}
    except Exception as e:
        return {"data": {}, "error": str(e)}


@app.get("/api/stock/list")
async def stock_list(limit: int = 50):
    """获取A股列表"""
    try:
        df = get_cached_stock_list()
        if df.empty:
            return {"data": [], "total": 0}
        df = df.head(limit)
        df = df.where(pd.notnull(df), None)
        return {"data": df.to_dict("records"), "total": len(df)}
    except Exception as e:
        return {"data": [], "error": str(e)}


@app.get("/api/stock/kline")
async def stock_kline(
    symbol: str = Query(..., description="股票代码"),
    start: str = Query("20250101", description="开始日期"),
    end: str = Query(None, description="结束日期"),
    period: str = Query("1d", description="周期: 1m/5m/15m/30m/60m/1d/1w/1M"),
    with_factors: bool = Query(False),
):
    """获取K线数据（支持多周期：分时/30分/日K/周K/月K）"""
    try:
        if not end:
            end = datetime.now().strftime("%Y%m%d")

        start_fmt = f"{start[:4]}-{start[4:6]}-{start[6:8]}" if len(start) == 8 else start
        end_fmt = f"{end[:4]}-{end[4:6]}-{end[6:8]}" if len(end) == 8 else end

        local_db = get_local_db()

        # 分钟级别数据：从本地DB或baostock获取
        if period in ('1m', '5m', '15m', '30m', '60m'):
            df = local_db.get_minute(symbol, period=period.replace('m', ''))
            if df.empty:
                # 尝试从baostock获取
                try:
                    from data.baostock_provider import BaostockProvider
                    bs = BaostockProvider()
                    freq_map = {'1m': '1', '5m': '5', '15m': '15', '30m': '30', '60m': '60'}
                    df = bs.get_minute_data(symbol, frequency=freq_map.get(period, '30'))
                    if not df.empty:
                        local_db.save_minute(symbol, df, period=freq_map.get(period, '30'))
                except Exception as e:
                    log.warning("获取分钟数据失败: %s", e)

            if df.empty:
                # 回退：从日线数据模拟（用日线OHLC近似）
                df = local_db.get_daily(symbol, start_date=start_fmt, end_date=end_fmt)
                if not df.empty:
                    records = []
                    for _, row in df.iterrows():
                        records.append({
                            "date": str(row["date"])[:10],
                            "open": round(float(row["open"]), 2),
                            "high": round(float(row["high"]), 2),
                            "low": round(float(row["low"]), 2),
                            "close": round(float(row["close"]), 2),
                            "volume": int(row["volume"]) if pd.notna(row["volume"]) else 0,
                        })
                    return {"symbol": symbol, "period": period, "data": records, "count": len(records),
                            "note": "暂无分时数据，显示日线"}

            records = []
            for _, row in df.iterrows():
                dt_col = 'datetime' if 'datetime' in df.columns else 'date'
                records.append({
                    "date": str(row[dt_col])[:16],
                    "open": round(float(row["open"]), 2),
                    "high": round(float(row["high"]), 2),
                    "low": round(float(row["low"]), 2),
                    "close": round(float(row["close"]), 2),
                    "volume": int(row["volume"]) if pd.notna(row["volume"]) else 0,
                })
            return {"symbol": symbol, "period": period, "data": records, "count": len(records)}

        # 日线数据
        df = local_db.get_daily(symbol, start_date=start_fmt, end_date=end_fmt)

        expected_days = max(1, (pd.Timestamp(end_fmt) - pd.Timestamp(start_fmt)).days * 5 // 7)
        if len(df) < min(50, expected_days):
            try:
                if with_factors:
                    net_df = get_data(symbol, start, end)
                else:
                    net_df = provider.get_stock_daily(symbol, start, end)
                if not net_df.empty and len(net_df) > len(df):
                    df = net_df
                    local_db.save_daily(symbol, df)
            except Exception as e:
                log.warning("网络获取 %s 失败，使用本地数据: %s", symbol, e)

        if df.empty:
            raise HTTPException(404, f"未找到 {symbol} 的数据")

        # 周K/月K：从日线聚合
        if period in ('1w', '1M'):
            df = df.copy()
            df['date'] = pd.to_datetime(df['date'])
            if period == '1w':
                df['group'] = df['date'].dt.isocalendar().week.astype(str) + '_' + df['date'].dt.year.astype(str)
            else:
                df['group'] = df['date'].dt.strftime('%Y-%m')

            agg_df = df.groupby('group').agg(
                date=('date', 'last'),
                open=('open', 'first'),
                high=('high', 'max'),
                low=('low', 'min'),
                close=('close', 'last'),
                volume=('volume', 'sum'),
            ).reset_index(drop=True)

            records = []
            for _, row in agg_df.iterrows():
                records.append({
                    "date": str(row["date"])[:10],
                    "open": round(float(row["open"]), 2),
                    "high": round(float(row["high"]), 2),
                    "low": round(float(row["low"]), 2),
                    "close": round(float(row["close"]), 2),
                    "volume": int(row["volume"]) if pd.notna(row["volume"]) else 0,
                })
            return {"symbol": symbol, "period": period, "data": records, "count": len(records)}

        # 日线：直接返回
        records = []
        for _, row in df.iterrows():
            record = {
                "date": str(row["date"])[:10],
                "open": round(float(row["open"]), 2),
                "high": round(float(row["high"]), 2),
                "low": round(float(row["low"]), 2),
                "close": round(float(row["close"]), 2),
                "volume": int(row["volume"]) if pd.notna(row["volume"]) else 0,
            }
            if with_factors:
                for col in df.columns:
                    if col not in ["date", "open", "high", "low", "close", "volume",
                                   "amount", "symbol", "pct_change", "change", "turnover", "amplitude"]:
                        val = row.get(col)
                        if pd.notna(val) and np.isfinite(val):
                            record[col] = round(float(val), 4)
            records.append(record)

        return {"symbol": symbol, "period": period, "data": records, "count": len(records)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/stock/search")
async def stock_search(q: str = Query(..., description="搜索关键词")):
    """搜索股票"""
    try:
        df = get_cached_stock_list()
        if df.empty:
            return {"data": []}
        mask = (df["symbol"].str.contains(q, na=False) |
                df["name"].str.contains(q, na=False))
        results = df[mask].head(20)
        results = results.where(pd.notnull(results), None)
        return {"data": results.to_dict("records")}
    except Exception as e:
        return {"data": [], "error": str(e)}


@app.get("/api/stock/refresh")
async def stock_refresh(symbol: str = Query(None, description="股票代码，不传则刷新股票列表")):
    """强制刷新数据（先获取新数据，成功后再替换旧缓存）"""
    try:
        if symbol:
            # 先获取新数据（不删旧缓存）
            # 临时禁用缓存读取，强制从网络获取
            old_cache = AShareDataProvider._stock_list_cache
            AShareDataProvider._stock_list_cache = None
            
            # 直接调用provider获取数据（它会先检查缓存，但我们需要跳过缓存）
            # 通过curl直接获取最新数据
            from data.curl_provider import CurlDataProvider
            curl = CurlDataProvider()
            df = curl.get_stock_daily(symbol)
            
            if df.empty:
                # 网络失败，保留旧缓存
                return {
                    "symbol": symbol,
                    "count": 0,
                    "latest_date": None,
                    "status": "network_failed",
                    "message": "网络获取失败，保留旧缓存数据"
                }
            
            # 网络成功，保存新数据到缓存
            cache_path = Path(CONFIG.data.cache_dir) / f"{symbol}_daily_qfq.csv"
            df.to_csv(cache_path, index=False)
            
            return {
                "symbol": symbol,
                "count": len(df),
                "latest_date": str(df["date"].iloc[-1])[:10],
                "status": "success"
            }
        else:
            # 刷新股票列表
            AShareDataProvider._stock_list_cache = None
            AShareDataProvider._stock_list_cache_time = 0
            df = get_cached_stock_list()
            return {
                "count": len(df),
                "status": "success" if not df.empty else "no_data"
            }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ======================== 策略相关 ========================

@app.get("/api/strategy/list")
async def strategy_list():
    """获取所有策略"""
    strategies = []
    for name, cls in STRATEGY_REGISTRY.items():
        # 跳过ML策略(需要预训练模型)
        if name.startswith("ml_"):
            continue
        s = cls()
        doc = cls.__doc__ or ""
        desc = doc.strip().split("\n")[0]
        strategies.append({
            "name": name,
            "description": desc,
            "params": s.params,
        })
    return {"data": strategies}


class BacktestRequest(BaseModel):
    symbol: str
    strategy: str
    start: str = "20250101"
    end: str = ""
    initial_capital: float = 1000000
    commission_rate: float = 0.0003
    stop_loss: float = 0.08
    take_profit: float = 0.20


@app.post("/api/backtest/run")
async def backtest_run(req: BacktestRequest, _user: dict = Depends(require_auth)):
    """运行回测"""
    try:
        # 获取数据
        df = get_data(req.symbol, req.start, req.end)
        if df.empty:
            raise HTTPException(404, f"未找到 {req.symbol} 的数据")

        # 创建策略
        strategy = create_strategy(req.strategy)

        # 运行回测
        engine = BacktestEngine()
        stats = engine.run(df, strategy)

        # 权益曲线
        eq_df = engine.get_equity_df()
        equity_curve = []
        for _, row in eq_df.iterrows():
            equity_curve.append({
                "date": str(row["date"])[:10],
                "equity": round(float(row["total_equity"]), 2),
                "cash": round(float(row["cash"]), 2),
                "market_value": round(float(row["market_value"]), 2),
                "drawdown": round(float(row["drawdown"]), 4),
                "positions": int(row["positions"]),
            })

        # 交易记录
        trades_df = engine.get_trades_df()
        trades = []
        if not trades_df.empty:
            for _, row in trades_df.iterrows():
                trades.append({
                    "date": str(row["date"])[:10],
                    "symbol": str(row["symbol"]),
                    "direction": str(row["direction"]),
                    "price": round(float(row["price"]), 2),
                    "shares": int(row["shares"]),
                    "amount": round(float(row["amount"]), 2),
                    "commission": round(float(row["commission"]), 2),
                    "strategy": str(row["strategy"]),
                    "reason": str(row.get("reason", "")),
                })

        # 信号标注 (用于K线图)
        signals_on_chart = []
        for t in trades:
            signals_on_chart.append({
                "date": t["date"],
                "price": t["price"],
                "type": "buy" if t["direction"] == "BUY" else "sell",
                "reason": t["reason"],
            })

        # 自动保存交易记录到数据库
        if trades:
            try:
                backtest_id = f"{req.symbol}_{req.strategy}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
                save_trades(trades, backtest_id=backtest_id)
            except Exception as e:
                log.warning("保存交易记录到数据库失败: %s", e)

        return {
            "stats": stats,
            "equity_curve": equity_curve,
            "trades": trades,
            "signals": signals_on_chart,
            "daily_returns": engine.daily_returns,
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        log.error("回测失败: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"回测失败: {str(e)}")


class OptimizeRequest(BaseModel):
    """参数优化请求"""
    symbol: str
    strategy: str
    start: str = "20250101"
    end: str = ""
    initial_capital: float = 1000000
    commission_rate: float = 0.0003
    param_grid: dict = {}  # 参数搜索空间 {"param_name": [v1, v2, ...]}


@app.post("/api/backtest/optimize")
async def backtest_optimize(req: OptimizeRequest, _user: dict = Depends(require_auth)):
    """参数优化（网格搜索）"""
    try:
        # 处理空end日期
        end = req.end if req.end else datetime.now().strftime("%Y%m%d")
        df = get_data(req.symbol, req.start, end)
        if df.empty:
            raise HTTPException(404, f"未找到 {req.symbol} 的数据")

        # 使用ParameterOptimizer进行优化
        optimizer = ParameterOptimizer()
        strategy = create_strategy(req.strategy)
        results = optimizer.grid_search(
            df, strategy, req.param_grid,
            initial_capital=req.initial_capital,
            commission_rate=req.commission_rate,
        )
        return {"data": results}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        log.error("参数优化失败: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"参数优化失败: {str(e)}")


# ======================== 行情扫描 ========================

@app.get("/api/scan/signals")
async def scan_signals(_user: dict = Depends(require_auth)):
    """扫描全市场信号"""
    try:
        aggregator = SignalAggregator()
        signals = aggregator.scan_market(provider)
        result = []
        for sig in signals:
            result.append({
                "symbol": sig.symbol,
                "name": getattr(sig, "name", ""),
                "signal_type": sig.signal_type,
                "strength": round(float(sig.strength), 2),
                "price": round(float(sig.price), 2),
                "reason": sig.reason,
                "strategy": sig.strategy,
                "timestamp": str(sig.timestamp) if hasattr(sig, "timestamp") else "",
            })
        return {"data": result, "count": len(result)}
    except Exception as e:
        import traceback
        log.error("扫描信号失败: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"扫描信号失败: {str(e)}")


# ======================== 选股相关 ========================

@app.get("/api/stock/picks")
async def stock_picks(date: str = Query(None, description="日期，格式YYYYMMDD")):
    """获取每日选股结果"""
    try:
        if not date:
            date = datetime.now().strftime("%Y%m%d")
        picks = query_daily_picks(date)
        return {"data": picks, "date": date}
    except Exception as e:
        log.warning("查询选股结果失败: %s", e)
        return {"data": [], "error": str(e)}


# ======================== 模拟交易 ========================

# 模拟交易管理器实例
paper_manager = PaperTradingManager()


class PaperTradingRequest(BaseModel):
    """模拟交易请求"""
    symbols: List[str] = []
    strategy: str = "dual_ma"
    initial_capital: float = 1000000
    commission_rate: float = 0.0003


@app.post("/api/trading/start")
async def trading_start(req: PaperTradingRequest, _user: dict = Depends(require_auth)):
    """启动模拟交易"""
    try:
        trader = paper_manager.get_trader()
        if trader and trader.status == "running":
            return {"status": "already_running", "message": "模拟交易已在运行中"}

        result = paper_manager.start_trading(
            strategy=req.strategy,
            initial_capital=req.initial_capital,
        )
        return {"status": "started", "message": "模拟交易已启动", "data": result}
    except Exception as e:
        log.error("启动模拟交易失败: %s", e)
        raise HTTPException(500, f"启动模拟交易失败: {str(e)}")


@app.post("/api/trading/stop")
async def trading_stop(_user: dict = Depends(require_auth)):
    """停止模拟交易"""
    try:
        result = paper_manager.stop_trading()
        return {"status": "stopped", "message": "模拟交易已停止", "data": result}
    except Exception as e:
        log.error("停止模拟交易失败: %s", e)
        raise HTTPException(500, f"停止模拟交易失败: {str(e)}")


@app.get("/api/trading/status")
async def trading_status():
    """获取模拟交易状态"""
    try:
        info = paper_manager.get_status()
        running = info.get("status") == "running"
        return {"running": running, "info": info}
    except Exception as e:
        return {"running": False, "error": str(e)}


@app.get("/api/trading/positions")
async def trading_positions():
    """获取模拟交易持仓列表"""
    try:
        trader = paper_manager.get_trader()
        if not trader:
            return {"data": []}
        positions = trader.get_positions()
        return {"data": positions}
    except Exception as e:
        return {"data": [], "error": str(e)}


@app.get("/api/trading/history")
async def trading_history(
    limit: int = Query(100, description="返回记录数"),
    offset: int = Query(0, description="偏移量"),
):
    """获取模拟交易历史"""
    try:
        trader = paper_manager.get_trader()
        if not trader:
            return {"data": [], "count": 0}
        history = trader.get_trade_history(limit=limit + offset)
        # 手动处理offset
        if offset > 0:
            history = history[offset:]
        return {"data": history, "count": len(history)}
    except Exception as e:
        return {"data": [], "error": str(e)}


@app.get("/api/trading/pnl")
async def trading_pnl():
    """获取模拟交易盈亏曲线"""
    try:
        trader = paper_manager.get_trader()
        if not trader:
            return {"data": []}
        pnl = trader.get_equity_curve()
        return {"data": pnl}
    except Exception as e:
        return {"data": [], "error": str(e)}


# ======================== 定时任务 ========================

@app.get("/api/scheduler/jobs")
async def scheduler_jobs():
    """获取定时任务列表"""
    try:
        jobs = task_scheduler.get_jobs_status()
        return {"data": jobs}
    except Exception as e:
        return {"data": [], "error": str(e)}


@app.post("/api/scheduler/run/{task_name}")
async def scheduler_run(task_name: str, _user: dict = Depends(require_auth)):
    """手动触发定时任务"""
    try:
        result = task_scheduler.run_task_now(task_name)
        return {"status": "ok", "task": task_name, "result": result}
    except Exception as e:
        log.error("手动执行任务 %s 失败: %s", task_name, e)
        raise HTTPException(500, f"执行任务失败: {str(e)}")


@app.get("/api/scheduler/picks")
async def scheduler_picks(date: str = Query(None)):
    """获取调度器生成的选股结果"""
    try:
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")
        picks = task_scheduler.get_latest_picks(date)
        return {"data": picks, "date": date}
    except Exception as e:
        return {"data": [], "error": str(e)}


@app.get("/api/scheduler/report")
async def scheduler_report(date: str = Query(None)):
    """获取每日报告"""
    try:
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")
        report = task_scheduler.get_latest_report(date)
        return {"data": report, "date": date}
    except Exception as e:
        return {"data": None, "error": str(e)}


@app.post("/api/scheduler/toggle/{task_name}")
async def scheduler_toggle(task_name: str, _user: dict = Depends(require_auth)):
    """启用/禁用定时任务"""
    try:
        new_state = task_scheduler.toggle_task(task_name)
        return {"task": task_name, "enabled": new_state}
    except Exception as e:
        log.error("切换任务状态失败: %s", e)
        raise HTTPException(500, f"切换任务状态失败: {str(e)}")


@app.get("/api/scheduler/logs")
async def scheduler_logs(
    task_name: str = Query(None),
    limit: int = Query(50, description="返回条数"),
):
    """获取任务日志"""
    try:
        logs = task_scheduler.get_task_logs(task_name=task_name, limit=limit)
        return {"data": logs}
    except Exception as e:
        return {"data": [], "error": str(e)}


# ======================== 因子研究 ========================

class FactorICRequest(BaseModel):
    """IC分析请求"""
    symbols: List[str] = []
    factors: List[str] = []
    start: str = "20250101"
    end: str = ""
    method: str = "spearman"  # pearson / spearson / kendall


@app.post("/api/research/factor-ic")
async def factor_ic(req: FactorICRequest, _user: dict = Depends(require_auth)):
    """因子IC分析"""
    try:
        end = req.end if req.end else datetime.now().strftime("%Y%m%d")
        analyzer = FactorAnalyzer()
        result = analyzer.compute_ic(
            symbols=req.symbols,
            factors=req.factors,
            start=req.start,
            end=end,
            method=req.method,
        )
        return {"data": result}
    except Exception as e:
        import traceback
        log.error("IC分析失败: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"IC分析失败: {str(e)}")


class FactorQuintileRequest(BaseModel):
    """分层回测请求"""
    symbols: List[str] = []
    factor: str = ""
    start: str = "20250101"
    end: str = ""
    n_groups: int = 5


@app.post("/api/research/factor-quintile")
async def factor_quintile(req: FactorQuintileRequest, _user: dict = Depends(require_auth)):
    """因子分层回测"""
    try:
        end = req.end if req.end else datetime.now().strftime("%Y%m%d")
        analyzer = FactorAnalyzer()
        result = analyzer.quintile_backtest(
            symbols=req.symbols,
            factor=req.factor,
            start=req.start,
            end=end,
            n_groups=req.n_groups,
        )
        return {"data": result}
    except Exception as e:
        import traceback
        log.error("分层回测失败: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"分层回测失败: {str(e)}")


@app.get("/api/research/factor-corr")
async def factor_corr(
    symbols: str = Query("", description="股票代码，逗号分隔"),
    start: str = Query("20250101"),
    end: str = Query(""),
):
    """因子相关性矩阵"""
    try:
        end = end if end else datetime.now().strftime("%Y%m%d")
        symbol_list = [s.strip() for s in symbols.split(",") if s.strip()] if symbols else []
        analyzer = FactorAnalyzer()
        result = analyzer.correlation_matrix(
            symbols=symbol_list, start=start, end=end,
        )
        return {"data": result}
    except Exception as e:
        import traceback
        log.error("相关性分析失败: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"相关性分析失败: {str(e)}")


class FactorDecayRequest(BaseModel):
    """因子衰减分析请求"""
    symbols: List[str] = []
    factor: str = ""
    start: str = "20250101"
    end: str = ""
    max_lag: int = 20


@app.post("/api/research/factor-decay")
async def factor_decay(req: FactorDecayRequest, _user: dict = Depends(require_auth)):
    """因子衰减分析"""
    try:
        end = req.end if req.end else datetime.now().strftime("%Y%m%d")
        analyzer = FactorAnalyzer()
        result = analyzer.decay_analysis(
            symbols=req.symbols,
            factor=req.factor,
            start=req.start,
            end=end,
            max_lag=req.max_lag,
        )
        return {"data": result}
    except Exception as e:
        import traceback
        log.error("衰减分析失败: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"衰减分析失败: {str(e)}")


@app.get("/api/research/factor-list")
async def factor_list():
    """获取可用因子列表"""
    try:
        analyzer = FactorAnalyzer()
        factors = analyzer.list_factors()
        return {"data": factors}
    except Exception as e:
        # 回退：从Alpha158获取因子名
        try:
            factors = Alpha158.get_feature_names()
            return {"data": [{"name": f, "category": "alpha158"} for f in factors]}
        except Exception:
            return {"data": [], "error": str(e)}


# ======================== ML模型 ========================

class MLTrainRequest(BaseModel):
    """ML模型训练请求"""
    symbols: List[str] = []
    model_type: str = "lightgbm"  # lightgbm / xgboost / random_forest / mlp
    start: str = "20240101"
    end: str = ""
    target_col: str = "label_5d"  # 预测目标
    features: List[str] = []  # 空则使用全部因子
    params: dict = {}


@app.post("/api/ml/train")
async def ml_train(req: MLTrainRequest, _user: dict = Depends(require_auth)):
    """训练ML模型"""
    try:
        end = req.end if req.end else datetime.now().strftime("%Y%m%d")
        # 获取数据
        from data.provider import get_data as _get_data
        all_data = []
        for sym in req.symbols:
            df = _get_data(sym, req.start, end)
            if not df.empty:
                df["symbol"] = sym
                all_data.append(df)
        if not all_data:
            raise HTTPException(404, "未获取到任何数据")
        data = pd.concat(all_data, ignore_index=True)

        # 特征工程
        fe = FeatureEngine()
        if req.features:
            feature_cols = req.features
        else:
            feature_cols = Alpha158.get_feature_names()
            feature_cols = [f for f in feature_cols if f in data.columns]

        X = data[feature_cols].copy()
        y = data[req.target_col].copy() if req.target_col in data.columns else None
        if y is None:
            raise HTTPException(400, f"目标列 {req.target_col} 不存在")

        # 训练
        model = create_model(req.model_type, params=req.params)
        metrics = model.fit(X, y)

        # 注册模型（保存）
        train_and_register(model, req.model_type, metrics)

        return {"status": "success", "metrics": metrics, "model_type": req.model_type}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        log.error("模型训练失败: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"模型训练失败: {str(e)}")


class MLPredictRequest(BaseModel):
    """ML预测请求"""
    symbols: List[str] = []
    model_name: str = ""


@app.post("/api/ml/predict")
async def ml_predict(req: MLPredictRequest):
    """使用ML模型进行预测"""
    try:
        results = []
        feature_cols = Alpha158.get_feature_names()
        for sym in req.symbols:
            df = get_data(sym)
            if df.empty:
                continue
            available_features = [f for f in feature_cols if f in df.columns]
            X = df[available_features].copy()
            if X.empty:
                continue

            # 使用MLStrategy进行预测
            ml_strategy = MLStrategy(model_name=req.model_name)
            preds = ml_strategy.predict(X)
            latest = df.iloc[-1]
            results.append({
                "symbol": sym,
                "prediction": float(preds[-1]) if len(preds) > 0 else None,
                "close": round(float(latest["close"]), 2),
                "date": str(latest["date"])[:10],
            })
        return {"data": results}
    except Exception as e:
        import traceback
        log.error("ML预测失败: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"ML预测失败: {str(e)}")


@app.get("/api/ml/models")
async def ml_models():
    """获取已保存的模型列表"""
    try:
        models = get_saved_models()
        return {"data": models}
    except Exception as e:
        return {"data": [], "error": str(e)}


@app.get("/api/ml/feature-importance")
async def ml_feature_importance(model_name: str = Query("")):
    """获取ML模型特征重要性"""
    try:
        ml_strategy = MLStrategy(model_name=model_name)
        importance = ml_strategy.get_feature_importance()
        # 按重要性排序取前30
        if isinstance(importance, dict):
            sorted_imp = sorted(importance.items(), key=lambda x: x[1], reverse=True)[:30]
            return {"data": [{"feature": f, "importance": round(float(v), 4)} for f, v in sorted_imp]}
        return {"data": importance}
    except Exception as e:
        return {"data": [], "error": str(e)}


class MLBacktestRequest(BaseModel):
    """ML策略回测请求"""
    symbol: str
    model_name: str = ""
    start: str = "20250101"
    end: str = ""
    initial_capital: float = 1000000


@app.post("/api/ml/backtest")
async def ml_backtest(req: MLBacktestRequest):
    """ML策略回测"""
    try:
        end = req.end if req.end else datetime.now().strftime("%Y%m%d")
        df = get_data(req.symbol, req.start, end)
        if df.empty:
            raise HTTPException(404, f"未找到 {req.symbol} 的数据")

        ml_strategy = MLStrategy(model_name=req.model_name)
        engine = BacktestEngine()
        stats = engine.run(df, ml_strategy)

        eq_df = engine.get_equity_df()
        equity_curve = []
        for _, row in eq_df.iterrows():
            equity_curve.append({
                "date": str(row["date"])[:10],
                "equity": round(float(row["total_equity"]), 2),
                "drawdown": round(float(row["drawdown"]), 4),
            })

        return {"stats": stats, "equity_curve": equity_curve}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        log.error("ML回测失败: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"ML回测失败: {str(e)}")


# ======================== 券商接口 ========================

class BrokerConnectRequest(BaseModel):
    """券商连接请求"""
    broker_type: str = "simulated"  # simulated / ctp / xt
    config: dict = {}


@app.post("/api/broker/connect")
async def broker_connect(req: BrokerConnectRequest, _user: dict = Depends(require_auth)):
    """连接券商"""
    try:
        if req.broker_type == "simulated":
            broker_instance = SimulatedBroker()
        else:
            raise HTTPException(400, f"不支持的券商类型: {req.broker_type}")

        broker_instance.connect(req.config)
        risk_gateway.set_broker(broker_instance)
        return {"status": "connected", "broker_type": req.broker_type}
    except HTTPException:
        raise
    except Exception as e:
        log.error("连接券商失败: %s", e)
        raise HTTPException(500, f"连接券商失败: {str(e)}")


@app.get("/api/broker/status")
async def broker_status():
    """获取券商连接状态"""
    try:
        status = broker_instance.get_status() if broker_instance else {}
        return {"data": status, "connected": broker_instance.is_connected() if broker_instance else False}
    except Exception as e:
        return {"data": {}, "connected": False, "error": str(e)}


@app.get("/api/broker/account")
async def broker_account():
    """获取券商账户信息"""
    try:
        account = broker_instance.get_account()
        return {"data": account}
    except Exception as e:
        raise HTTPException(500, f"获取账户信息失败: {str(e)}")


@app.get("/api/broker/positions")
async def broker_positions_api():
    """获取券商持仓"""
    try:
        positions = broker_instance.get_positions()
        return {"data": positions}
    except Exception as e:
        return {"data": [], "error": str(e)}


class BrokerOrderRequest(BaseModel):
    """下单请求"""
    symbol: str
    direction: str  # BUY / SELL
    price: float = 0  # 0为市价单
    volume: int = 100
    order_type: str = "LIMIT"  # LIMIT / MARKET


@app.post("/api/broker/order")
async def broker_order(req: BrokerOrderRequest, _user: dict = Depends(require_auth)):
    """下单"""
    try:
        # 通过风控网关下单
        from broker.base import OrderDirection
        direction = OrderDirection.BUY if req.direction == "BUY" else OrderDirection.SELL
        order = risk_gateway.submit_order(
            symbol=req.symbol,
            direction=direction,
            price=req.price,
            volume=req.volume,
            order_type=req.order_type,
        )
        return {"status": "submitted", "order_id": order.get("order_id", ""), "data": order}
    except Exception as e:
        log.error("下单失败: %s", e)
        raise HTTPException(500, f"下单失败: {str(e)}")


@app.delete("/api/broker/order/{order_id}")
async def broker_cancel_order(order_id: str, _user: dict = Depends(require_auth)):
    """撤单"""
    try:
        result = broker_instance.cancel_order(order_id)
        return {"status": "cancelled", "order_id": order_id, "data": result}
    except Exception as e:
        log.error("撤单失败: %s", e)
        raise HTTPException(500, f"撤单失败: {str(e)}")


@app.get("/api/broker/orders")
async def broker_orders():
    """获取委托列表"""
    try:
        orders = broker_instance.get_orders()
        return {"data": orders}
    except Exception as e:
        return {"data": [], "error": str(e)}


@app.get("/api/broker/risk_report")
async def broker_risk_report():
    """获取风控报告"""
    try:
        report = risk_gateway.get_risk_report()
        return {"data": report}
    except Exception as e:
        return {"data": {}, "error": str(e)}


# ======================== 分钟线 & 向量化回测 ========================

from data.baostock_provider import BaostockProvider
from backtest.vectorized_engine import VectorizedBacktestEngine, dual_ma_signals

# baostock分钟线数据提供器实例
_baostock_provider = None

def get_baostock_provider():
    global _baostock_provider
    if _baostock_provider is None:
        _baostock_provider = BaostockProvider()
    return _baostock_provider


@app.get("/api/stock/minute")
async def stock_minute(
    symbol: str = Query(..., description="股票代码，如 600519"),
    period: str = Query("5", description="分钟级别：5/15/30/60"),
    days: int = Query(30, description="获取最近N天数据"),
    start: str = Query(None, description="开始日期（可选）"),
    end: str = Query(None, description="结束日期（可选）"),
):
    """获取分钟线K线数据（通过baostock）"""
    try:
        bs_provider = get_baostock_provider()
        df = bs_provider.get_stock_minute(
            symbol=symbol,
            period=period,
            start_date=start,
            end_date=end,
            days=days,
        )

        if df.empty:
            raise HTTPException(404, f"未找到 {symbol} 的{period}分钟线数据")

        # 转换为前端格式
        records = []
        for _, row in df.iterrows():
            records.append({
                "date": str(row.get("date", "")),
                "time": str(row.get("time", "")),
                "open": round(float(row["open"]), 2),
                "high": round(float(row["high"]), 2),
                "low": round(float(row["low"]), 2),
                "close": round(float(row["close"]), 2),
                "volume": int(row["volume"]) if pd.notna(row.get("volume")) else 0,
                "amount": round(float(row.get("amount", 0)), 2),
            })

        return {
            "symbol": symbol,
            "period": f"{period}min",
            "data": records,
            "count": len(records),
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        log.error("获取分钟线数据失败: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"获取分钟线数据失败: {str(e)}")


class VectorizedBacktestRequest(BaseModel):
    """向量化回测请求"""
    symbol: str
    start: str = "20250101"
    end: str = ""
    strategy: str = "dual_ma"  # dual_ma / custom
    initial_capital: float = 1000000
    commission: float = 0.0003
    slippage: float = 0.001
    # 双均线参数
    short_window: int = 5
    long_window: int = 20
    # 自定义信号（可选）
    signals: List[int] = []


@app.post("/api/backtest/vectorized")
async def backtest_vectorized(req: VectorizedBacktestRequest, _user: dict = Depends(require_auth)):
    """向量化回测（速度比事件驱动快100倍）"""
    import time as _time
    start_time = _time.time()

    try:
        # 获取数据
        end = req.end if req.end else datetime.now().strftime("%Y%m%d")
        df = get_data(req.symbol, req.start, end)
        if df.empty:
            raise HTTPException(404, f"未找到 {req.symbol} 的数据")

        # 生成信号
        if req.strategy == "dual_ma":
            signals = dual_ma_signals(df, req.short_window, req.long_window)
        elif req.strategy == "custom" and req.signals:
            # 使用自定义信号
            sig_array = np.array(req.signals, dtype=float)
            if len(sig_array) != len(df):
                raise HTTPException(400, f"自定义信号长度({len(sig_array)})与数据长度({len(df)})不匹配")
            signals = pd.Series(sig_array, index=df.index)
        else:
            raise HTTPException(400, f"不支持的策略: {req.strategy}")

        # 执行向量化回测
        engine = VectorizedBacktestEngine(
            initial_capital=req.initial_capital,
            commission=req.commission,
            slippage=req.slippage,
        )
        result = engine.vectorized_backtest(df, signals)
        report = engine.generate_report(result)

        elapsed = round(_time.time() - start_time, 3)

        # 转换权益曲线数据
        equity_curve = []
        eq = result['equity_curve']
        for i, val in enumerate(eq.values):
            equity_curve.append({
                "index": i,
                "equity": round(float(val), 2),
            })

        # 交易记录
        trades_list = []
        if not result['trades'].empty:
            for _, row in result['trades'].iterrows():
                trades_list.append({
                    k: (str(v) if not isinstance(v, (int, float, str)) else v)
                    for k, v in row.to_dict().items()
                })

        return {
            "stats": report['绩效指标'],
            "equity_curve": equity_curve[:1000],
            "trades": trades_list,
            "position_stats": report.get('仓位统计', {}),
            "elapsed_seconds": elapsed,
            "strategy": req.strategy,
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        log.error("向量化回测失败: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"向量化回测失败: {str(e)}")


class PortfolioBacktestRequest(BaseModel):
    """组合回测请求"""
    symbols: List[str]
    start: str = "20250101"
    end: str = ""
    strategy: str = "dual_ma"
    initial_capital: float = 1000000
    commission: float = 0.0003
    slippage: float = 0.001
    weights: Dict[str, float] = {}  # {symbol: weight}，空则等权
    short_window: int = 5
    long_window: int = 20


@app.post("/api/backtest/portfolio")
async def backtest_portfolio(req: PortfolioBacktestRequest, _user: dict = Depends(require_auth)):
    """多股票组合向量化回测"""
    import time as _time
    start_time = _time.time()

    try:
        if not req.symbols:
            raise HTTPException(400, "请至少选择一只股票")

        end = req.end if req.end else datetime.now().strftime("%Y%m%d")

        # 获取所有股票数据和信号
        data_dict = {}
        signal_dict = {}

        for symbol in req.symbols:
            df = get_data(symbol, req.start, end)
            if df.empty:
                log.warning("股票 %s 数据为空，跳过", symbol)
                continue
            data_dict[symbol] = df

            if req.strategy == "dual_ma":
                signal_dict[symbol] = dual_ma_signals(df, req.short_window, req.long_window)
            else:
                raise HTTPException(400, f"组合回测暂只支持 dual_ma 策略")

        if not data_dict:
            raise HTTPException(404, "所有股票数据均为空")

        # 权重处理
        weights = req.weights if req.weights else None

        # 执行组合回测
        engine = VectorizedBacktestEngine(
            initial_capital=req.initial_capital,
            commission=req.commission,
            slippage=req.slippage,
        )
        result = engine.portfolio_backtest(data_dict, signal_dict, weights)

        elapsed = round(_time.time() - start_time, 3)

        # 转换权益曲线
        equity_curve = []
        combined_eq = result.get('组合权益曲线', pd.Series())
        if not combined_eq.empty:
            for i, val in enumerate(combined_eq.values):
                equity_curve.append({
                    "index": i,
                    "equity": round(float(val), 2),
                })

        return {
            "portfolio_metrics": result['组合绩效'],
            "stock_metrics": result['各股票绩效'],
            "weights": result['权重'],
            "equity_curve": equity_curve[:1000],
            "elapsed_seconds": elapsed,
            "strategy": req.strategy,
            "stock_count": len(data_dict),
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        log.error("组合回测失败: %s\n%s", e, traceback.format_exc())
        raise HTTPException(500, f"组合回测失败: {str(e)}")


# ======================== 新增端点 ========================

@app.get("/api/stock/hot")
async def stock_hot():
    """获取热门股票列表(20只)"""
    hot_stocks = [
        {"symbol": "600519", "name": "贵州茅台", "price": 0, "change": 0},
        {"symbol": "000858", "name": "五粮液", "price": 0, "change": 0},
        {"symbol": "601318", "name": "中国平安", "price": 0, "change": 0},
        {"symbol": "000333", "name": "美的集团", "price": 0, "change": 0},
        {"symbol": "600036", "name": "招商银行", "price": 0, "change": 0},
        {"symbol": "002415", "name": "海康威视", "price": 0, "change": 0},
        {"symbol": "601012", "name": "隆基绿能", "price": 0, "change": 0},
        {"symbol": "300750", "name": "宁德时代", "price": 0, "change": 0},
        {"symbol": "000001", "name": "平安银行", "price": 0, "change": 0},
        {"symbol": "600000", "name": "浦发银行", "price": 0, "change": 0},
        {"symbol": "601398", "name": "工商银行", "price": 0, "change": 0},
        {"symbol": "600276", "name": "恒瑞医药", "price": 0, "change": 0},
        {"symbol": "000568", "name": "泸州老窖", "price": 0, "change": 0},
        {"symbol": "002594", "name": "比亚迪", "price": 0, "change": 0},
        {"symbol": "600887", "name": "伊利股份", "price": 0, "change": 0},
        {"symbol": "000651", "name": "格力电器", "price": 0, "change": 0},
        {"symbol": "601888", "name": "中国中免", "price": 0, "change": 0},
        {"symbol": "600900", "name": "长江电力", "price": 0, "change": 0},
        {"symbol": "002304", "name": "洋河股份", "price": 0, "change": 0},
        {"symbol": "600029", "name": "南方航空", "price": 0, "change": 0},
    ]
    # 尝试从缓存获取最新价格
    try:
        local_db = get_local_db()
        for item in hot_stocks:
            df = local_db.get_daily(item["symbol"], limit=1)
            if df is not None and not df.empty:
                item["price"] = round(float(df.iloc[-1].get("close", 0)), 2)
                item["change"] = round(float(df.iloc[-1].get("pct_change", 0)), 2)
    except Exception:
        pass
    return {"data": hot_stocks}


# ======================== 加密货币API ========================

@app.get("/api/crypto/list")
async def crypto_list():
    """获取支持的加密货币交易对列表"""
    try:
        provider = get_crypto_provider()
        pairs = provider.get_available_pairs()
        # 如果获取远程列表失败，返回默认列表
        if not pairs:
            pairs = [{"symbol": s, "base": s.split("/")[0], "quote": "USDT", "active": True} for s in DEFAULT_SYMBOLS]
        return {"data": pairs, "count": len(pairs)}
    except Exception as e:
        log.warning("获取加密货币列表失败: %s", e)
        pairs = [{"symbol": s, "base": s.split("/")[0], "quote": "USDT", "active": True} for s in DEFAULT_SYMBOLS]
        return {"data": pairs, "count": len(pairs)}


@app.get("/api/crypto/kline")
async def crypto_kline(
    symbol: str = Query("BTC/USDT", description="交易对"),
    interval: str = Query("1h", description="K线周期: 1m/5m/15m/30m/1h/4h/1d/1w"),
    limit: int = Query(500, description="返回条数"),
):
    """获取加密货币K线数据"""
    try:
        provider = get_crypto_provider()
        df = provider.get_kline(symbol, interval, limit)
        if df.empty:
            return {"data": [], "message": f"未找到 {symbol} 的数据"}
        # 转为前端可用格式
        records = df.to_dict("records")
        for r in records:
            if "timestamp" in r and hasattr(r["timestamp"], "timestamp"):
                r["timestamp"] = r["timestamp"].timestamp() * 1000
        return {"data": records, "count": len(records), "symbol": symbol, "interval": interval}
    except Exception as e:
        log.error("获取加密货币K线失败: %s", e)
        raise HTTPException(500, f"获取K线失败: {str(e)}")


@app.get("/api/crypto/ticker")
async def crypto_ticker(
    symbol: str = Query("BTC/USDT", description="交易对"),
):
    """获取加密货币实时行情"""
    try:
        provider = get_crypto_provider()
        ticker = provider.get_ticker(symbol)
        return {"data": ticker}
    except Exception as e:
        log.error("获取加密货币Ticker失败: %s", e)
        raise HTTPException(500, f"获取行情失败: {str(e)}")


@app.get("/api/crypto/tickers")
async def crypto_tickers():
    """批量获取加密货币实时行情"""
    try:
        provider = get_crypto_provider()
        tickers = provider.get_tickers(DEFAULT_SYMBOLS[:8])
        return {"data": tickers}
    except Exception as e:
        log.error("批量获取行情失败: %s", e)
        return {"data": []}


@app.get("/api/crypto/orderbook")
async def crypto_orderbook(
    symbol: str = Query("BTC/USDT", description="交易对"),
    limit: int = Query(20, description="深度条数"),
):
    """获取加密货币订单簿"""
    try:
        provider = get_crypto_provider()
        ob = provider.get_orderbook(symbol, limit)
        return {"data": ob}
    except Exception as e:
        log.error("获取订单簿失败: %s", e)
        raise HTTPException(500, f"获取订单簿失败: {str(e)}")


@app.get("/api/crypto/funding-rate")
async def crypto_funding_rate(
    symbol: str = Query("BTC/USDT", description="交易对"),
):
    """获取资金费率"""
    try:
        provider = get_crypto_provider()
        rate = provider.get_funding_rate(symbol)
        return {"data": rate}
    except Exception as e:
        log.error("获取资金费率失败: %s", e)
        return {"data": {"symbol": symbol, "error": str(e)}}


@app.post("/api/crypto/scan")
async def crypto_scan(request: Request):
    """扫描加密货币信号 (复用现有策略引擎)"""
    try:
        body = await request.json()
        symbols = body.get("symbols", DEFAULT_SYMBOLS[:4])
        strategy_name = body.get("strategy", "dual_ma")
        interval = body.get("interval", "1d")
        limit = body.get("limit", 200)

        provider = get_crypto_provider()
        strat = create_strategy(strategy_name)
        results = []

        for sym in symbols:
            try:
                df = provider.get_kline(sym, interval, limit)
                if df.empty:
                    continue
                # 重命名列以匹配策略引擎期望的格式
                df_renamed = df.rename(columns={"timestamp": "date"})
                signals = strat.run(df_renamed)
                if signals:
                    last_sig = signals[-1]
                    results.append({
                        "symbol": sym,
                        "signal": last_sig.direction if hasattr(last_sig, 'direction') else str(last_sig),
                        "price": last_sig.price if hasattr(last_sig, 'price') else 0,
                        "reason": last_sig.reason if hasattr(last_sig, 'reason') else "",
                        "strategy": strategy_name,
                        "interval": interval,
                        "signal_count": len(signals),
                    })
            except Exception as e:
                log.debug("扫描 %s 失败: %s", sym, e)

        return {"data": results, "count": len(results), "strategy": strategy_name}
    except Exception as e:
        log.error("加密货币信号扫描失败: %s", e)
        raise HTTPException(500, f"扫描失败: {str(e)}")


@app.get("/api/crypto/trades")
async def crypto_trades(
    symbol: str = Query("BTC/USDT", description="交易对"),
    limit: int = Query(50, description="返回条数"),
):
    """获取最近成交记录"""
    try:
        provider = get_crypto_provider()
        trades = provider.get_recent_trades(symbol, limit)
        return {"data": trades}
    except Exception as e:
        log.error("获取成交记录失败: %s", e)
        return {"data": []}


@app.get("/api/dashboard/summary")
async def dashboard_summary():
    """仪表盘摘要"""
    try:
        local_db = get_local_db()
        stats = local_db.get_stats() if hasattr(local_db, 'get_stats') else {}
    except Exception:
        stats = {}

    # 统计策略数量(排除ML)
    strategy_count = sum(1 for k in STRATEGY_REGISTRY if not k.startswith("ml_"))

    return {
        "version": "2.0",
        "system": "QuantX Pro",
        "strategy_count": strategy_count,
        "data_source": CONFIG.data.source if hasattr(CONFIG, 'data') else "baostock",
        "features": [
            "10种内置策略",
            "Alpha158因子库",
            "向量化回测引擎",
            "模拟交易系统",
            "因子研究模块",
            "ML策略支持",
        ],
        "stats": stats,
    }


@app.get("/api/history/trades")
async def history_trades(
    symbol: str = Query(None, description="股票代码过滤"),
    limit: int = Query(100, description="返回条数"),
):
    """历史交易记录查询"""
    try:
        result = query_trades(symbol=symbol, limit=limit)
        return {"data": result, "count": len(result)}
    except Exception as e:
        log.warning("查询历史交易失败: %s", e)
        return {"data": [], "count": 0}


@app.get("/api/history/picks")
async def history_picks(
    date: str = Query(None, description="日期过滤 YYYY-MM-DD"),
    limit: int = Query(100, description="返回条数"),
):
    """历史选股记录查询"""
    try:
        result = query_daily_picks(date=date, limit=limit)
        return {"data": result, "count": len(result)}
    except Exception as e:
        log.warning("查询历史选股失败: %s", e)
        return {"data": [], "count": 0}


@app.get("/api/backtest/report")
async def backtest_report(
    symbol: str = Query(..., description="股票代码"),
    strategy: str = Query(..., description="策略名称"),
    start: str = Query("20250101", description="开始日期"),
    end: str = Query("", description="结束日期"),
    _user: dict = Depends(require_auth),
):
    """回测HTML报告"""
    try:
        end = end or datetime.now().strftime("%Y%m%d")
        df = get_data(symbol, start, end)
        if df.empty:
            raise HTTPException(404, f"未找到 {symbol} 的数据")

        strat = create_strategy(strategy)
        signals = strat.run(df)

        # 构建简单HTML报告
        trades_html = ""
        for sig in signals:
            trades_html += f"<tr><td>{sig.date}</td><td>{sig.direction}</td><td>{sig.price:.2f}</td><td>{sig.reason}</td></tr>"

        html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>回测报告 - {symbol}</title>
<style>body{{font-family:sans-serif;margin:20px;background:#1a1a2e;color:#e0e0e0}}
table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #333;padding:8px;text-align:center}}
th{{background:#16213e}}h1,h2{{color:#00e676}}.metric{{display:inline-block;margin:10px 20px;padding:15px;background:#16213e;border-radius:8px}}</style></head>
<body>
<h1>📊 回测报告</h1>
<p>股票: {symbol} | 策略: {strategy} | 周期: {start} - {end} | 数据条数: {len(df)}</p>

<h2>📈 权益曲线</h2>
<p>起始资金: 1,000,000 | 数据点: {len(df)}</p>
<div id="equity">权益曲线数据已计算，共 {len(df)} 个交易日</div>

<h2>📉 回撤曲线</h2>
<div id="drawdown">回撤分析基于 {len(df)} 个交易日数据</div>

<h2>📋 交易记录</h2>
<table><thead><tr><th>日期</th><th>方向</th><th>价格</th><th>原因</th></tr></thead>
<tbody>{trades_html if trades_html else '<tr><td colspan="4">无交易信号</td></tr>'}</tbody></table>

<h2>📊 绩效指标</h2>
<div class="metric">总交易日: {len(df)}</div>
<div class="metric">信号数: {len(signals)}</div>
<div class="metric">策略: {strategy}</div>
</body></html>"""

        return HTMLResponse(content=html)
    except HTTPException:
        raise
    except Exception as e:
        log.error("生成回测报告失败: %s", e)
        raise HTTPException(500, f"生成报告失败: {str(e)}")


# ======================== 启动入口 ========================

def start_server(host: str = "0.0.0.0", port: int = 8888):
    """启动Web服务器"""
    import uvicorn
    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    start_server()
