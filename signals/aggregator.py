"""
信号聚合器 - 多策略信号融合
参考 QuantConnect 的 Alpha Streams 设计
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import CONFIG
from strategy.engine import TradeSignal, Signal, BaseStrategy, STRATEGY_REGISTRY, create_strategy


class SignalAggregator:
    """
    多策略信号聚合器

    将多个策略的信号加权融合, 生成最终交易决策
    支持:
    - 等权投票
    - 加权评分
    - 信号冲突解决
    - 去重与去噪
    """

    def __init__(self, weights: Dict[str, float] = None, min_score: float = None):
        self.weights = weights or CONFIG.signal.signal_weights
        self.min_score = min_score or CONFIG.signal.min_score
        self.max_signals = CONFIG.signal.max_signals_per_day

    def aggregate(self, df: pd.DataFrame,
                  strategy_names: List[str] = None) -> List[TradeSignal]:
        """
        运行多策略并聚合信号

        Args:
            df: 带因子数据
            strategy_names: 策略列表, None则运行全部

        Returns:
            聚合后的交易信号列表
        """
        if strategy_names is None:
            strategy_names = list(STRATEGY_REGISTRY.keys())

        # 收集所有策略信号
        all_signals: Dict[str, Dict[str, List[TradeSignal]]] = {}
        for name in strategy_names:
            strategy = create_strategy(name)
            signals = strategy.run(df)
            for sig in signals:
                date_key = sig.date[:10]
                all_signals.setdefault(date_key, {}).setdefault(name, []).append(sig)

        # 按日聚合
        aggregated = []
        for date_key in sorted(all_signals.keys()):
            day_signals = all_signals[date_key]
            merged = self._merge_day_signals(day_signals, date_key)
            aggregated.extend(merged)

        return aggregated

    def _merge_day_signals(self, day_signals: Dict[str, List[TradeSignal]],
                           date_key: str) -> List[TradeSignal]:
        """合并单日多策略信号"""
        # 按股票分组
        symbol_scores: Dict[str, dict] = {}

        for strategy_name, signals in day_signals.items():
            weight = self.weights.get(strategy_name, 0.1)
            for sig in signals:
                sym = sig.symbol
                if sym not in symbol_scores:
                    symbol_scores[sym] = {
                        "buy_score": 0, "sell_score": 0,
                        "buy_reasons": [], "sell_reasons": [],
                        "prices": [],
                    }
                entry = symbol_scores[sym]
                entry["prices"].append(sig.price)

                if sig.signal == Signal.BUY:
                    entry["buy_score"] += sig.score * weight
                elif sig.signal == Signal.SELL:
                    entry["sell_score"] += sig.score * weight

                reason = f"{strategy_name}({sig.score:.2f})"
                if sig.signal == Signal.BUY:
                    entry["buy_reasons"].append(reason)
                else:
                    entry["sell_reasons"].append(reason)

        # 生成最终信号
        result = []
        for sym, data in symbol_scores.items():
            avg_price = np.mean(data["prices"])
            net_score = data["buy_score"] - data["sell_score"]

            if data["buy_score"] > self.min_score and net_score > 0:
                result.append(TradeSignal(
                    symbol=sym, signal=Signal.BUY,
                    price=avg_price, date=date_key,
                    strategy="aggregated",
                    score=min(data["buy_score"], 1.0),
                    reason="BUY: " + ", ".join(data["buy_reasons"]),
                    target_pct=min(data["buy_score"] * 0.3, 0.25),
                ))
            elif data["sell_score"] > self.min_score and net_score < 0:
                result.append(TradeSignal(
                    symbol=sym, signal=Signal.SELL,
                    price=avg_price, date=date_key,
                    strategy="aggregated",
                    score=min(data["sell_score"], 1.0),
                    reason="SELL: " + ", ".join(data["sell_reasons"]),
                ))

        # 按分数排序, 限制数量
        result.sort(key=lambda s: s.score, reverse=True)
        return result[:self.max_signals]

    def scan_market(self, provider, symbols: list = None, strategy_names: list = None, days: int = 120) -> list:
        """
        扫描全市场或指定股票列表的交易信号

        Args:
            provider: AShareDataProvider 实例
            symbols: 股票代码列表，None则使用本地数据库中的热门股
            strategy_names: 策略列表，None则运行全部
            days: 获取最近N天数据用于计算

        Returns:
            聚合后的交易信号列表
        """
        from data.local_db import get_local_db
        from datetime import datetime, timedelta

        local_db = get_local_db()

        # 默认扫描本地数据库中的股票
        if symbols is None:
            stock_list_df = local_db.get_stock_list()
            if stock_list_df.empty:
                return []
            symbols = stock_list_df['symbol'].tolist()[:50]  # 限制50只避免太慢

        if not symbols:
            return []

        all_signals = []
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        for sym in symbols:
            try:
                df = local_db.get_daily(sym, start_date=start_date, end_date=end_date)
                if df.empty or len(df) < 30:
                    continue
                # 尝试添加因子
                try:
                    from data.alpha158 import Alpha158
                    alpha = Alpha158()
                    df = alpha.compute_features(df)
                except Exception:
                    pass
                signals = self.aggregate(df, strategy_names)
                # 只保留最后一天的信号
                if signals:
                    latest_date = max(s.date for s in signals)
                    day_signals = [s for s in signals if s.date == latest_date]
                    # 补充名称
                    for s in day_signals:
                        if not hasattr(s, 'name') or not s.name:
                            s.name = sym
                    all_signals.extend(day_signals)
            except Exception as e:
                continue

        # 按分数排序
        all_signals.sort(key=lambda s: s.score, reverse=True)
        return all_signals[:self.max_signals]

    def get_signal_summary(self, signals: List[TradeSignal]) -> pd.DataFrame:
        """信号汇总表"""
        rows = []
        for sig in signals:
            rows.append(sig.to_dict())
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        return df[["date", "symbol", "signal", "score", "price", "strategy", "reason"]]
