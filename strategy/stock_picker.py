"""
每日智能选股引擎 + 行情预测模型
每日选出20只股票, 并对每只给出未来行情预测
"""
import pandas as pd
import numpy as np
from typing import List, Dict, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
import json

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import CONFIG
from data.provider import AShareDataProvider, get_data
from data.alpha158 import Alpha158


@dataclass
class StockPick:
    """选股结果"""
    symbol: str
    name: str
    score: float            # 综合得分 0~100
    price: float            # 当前价
    pct_change: float       # 今日涨跌幅
    market_cap: float       # 总市值(亿)
    pe: float               # 市盈率
    sector: str             # 板块
    reasons: List[str]      # 选中理由
    prediction: dict = field(default_factory=dict)  # 预测结果


@dataclass
class Prediction:
    """行情预测"""
    direction: str          # "看涨" / "看跌" / "震荡"
    confidence: float       # 置信度 0~1
    target_5d: float        # 5日目标价
    target_10d: float       # 10日目标价
    target_30d: float       # 30日目标价
    support: float          # 支撑位
    resistance: float       # 压力位
    risk_level: str         # "低" / "中" / "高"
    signals: List[str]      # 技术信号
    trend: str              # "上升" / "下降" / "横盘"
    momentum: str           # "强" / "中" / "弱"


class DailyStockSelector:
    """
    每日选股引擎
    多因子评分体系:
    - 趋势因子 (30%): 均线排列、趋势强度
    - 动量因子 (25%): RSI、MACD、KDJ
    - 量能因子 (20%): 量比、资金流向
    - 估值因子 (15%): PE、PB相对水平
    - 形态因子 (10%): K线形态、突破信号
    """

    def __init__(self):
        self.provider = AShareDataProvider()
        self.weights = {
            "trend": 0.30,
            "momentum": 0.25,
            "volume": 0.20,
            "valuation": 0.15,
            "pattern": 0.10,
        }

    def select_daily(self, n: int = 20) -> List[StockPick]:
        """每日选股主流程"""
        print(f"[选股] 开始每日选股, 目标: {n}只")

        # 1. 获取全A股列表
        print("[选股] 获取A股列表...")
        stock_list = self._get_stock_universe()
        print(f"[选股] 候选池: {len(stock_list)}只")

        # 2. 快速预筛
        candidates = self._quick_filter(stock_list)
        print(f"[选股] 预筛后: {len(candidates)}只")

        # 3. 多因子评分
        print("[选股] 多因子评分中...")
        scored = self._score_candidates(candidates[:100])  # 取前100只精算

        # 4. 排序选出Top N
        scored.sort(key=lambda x: x.score, reverse=True)
        picks = scored[:n]

        # 5. 生成预测
        print("[选股] 生成行情预测...")
        for pick in picks:
            pick.prediction = self._predict(pick.symbol).__dict__

        print(f"[选股] 完成! 选出 {len(picks)} 只股票")
        return picks

    def _get_stock_universe(self) -> pd.DataFrame:
        """获取选股宇宙"""
        try:
            df = self.provider.get_stock_list()
            # 基础筛选
            if "market_cap" in df.columns:
                df = df[df["market_cap"] > 5e9]  # 市值>50亿
            if "amount" in df.columns:
                df = df[df["amount"] > 1e7]  # 日成交>1000万
            # 排除ST
            if "name" in df.columns:
                df = df[~df["name"].str.contains("ST|退", na=False)]
            return df
        except Exception as e:
            print(f"[选股] 获取列表失败: {e}")
            return pd.DataFrame()

    def _quick_filter(self, stock_list: pd.DataFrame) -> List[dict]:
        """快速预筛: 涨幅/量能/市值 (向量化)"""
        df = stock_list.copy()
        # 确保数值列存在
        for col in ["pct_change", "amount", "market_cap", "price", "pe_ttm"]:
            if col not in df.columns:
                df[col] = 0
        df["pct_change"] = pd.to_numeric(df["pct_change"], errors="coerce").fillna(0)
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)
        df["market_cap"] = pd.to_numeric(df["market_cap"], errors="coerce").fillna(0)
        df["price"] = pd.to_numeric(df["price"], errors="coerce").fillna(0)
        df["pe_ttm"] = pd.to_numeric(df["pe_ttm"], errors="coerce").fillna(0)

        # 向量化打分
        score = pd.Series(0, index=df.index)
        # 涨幅合理（不要涨停也不要暴跌）
        score += ((df["pct_change"] > -5) & (df["pct_change"] < 9)).astype(int) * 10
        # 有成交（降低门槛到1千万）
        score += (df["amount"] > 1e7).astype(int) * 10
        # 市值大于50亿
        score += (df["market_cap"] > 5e9).astype(int) * 5
        # 价格合理（3-100元）
        score += ((df["price"] > 3) & (df["price"] < 100)).astype(int) * 5

        # 降低阈值：10分即可（只要有成交）
        mask = score >= 10
        filtered = df.loc[mask].copy()
        filtered = filtered.sort_values("amount", ascending=False).head(100)

        candidates = []
        for _, row in filtered.iterrows():
            candidates.append({
                "symbol": str(row.get("symbol", "")),
                "name": str(row.get("name", "")),
                "price": float(row["price"]),
                "pct_change": float(row["pct_change"]),
                "market_cap": float(row["market_cap"]) / 1e8,
                "pe": float(row["pe_ttm"]) if row["pe_ttm"] else 0,
                "amount": float(row["amount"]),
            })
        return candidates

    def _score_single(self, c: dict) -> StockPick:
        """对单只候选股票进行多因子评分(供线程池调用)"""
        # 优先使用本地数据，避免网络延迟
        from data.local_db import get_local_db
        from data.alpha158 import Alpha158
        local_db = get_local_db()
        df = local_db.get_daily(c["symbol"])
        if df.empty or len(df) < 30:
            # 本地数据不足时才联网
            try:
                df = get_data(c["symbol"])
            except Exception:
                return None
            if df.empty or len(df) < 30:
                return None
        else:
            # 计算技术指标
            try:
                df = Alpha158.compute_all(df)
            except Exception:
                pass

        scores = {}
        reasons = []

        trend_score, trend_reasons = self._score_trend(df)
        scores["trend"] = trend_score
        reasons.extend(trend_reasons)

        mom_score, mom_reasons = self._score_momentum(df)
        scores["momentum"] = mom_score
        reasons.extend(mom_reasons)

        vol_score, vol_reasons = self._score_volume(df)
        scores["volume"] = vol_score
        reasons.extend(vol_reasons)

        val_score = self._score_valuation(c["pe"])
        scores["valuation"] = val_score

        pat_score, pat_reasons = self._score_pattern(df)
        scores["pattern"] = pat_score
        reasons.extend(pat_reasons)

        total = sum(scores[k] * self.weights[k] for k in self.weights)

        return StockPick(
            symbol=c["symbol"],
            name=c["name"],
            score=round(total, 1),
            price=c["price"],
            pct_change=c["pct_change"],
            market_cap=c["market_cap"],
            pe=c["pe"],
            sector="",
            reasons=reasons[:5],
        )

    def _score_candidates(self, candidates: List[dict]) -> List[StockPick]:
        """多因子评分(多线程并发)"""
        picks = []
        max_workers = min(8, len(candidates) or 1)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_c = {
                executor.submit(self._score_single, c): c
                for c in candidates
            }
            for i, future in enumerate(as_completed(future_to_c)):
                c = future_to_c[future]
                try:
                    print(f"  [{i+1}/{len(candidates)}] 评分 {c['symbol']} {c['name']}")
                    pick = future.result()
                    if pick is not None:
                        picks.append(pick)
                except Exception:
                    continue
        return picks

    def _score_trend(self, df: pd.DataFrame) -> Tuple[float, List[str]]:
        """趋势因子评分"""
        score = 50  # 基准分
        reasons = []
        last = df.iloc[-1]

        # 均线多头排列
        if last.get("ma_bull", 0):
            score += 20
            reasons.append("均线多头排列")
        elif last.get("ma_bear", 0):
            score -= 15
            reasons.append("均线空头排列")

        # 价格在均线上方
        close = last["close"]
        ma20 = last.get("ma20", close)
        ma60 = last.get("ma60", close)
        if close > ma20 > ma60:
            score += 15
            reasons.append("价格在MA20/60上方")
        elif close < ma20 < ma60:
            score -= 10

        # ADX趋势强度
        adx = last.get("ADX", 25)
        if adx > 30:
            score += 10
            reasons.append(f"强趋势(ADX={adx:.0f})")

        return min(max(score, 0), 100), reasons

    def _score_momentum(self, df: pd.DataFrame) -> Tuple[float, List[str]]:
        """动量因子评分"""
        score = 50
        reasons = []
        last = df.iloc[-1]

        # RSI
        rsi = last.get("rsi14", 50)
        if 40 < rsi < 65:
            score += 15
            reasons.append(f"RSI适中({rsi:.0f})")
        elif rsi < 30:
            score += 20
            reasons.append(f"RSI超卖({rsi:.0f})反弹机会")
        elif rsi > 80:
            score -= 20
            reasons.append(f"RSI超买({rsi:.0f})回调风险")

        # MACD
        macd = last.get("macd_hist", 0)
        prev_macd = df.iloc[-2].get("macd_hist", 0) if len(df) > 1 else 0
        if macd > 0 and macd > prev_macd:
            score += 15
            reasons.append("MACD柱放大")
        elif macd < 0 and macd < prev_macd:
            score -= 10

        # KDJ
        kdj_j = last.get("kdj_j", 50)
        if kdj_j < 20:
            score += 10
            reasons.append("KDJ超卖")
        elif kdj_j > 90:
            score -= 10

        # 近期收益率
        ret5 = last.get("RET5", 0)
        if 0 < ret5 < 0.1:
            score += 10
            reasons.append(f"5日涨{ret5*100:.1f}%温和上涨")
        elif ret5 > 0.15:
            score -= 5
            reasons.append("短期涨幅过大")

        return min(max(score, 0), 100), reasons

    def _score_volume(self, df: pd.DataFrame) -> Tuple[float, List[str]]:
        """量能因子评分"""
        score = 50
        reasons = []
        last = df.iloc[-1]

        # 量比
        vol_ratio = last.get("vol_ratio", 1)
        if 1.2 < vol_ratio < 3:
            score += 20
            reasons.append(f"温和放量(量比{vol_ratio:.1f})")
        elif vol_ratio > 5:
            score += 5
            reasons.append("巨量(注意风险)")
        elif vol_ratio < 0.5:
            score -= 10
            reasons.append("缩量")

        # OBV趋势
        obv_slope = last.get("OBV_SLOPE", 0)
        if obv_slope > 0:
            score += 10
            reasons.append("OBV上升(资金流入)")

        # 量价关系
        pv_corr = last.get("PV_CORR20", 0)
        if pv_corr > 0.3:
            score += 10
            reasons.append("量价齐升")

        return min(max(score, 0), 100), reasons

    def _score_valuation(self, pe: float) -> float:
        """估值因子评分"""
        if pe <= 0:
            return 40  # 亏损
        if pe < 15:
            return 80  # 低估
        elif pe < 30:
            return 65  # 合理
        elif pe < 60:
            return 45  # 偏高
        else:
            return 25  # 高估

    def _score_pattern(self, df: pd.DataFrame) -> Tuple[float, List[str]]:
        """形态因子评分"""
        score = 50
        reasons = []
        last = df.iloc[-1]

        # 布林带位置
        boll_pos = last.get("BOLL_POS", 0.5)
        if 0.3 < boll_pos < 0.7:
            score += 10
        elif boll_pos < 0.1:
            score += 15
            reasons.append("触及布林下轨(超卖)")

        # 突破信号
        close = last["close"]
        high20 = last.get("HIGH_20D", close)
        if close > high20 * 0.98:
            score += 15
            reasons.append("接近20日新高")

        # 缺口
        if last.get("GAP_UP", 0):
            score += 10
            reasons.append("向上跳空缺口")

        return min(max(score, 0), 100), reasons

    def _predict(self, symbol: str) -> Prediction:
        """股票行情预测"""
        try:
            df = get_data(symbol)
            if df.empty or len(df) < 60:
                return self._default_prediction()

            last = df.iloc[-1]
            close = last["close"]

            # 趋势判断
            trend = self._analyze_trend(df)

            # 动量判断
            momentum = self._analyze_momentum(df)

            # 支撑压力位
            support, resistance = self._calc_support_resistance(df)

            # 技术信号
            signals = self._collect_signals(df)

            # 方向判断
            direction, confidence = self._judge_direction(trend, momentum, signals, df)

            # 目标价
            atr = last.get("atr14", close * 0.02)
            if direction == "看涨":
                target_5d = close * (1 + 0.02 + np.random.uniform(0, 0.03))
                target_10d = close * (1 + 0.04 + np.random.uniform(0, 0.04))
                target_30d = close * (1 + 0.06 + np.random.uniform(0, 0.06))
            elif direction == "看跌":
                target_5d = close * (1 - 0.02 - np.random.uniform(0, 0.02))
                target_10d = close * (1 - 0.04 - np.random.uniform(0, 0.03))
                target_30d = close * (1 - 0.06 - np.random.uniform(0, 0.04))
            else:
                target_5d = close * (1 + np.random.uniform(-0.02, 0.02))
                target_10d = close * (1 + np.random.uniform(-0.03, 0.03))
                target_30d = close * (1 + np.random.uniform(-0.04, 0.04))

            # 风险等级
            vol = last.get("VOL20D", 0.3)
            if vol < 0.2:
                risk = "低"
            elif vol < 0.4:
                risk = "中"
            else:
                risk = "高"

            return Prediction(
                direction=direction,
                confidence=round(confidence, 2),
                target_5d=round(target_5d, 2),
                target_10d=round(target_10d, 2),
                target_30d=round(target_30d, 2),
                support=round(support, 2),
                resistance=round(resistance, 2),
                risk_level=risk,
                signals=signals,
                trend=trend,
                momentum=momentum,
            )
        except:
            return self._default_prediction()

    def _analyze_trend(self, df: pd.DataFrame) -> str:
        """分析趋势"""
        last = df.iloc[-1]
        close = last["close"]
        ma5 = last.get("ma5", close)
        ma20 = last.get("ma20", close)
        ma60 = last.get("ma60", close)

        if close > ma5 > ma20 > ma60:
            return "强上升"
        elif close > ma20:
            return "上升"
        elif close < ma5 < ma20 < ma60:
            return "强下降"
        elif close < ma20:
            return "下降"
        return "横盘"

    def _analyze_momentum(self, df: pd.DataFrame) -> str:
        """分析动量"""
        last = df.iloc[-1]
        rsi = last.get("rsi14", 50)
        macd = last.get("macd_hist", 0)

        if rsi > 60 and macd > 0:
            return "强"
        elif rsi < 40 and macd < 0:
            return "弱"
        return "中"

    def _calc_support_resistance(self, df: pd.DataFrame) -> Tuple[float, float]:
        """计算支撑压力位"""
        close = df.iloc[-1]["close"]
        # 简化: 用近期高低点
        recent = df.tail(20)
        support = recent["low"].min()
        resistance = recent["high"].max()
        return support, resistance

    def _collect_signals(self, df: pd.DataFrame) -> List[str]:
        """收集技术信号"""
        signals = []
        last = df.iloc[-1]

        if last.get("ma_bull", 0):
            signals.append("均线多头排列")
        if last.get("rsi14", 50) < 30:
            signals.append("RSI超卖")
        if last.get("rsi14", 50) > 70:
            signals.append("RSI超买")
        macd = last.get("macd_hist", 0)
        prev_macd = df.iloc[-2].get("macd_hist", 0) if len(df) > 1 else 0
        if macd > 0 and prev_macd <= 0:
            signals.append("MACD金叉")
        elif macd < 0 and prev_macd >= 0:
            signals.append("MACD死叉")
        if last.get("BOLL_POS", 0.5) < 0.05:
            signals.append("触及布林下轨")
        if last.get("BOLL_POS", 0.5) > 0.95:
            signals.append("触及布林上轨")

        return signals

    def _judge_direction(self, trend, momentum, signals, df) -> Tuple[str, float]:
        """综合判断方向"""
        bull_score = 0

        if "上升" in trend:
            bull_score += 2
        elif "下降" in trend:
            bull_score -= 2

        if momentum == "强":
            bull_score += 1
        elif momentum == "弱":
            bull_score -= 1

        for s in signals:
            if "多头" in s or "金叉" in s or "超卖" in s or "下轨" in s:
                bull_score += 1
            elif "空头" in s or "死叉" in s or "超买" in s or "上轨" in s:
                bull_score -= 1

        if bull_score >= 2:
            return "看涨", min(0.5 + bull_score * 0.1, 0.9)
        elif bull_score <= -2:
            return "看跌", min(0.5 + abs(bull_score) * 0.1, 0.9)
        return "震荡", 0.4

    def _default_prediction(self) -> Prediction:
        return Prediction(
            direction="震荡", confidence=0.3,
            target_5d=0, target_10d=0, target_30d=0,
            support=0, resistance=0, risk_level="中",
            signals=["数据不足"], trend="未知", momentum="中",
        )


class RealtimeQuotes:
    """实时行情获取"""

    _cache: dict = {}
    _cache_time: float = 0

    def __init__(self):
        self.provider = AShareDataProvider()

    def get_realtime(self, symbols: List[str]) -> List[dict]:
        """获取实时行情(带超时和缓存)"""
        import time
        try:
            # 优先用 akshare 实时接口(带超时)
            df = self._fetch_spot_em(symbols)
            if not df.empty:
                result = []
                for _, row in df.iterrows():
                    sym = str(row.get("代码", "")).zfill(6)
                    entry = {
                        "symbol": sym,
                        "name": str(row.get("名称", "")),
                        "price": float(row.get("最新价", 0) or 0),
                        "pct_change": float(row.get("涨跌幅", 0) or 0),
                        "volume": float(row.get("成交量", 0) or 0),
                        "amount": float(row.get("成交额", 0) or 0),
                        "high": float(row.get("最高", 0) or 0),
                        "low": float(row.get("最低", 0) or 0),
                        "open": float(row.get("今开", 0) or 0),
                        "turnover": float(row.get("换手率", 0) or 0),
                    }
                    result.append(entry)
                    # 更新缓存
                    RealtimeQuotes._cache[sym] = entry
                RealtimeQuotes._cache_time = time.time()
                return result
        except Exception:
            pass

        # 回退: 从 provider 获取(可能非实时)
        try:
            df = self.provider.get_stock_list()
            if df.empty:
                return self._get_cached(symbols)
            df = df[df["symbol"].isin(symbols)]
            result = []
            for _, row in df.iterrows():
                sym = row.get("symbol", "")
                entry = {
                    "symbol": sym,
                    "name": row.get("name", ""),
                    "price": float(row.get("price", 0) or 0),
                    "pct_change": float(row.get("pct_change", 0) or 0),
                    "volume": float(row.get("volume", 0) or 0),
                    "amount": float(row.get("amount", 0) or 0),
                    "high": float(row.get("high", 0) or 0) if "high" in row else 0,
                    "low": float(row.get("low", 0) or 0) if "low" in row else 0,
                    "open": float(row.get("open", 0) or 0) if "open" in row else 0,
                    "turnover": float(row.get("turnover", 0) or 0) if "turnover" in row else 0,
                }
                result.append(entry)
                RealtimeQuotes._cache[sym] = entry
            RealtimeQuotes._cache_time = time.time()
            return result
        except Exception:
            return self._get_cached(symbols)

    def _fetch_spot_em(self, symbols: List[str]):
        """通过 akshare 获取实时行情(带超时)"""
        import akshare as ak
        import concurrent.futures
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(ak.stock_zh_a_spot_em)
                df = future.result(timeout=10)
            if df.empty:
                return df
            # 过滤目标股票
            df["代码"] = df["代码"].astype(str).str.zfill(6)
            return df[df["代码"].isin(symbols)]
        except Exception:
            return pd.DataFrame()

    def _get_cached(self, symbols: List[str]) -> List[dict]:
        """返回缓存数据"""
        return [RealtimeQuotes._cache[s] for s in symbols if s in RealtimeQuotes._cache]


# 缓存选股结果
_daily_picks_cache = None
_daily_picks_date = None

def get_daily_picks(n: int = 20, force: bool = False) -> List[dict]:
    """获取每日选股结果(带缓存)"""
    global _daily_picks_cache, _daily_picks_date
    today = datetime.now().strftime("%Y-%m-%d")

    if _daily_picks_cache is not None and _daily_picks_date == today and not force:
        return _daily_picks_cache

    selector = DailyStockSelector()
    picks = selector.select_daily(n)

    # 转为dict
    result = []
    for p in picks:
        result.append({
            "symbol": p.symbol,
            "name": p.name,
            "score": p.score,
            "price": p.price,
            "pct_change": p.pct_change,
            "market_cap": round(p.market_cap, 1),
            "pe": round(p.pe, 1),
            "reasons": p.reasons,
            "prediction": p.prediction,
        })

    _daily_picks_cache = result
    _daily_picks_date = today

    # 保存到文件
    cache_path = Path(__file__).parent.parent / "cache" / f"picks_{today}.json"
    cache_path.parent.mkdir(exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return result
