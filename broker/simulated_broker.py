"""
模拟券商 - 用于策略安全测试
提供完整的虚拟撮合引擎：延迟成交、手续费模拟、资金/持仓管理
"""
import logging
import uuid
import time
import random
from datetime import datetime
from typing import List, Dict, Optional
from .base import (
    BaseBroker, OrderInfo, AccountInfo, PositionInfo,
    OrderStatus, OrderDirection,
)

log = logging.getLogger("quant.broker.simulated")


class SimulatedBroker(BaseBroker):
    """
    模拟券商
    - 模拟真实的A股撮合规则
    - 支持限价单撮合（延迟成交）
    - 模拟手续费（佣金+印花税）
    - 适合策略验证和测试
    """

    # 默认配置
    DEFAULT_CONFIG = {
        "initial_capital": 1000000.0,   # 初始资金100万
        "commission_rate": 0.0003,       # 佣金费率万三
        "commission_min": 5.0,           # 最低佣金5元
        "stamp_tax_rate": 0.001,         # 印花税千一（仅卖出）
        "slippage_pct": 0.001,           # 滑点0.1%
    }

    def __init__(self):
        super().__init__(broker_type="simulated")
        self._config: dict = {}
        self._account = AccountInfo()
        self._positions: Dict[str, PositionInfo] = {}
        self._orders: Dict[str, OrderInfo] = {}
        self._initial_capital = 1000000.0
        self._start_day_asset = 1000000.0  # 当日起始资产

    def connect(self, config: dict = None) -> bool:
        """连接模拟券商（初始化账户）"""
        self._config = {**self.DEFAULT_CONFIG, **(config or {})}
        self._initial_capital = self._config["initial_capital"]

        self._account = AccountInfo(
            total_asset=self._initial_capital,
            available_cash=self._initial_capital,
            market_value=0.0,
            frozen_cash=0.0,
            daily_pnl=0.0,
            daily_pnl_pct=0.0,
            commission_total=0.0,
        )
        self._start_day_asset = self._initial_capital
        self._positions = {}
        self._orders = {}
        self.connected = True
        self._last_error = ""

        log.info("模拟券商已连接, 初始资金: %.2f", self._initial_capital)
        return True

    def disconnect(self) -> None:
        """断开连接"""
        self.connected = False
        log.info("模拟券商已断开")

    def get_account(self) -> AccountInfo:
        """获取账户资金"""
        self._update_market_value()
        return self._account

    def get_positions(self) -> List[PositionInfo]:
        """获取持仓列表"""
        self._update_market_value()
        return list(self._positions.values())

    def place_order(self, symbol: str, direction: OrderDirection,
                    price: float, shares: int, **kwargs) -> OrderInfo:
        """
        下单（模拟撮合）
        - 买入：冻结资金，模拟成交
        - 卖出：冻结持仓，模拟成交
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        order_id = f"SIM{datetime.now().strftime('%Y%m%d%H%M%S')}{random.randint(1000,9999)}"

        order = OrderInfo(
            order_id=order_id,
            symbol=symbol,
            direction=direction,
            price=price,
            shares=shares,
            status=OrderStatus.PENDING,
            create_time=now,
            update_time=now,
            strategy=kwargs.get("strategy", ""),
            reason=kwargs.get("reason", ""),
        )

        # 买入校验
        if direction == OrderDirection.BUY:
            amount = price * shares
            commission = self._calc_commission(amount, is_sell=False)
            total_cost = amount + commission
            if total_cost > self._account.available_cash:
                order.status = OrderStatus.REJECTED
                order.reject_reason = f"可用资金不足: 需要{total_cost:.2f}, 可用{self._account.available_cash:.2f}"
                self._orders[order_id] = order
                log.warning("买入被拒: %s", order.reject_reason)
                return order

            # 冻结资金
            self._account.available_cash -= total_cost
            self._account.frozen_cash += total_cost

        # 卖出校验
        elif direction == OrderDirection.SELL:
            pos = self._positions.get(symbol)
            if not pos or pos.available_shares < shares:
                avail = pos.available_shares if pos else 0
                order.status = OrderStatus.REJECTED
                order.reject_reason = f"可卖数量不足: 需要{shares}股, 可用{avail}股"
                self._orders[order_id] = order
                log.warning("卖出被拒: %s", order.reject_reason)
                return order

            # 冻结持仓
            pos.available_shares -= shares

        # 模拟成交（立即撮合，加随机延迟）
        self._orders[order_id] = order
        self._simulate_fill(order)

        log.info("委托已提交: %s %s %s 价格%.2f 数量%d 状态:%s",
                 order_id, direction.value, symbol, price, shares, order.status.value)
        return order

    def cancel_order(self, order_id: str) -> bool:
        """撤单"""
        order = self._orders.get(order_id)
        if not order:
            log.warning("撤单失败: 订单 %s 不存在", order_id)
            return False

        if order.status not in (OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.PARTIAL):
            log.warning("撤单失败: 订单 %s 状态为 %s", order_id, order.status.value)
            return False

        # 解冻资金/持仓
        if order.direction == OrderDirection.BUY:
            unfilled = order.shares - order.filled_shares
            unfrozen = order.price * unfilled
            self._account.frozen_cash -= unfrozen
            self._account.available_cash += unfrozen
        elif order.direction == OrderDirection.SELL:
            unfilled = order.shares - order.filled_shares
            if order.symbol in self._positions:
                self._positions[order.symbol].available_shares += unfilled

        order.status = OrderStatus.CANCELLED
        order.update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log.info("订单 %s 已撤单", order_id)
        return True

    def get_orders(self, status_filter: Optional[OrderStatus] = None) -> List[OrderInfo]:
        """获取委托列表"""
        orders = list(self._orders.values())
        if status_filter:
            orders = [o for o in orders if o.status == status_filter]
        # 按时间倒序
        orders.sort(key=lambda o: o.create_time, reverse=True)
        return orders

    def get_quote_price(self, symbol: str) -> float:
        """
        获取股票当前价（模拟行情）
        实际项目中应对接行情接口，这里用委托价模拟波动
        """
        # 简单模拟：随机小幅波动
        base = 20.0  # 默认基准价
        for order in reversed(list(self._orders.values())):
            if order.symbol == symbol and order.filled_price > 0:
                base = order.filled_price
                break
        # 模拟 ±2% 波动
        return base * (1 + random.uniform(-0.02, 0.02))

    def _simulate_fill(self, order: OrderInfo):
        """
        模拟撮合引擎
        - 限价单：买入价>=卖一 或 卖出价<=买一 时成交
        - 加入随机滑点模拟
        """
        slippage = self._config.get("slippage_pct", 0.001)

        # 模拟成交（90%概率立即成交）
        if random.random() < 0.9:
            # 模拟成交价（带滑点）
            if order.direction == OrderDirection.BUY:
                fill_price = order.price * (1 + random.uniform(0, slippage))
            else:
                fill_price = order.price * (1 - random.uniform(0, slippage))

            fill_price = round(fill_price, 2)
            order.filled_shares = order.shares
            order.filled_price = fill_price

            # 计算手续费
            amount = fill_price * order.shares
            is_sell = (order.direction == OrderDirection.SELL)
            commission = self._calc_commission(amount, is_sell=is_sell)
            order.commission = commission

            # 更新账户
            if order.direction == OrderDirection.BUY:
                cost = amount + commission
                # 解冻 -> 扣除
                frozen = order.price * order.shares
                self._account.frozen_cash -= frozen
                self._account.available_cash += (frozen - cost)
                # 更新持仓
                self._update_position_buy(order.symbol, order.shares, fill_price, commission)
            else:
                # 解冻 -> 收入
                income = amount - commission
                self._account.available_cash += income
                # 更新持仓
                self._update_position_sell(order.symbol, order.shares, fill_price, commission)

            # 更新手续费总计
            self._account.commission_total += commission
            order.status = OrderStatus.FILLED
            order.update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            log.info("撮合成交: %s %s %s 价格%.2f 数量%d 手续费%.2f",
                     order.order_id, order.direction.value, order.symbol,
                     fill_price, order.shares, commission)
        else:
            # 未成交，保持PENDING
            order.status = OrderStatus.SUBMITTED
            order.update_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _calc_commission(self, amount: float, is_sell: bool = False) -> float:
        """计算手续费（佣金+印花税）"""
        commission_rate = self._config.get("commission_rate", 0.0003)
        commission_min = self._config.get("commission_min", 5.0)
        stamp_tax_rate = self._config.get("stamp_tax_rate", 0.001)

        # 佣金（双向收取，有最低限制）
        commission = max(amount * commission_rate, commission_min)
        # 印花税（仅卖出收取）
        if is_sell:
            commission += amount * stamp_tax_rate

        return round(commission, 2)

    def _update_position_buy(self, symbol: str, shares: int, price: float, commission: float):
        """更新买入后持仓"""
        if symbol in self._positions:
            pos = self._positions[symbol]
            total_cost = pos.cost_price * pos.shares + price * shares + commission
            pos.shares += shares
            pos.available_shares += shares
            pos.cost_price = total_cost / pos.shares
        else:
            self._positions[symbol] = PositionInfo(
                symbol=symbol,
                shares=shares,
                available_shares=shares,
                cost_price=price + commission / shares,
                current_price=price,
                market_value=price * shares,
                pnl=-commission,
                pnl_pct=-commission / (price * shares) if price * shares > 0 else 0,
            )
        self._update_market_value()

    def _update_position_sell(self, symbol: str, shares: int, price: float, commission: float):
        """更新卖出后持仓"""
        if symbol not in self._positions:
            return
        pos = self._positions[symbol]
        pos.shares -= shares
        if pos.shares <= 0:
            del self._positions[symbol]
        else:
            pos.current_price = price
            pos.market_value = pos.shares * price
            pos.pnl = (price - pos.cost_price) * pos.shares
            pos.pnl_pct = pos.pnl / (pos.cost_price * pos.shares) if pos.shares > 0 else 0
        self._update_market_value()

    def _update_market_value(self):
        """更新持仓市值和总资产"""
        total_mv = 0.0
        for pos in self._positions.values():
            # 用模拟行情或最后成交价更新
            if pos.current_price <= 0:
                pos.current_price = pos.cost_price  # 无行情用成本价
            pos.market_value = pos.current_price * pos.shares
            pos.pnl = (pos.current_price - pos.cost_price) * pos.shares
            pos.pnl_pct = pos.pnl / (pos.cost_price * pos.shares) if pos.cost_price * pos.shares > 0 else 0
            total_mv += pos.market_value

        self._account.market_value = total_mv
        self._account.total_asset = self._account.available_cash + self._account.frozen_cash + total_mv
        # 当日盈亏 = 总资产 - 当日起始资产
        self._account.daily_pnl = self._account.total_asset - self._start_day_asset
        self._account.daily_pnl_pct = (
            self._account.daily_pnl / self._start_day_asset if self._start_day_asset > 0 else 0
        )
