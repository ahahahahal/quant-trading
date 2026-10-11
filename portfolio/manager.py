"""
组合管理器 - 持仓跟踪与调仓
"""
import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Dict, List
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import CONFIG


@dataclass
class PortfolioSnapshot:
    """组合快照"""
    date: str
    cash: float
    positions: Dict[str, dict]   # symbol -> {shares, cost, price, value, pnl}
    total_value: float
    total_pnl: float
    total_return: float


class PortfolioManager:
    """组合管理器"""

    def __init__(self, initial_capital: float = None):
        self.initial_capital = initial_capital or CONFIG.backtest.initial_capital
        self.cash = self.initial_capital
        self.positions: Dict[str, dict] = {}
        self.history: List[PortfolioSnapshot] = []
        self.rebalance_log: List[dict] = []

    def update_prices(self, prices: Dict[str, float]):
        """更新持仓市价"""
        for sym, pos in self.positions.items():
            if sym in prices:
                pos["price"] = prices[sym]
                pos["value"] = pos["shares"] * prices[sym]
                pos["pnl"] = (prices[sym] - pos["cost"]) * pos["shares"]
                pos["pnl_pct"] = (prices[sym] - pos["cost"]) / pos["cost"] if pos["cost"] > 0 else 0

    def get_allocation(self) -> pd.DataFrame:
        """获取持仓配置"""
        if not self.positions:
            return pd.DataFrame()
        total = self.cash + sum(p["value"] for p in self.positions.values())
        rows = []
        for sym, pos in self.positions.items():
            rows.append({
                "symbol": sym,
                "shares": pos["shares"],
                "cost": pos["cost"],
                "price": pos["price"],
                "value": pos["value"],
                "pnl": pos["pnl"],
                "pnl_pct": f"{pos.get('pnl_pct', 0)*100:.2f}%",
                "weight": f"{pos['value']/total*100:.1f}%",
            })
        rows.append({
            "symbol": "CASH",
            "shares": "-", "cost": "-", "price": "-",
            "value": self.cash, "pnl": "-", "pnl_pct": "-",
            "weight": f"{self.cash/total*100:.1f}%",
        })
        return pd.DataFrame(rows)

    def get_sector_exposure(self, sector_map: Dict[str, str] = None) -> pd.DataFrame:
        """获取行业暴露(需要行业映射表)"""
        if not sector_map:
            return pd.DataFrame()
        total = sum(p["value"] for p in self.positions.values())
        sector_values = {}
        for sym, pos in self.positions.items():
            sector = sector_map.get(sym, "其他")
            sector_values[sector] = sector_values.get(sector, 0) + pos["value"]
        rows = [{"sector": s, "value": v, "weight": f"{v/total*100:.1f}%"}
                for s, v in sorted(sector_values.items(), key=lambda x: -x[1])]
        return pd.DataFrame(rows)

    def snapshot(self, date: str) -> PortfolioSnapshot:
        """记录组合快照"""
        total = self.cash + sum(p["value"] for p in self.positions.values())
        pnl = total - self.initial_capital
        ret = pnl / self.initial_capital
        snap = PortfolioSnapshot(
            date=date, cash=self.cash,
            positions=dict(self.positions),
            total_value=total, total_pnl=pnl, total_return=ret,
        )
        self.history.append(snap)
        return snap
