"""
券商接口基类 - 定义标准化的交易接口
参考 vnpy 的 Gateway 设计，统一券商调用协议
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from enum import Enum
from datetime import datetime

log = logging.getLogger("quant.broker")


class OrderStatus(Enum):
    """委托状态"""
    PENDING = "pending"         # 待报
    SUBMITTED = "submitted"     # 已报
    PARTIAL = "partial"         # 部分成交
    FILLED = "filled"           # 全部成交
    CANCELLED = "cancelled"     # 已撤单
    REJECTED = "rejected"       # 拒绝
    FAILED = "failed"           # 失败


class OrderDirection(Enum):
    """委托方向"""
    BUY = "BUY"       # 买入
    SELL = "SELL"      # 卖出


@dataclass
class OrderInfo:
    """委托信息"""
    order_id: str               # 委托编号
    symbol: str                 # 股票代码
    direction: OrderDirection   # 买入/卖出
    price: float                # 委托价格
    shares: int                 # 委托数量
    status: OrderStatus = OrderStatus.PENDING
    filled_shares: int = 0      # 已成交数量
    filled_price: float = 0.0   # 成交均价
    commission: float = 0.0     # 手续费
    create_time: str = ""       # 委托时间
    update_time: str = ""       # 更新时间
    strategy: str = ""          # 来源策略
    reason: str = ""            # 委托原因
    reject_reason: str = ""     # 拒绝原因

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "direction": self.direction.value if isinstance(self.direction, OrderDirection) else self.direction,
            "price": self.price,
            "shares": self.shares,
            "status": self.status.value if isinstance(self.status, OrderStatus) else self.status,
            "filled_shares": self.filled_shares,
            "filled_price": round(self.filled_price, 4),
            "commission": round(self.commission, 2),
            "amount": round(self.filled_price * self.filled_shares, 2) if self.filled_shares > 0 else 0,
            "create_time": self.create_time,
            "update_time": self.update_time,
            "strategy": self.strategy,
            "reason": self.reason,
            "reject_reason": self.reject_reason,
        }


@dataclass
class AccountInfo:
    """账户资金信息"""
    total_asset: float = 0.0       # 总资产
    available_cash: float = 0.0    # 可用资金
    market_value: float = 0.0      # 持仓市值
    frozen_cash: float = 0.0       # 冻结资金
    daily_pnl: float = 0.0         # 当日盈亏
    daily_pnl_pct: float = 0.0     # 当日收益率
    commission_total: float = 0.0  # 累计手续费

    def to_dict(self) -> dict:
        return {
            "total_asset": round(self.total_asset, 2),
            "available_cash": round(self.available_cash, 2),
            "market_value": round(self.market_value, 2),
            "frozen_cash": round(self.frozen_cash, 2),
            "daily_pnl": round(self.daily_pnl, 2),
            "daily_pnl_pct": round(self.daily_pnl_pct, 4),
            "commission_total": round(self.commission_total, 2),
        }


@dataclass
class PositionInfo:
    """持仓信息"""
    symbol: str                  # 股票代码
    name: str = ""               # 股票名称
    shares: int = 0              # 持仓数量
    available_shares: int = 0    # 可卖数量
    cost_price: float = 0.0      # 成本价
    current_price: float = 0.0   # 当前价
    market_value: float = 0.0    # 持仓市值
    pnl: float = 0.0            # 盈亏金额
    pnl_pct: float = 0.0        # 盈亏比例

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "shares": self.shares,
            "available_shares": self.available_shares,
            "cost_price": round(self.cost_price, 4),
            "current_price": round(self.current_price, 4),
            "market_value": round(self.market_value, 2),
            "pnl": round(self.pnl, 2),
            "pnl_pct": round(self.pnl_pct, 4),
        }


class BaseBroker(ABC):
    """
    券商抽象基类
    所有券商实现必须继承此类，提供统一的交易接口
    """

    def __init__(self, broker_type: str = "base"):
        self.broker_type = broker_type
        self.connected = False
        self._last_error = ""

    @abstractmethod
    def connect(self, config: dict = None) -> bool:
        """
        连接券商
        :param config: 连接配置（如账户路径、密码等）
        :return: 是否连接成功
        """
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """断开连接"""
        pass

    @abstractmethod
    def get_account(self) -> AccountInfo:
        """获取账户资金信息"""
        pass

    @abstractmethod
    def get_positions(self) -> List[PositionInfo]:
        """获取持仓列表"""
        pass

    @abstractmethod
    def place_order(self, symbol: str, direction: OrderDirection,
                    price: float, shares: int, **kwargs) -> OrderInfo:
        """
        下单委托
        :param symbol: 股票代码
        :param direction: 买入/卖出
        :param price: 委托价格
        :param shares: 委托数量
        :return: OrderInfo 委托信息
        """
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """
        撤单
        :param order_id: 委托编号
        :return: 是否撤单成功
        """
        pass

    @abstractmethod
    def get_orders(self, status_filter: Optional[OrderStatus] = None) -> List[OrderInfo]:
        """
        获取委托列表
        :param status_filter: 可选，按状态筛选
        :return: 委托列表
        """
        pass

    def get_status(self) -> dict:
        """获取连接状态"""
        return {
            "broker_type": self.broker_type,
            "connected": self.connected,
            "last_error": self._last_error,
        }
