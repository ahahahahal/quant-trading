"""
EasyTrader 真实券商接口
基于 easytrader 库对接同花顺/通达信客户端
注意：仅在明确切换到实盘时使用，默认使用 SimulatedBroker
"""
import logging
import time
from typing import List, Optional
from .base import (
    BaseBroker, OrderInfo, AccountInfo, PositionInfo,
    OrderStatus, OrderDirection,
)

log = logging.getLogger("quant.broker.easytrader")

# 尝试导入 easytrader，失败则标记不可用
try:
    import easytrader
    EASYTRADER_AVAILABLE = True
except ImportError:
    EASYTRADER_AVAILABLE = False
    log.warning("easytrader 未安装，真实券商功能不可用。安装命令: pip install easytrader")


class EasyTraderBroker(BaseBroker):
    """
    基于 easytrader 的真实券商接口
    - 支持同花顺（ths）和通达信（tongdaxin）客户端
    - 需要客户端已登录
    - 包含连接状态检测和自动重连
    """

    MAX_RETRY = 3          # 最大重试次数
    RETRY_INTERVAL = 2     # 重试间隔(秒)

    def __init__(self):
        super().__init__(broker_type="easytrader")
        self._user = None
        self._client_type = "ths"  # 默认同花顺
        self._account_path = ""
        self._order_counter = 0

    def connect(self, config: dict = None) -> bool:
        """
        连接券商客户端
        :param config: {
            "client_type": "ths" | "tongdaxin",
            "account_path": "券商账户配置文件路径"
        }
        """
        if not EASYTRADER_AVAILABLE:
            self.connected = False
            self._last_error = "easytrader 未安装，请先执行: pip install easytrader"
            log.error(self._last_error)
            return False

        config = config or {}
        self._client_type = config.get("client_type", "ths")
        self._account_path = config.get("account_path", "")

        for attempt in range(self.MAX_RETRY):
            try:
                self._user = easytrader.use(self._client_type)

                if self._account_path:
                    self._user.connect(self._account_path)
                else:
                    # 无路径则尝试自动连接（需客户端已登录）
                    self._user.connect()

                # 验证连接：尝试查询账户
                test_account = self._user.balance
                if test_account:
                    self.connected = True
                    self._last_error = ""
                    log.info("EasyTrader 连接成功: 客户端=%s", self._client_type)
                    return True

            except Exception as e:
                self._last_error = f"连接失败 (第{attempt+1}次): {str(e)}"
                log.warning(self._last_error)
                if attempt < self.MAX_RETRY - 1:
                    time.sleep(self.RETRY_INTERVAL)

        self.connected = False
        log.error("EasyTrader 连接失败，已重试 %d 次", self.MAX_RETRY)
        return False

    def disconnect(self) -> None:
        """断开连接"""
        self._user = None
        self.connected = False
        log.info("EasyTrader 已断开")

    def _ensure_connected(self) -> bool:
        """确保连接有效，断线则尝试重连"""
        if not self.connected or not self._user:
            return self.connect({"client_type": self._client_type, "account_path": self._account_path})
        try:
            # 发送心跳检测
            self._user.balance
            return True
        except Exception as e:
            log.warning("连接检测失败，尝试重连: %s", e)
            return self.connect({"client_type": self._client_type, "account_path": self._account_path})

    def get_account(self) -> AccountInfo:
        """获取账户资金"""
        self._ensure_connected()
        try:
            balance = self._user.balance
            return AccountInfo(
                total_asset=balance.get("总资产", 0.0),
                available_cash=balance.get("可用余额", 0.0),
                market_value=balance.get("股票市值", 0.0),
                frozen_cash=balance.get("冻结金额", 0.0),
                daily_pnl=balance.get("今日盈亏", 0.0),
                daily_pnl_pct=0.0,  # easytrader 不一定提供
                commission_total=balance.get("手续费", 0.0),
            )
        except Exception as e:
            log.error("获取账户信息失败: %s", e)
            self._last_error = str(e)
            return AccountInfo()

    def get_positions(self) -> List[PositionInfo]:
        """获取持仓"""
        self._ensure_connected()
        try:
            positions_data = self._user.position
            positions = []
            for p in positions_data:
                shares = int(p.get("证券数量", 0))
                available = int(p.get("可用余额", 0))
                cost_price = float(p.get("成本价", 0))
                current_price = float(p.get("市价", 0))
                market_value = float(p.get("市值", 0))

                pnl = (current_price - cost_price) * shares if shares > 0 else 0
                pnl_pct = pnl / (cost_price * shares) if cost_price * shares > 0 else 0

                positions.append(PositionInfo(
                    symbol=p.get("证券代码", ""),
                    name=p.get("证券名称", ""),
                    shares=shares,
                    available_shares=available,
                    cost_price=cost_price,
                    current_price=current_price,
                    market_value=market_value,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                ))
            return positions
        except Exception as e:
            log.error("获取持仓失败: %s", e)
            self._last_error = str(e)
            return []

    def place_order(self, symbol: str, direction: OrderDirection,
                    price: float, shares: int, **kwargs) -> OrderInfo:
        """
        下单
        :param symbol: 股票代码（6位数字）
        :param direction: 买入/卖出
        :param price: 委托价格
        :param shares: 委托数量
        """
        self._ensure_connected()
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        self._order_counter += 1
        order_id = f"ET{time.strftime('%Y%m%d%H%M%S')}{self._order_counter:04d}"

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

        try:
            if direction == OrderDirection.BUY:
                result = self._user.buy(symbol, price=price, amount=shares)
            else:
                result = self._user.sell(symbol, price=price, amount=shares)

            order.status = OrderStatus.SUBMITTED
            order.update_time = time.strftime("%Y-%m-%d %H:%M:%S")
            log.info("EasyTrader 委托已提交: %s %s %s %.2f x %d",
                     order_id, direction.value, symbol, price, shares)

        except Exception as e:
            order.status = OrderStatus.FAILED
            order.reject_reason = str(e)
            order.update_time = time.strftime("%Y-%m-%d %H:%M:%S")
            self._last_error = str(e)
            log.error("EasyTrader 下单失败: %s", e)

        return order

    def cancel_order(self, order_id: str) -> bool:
        """撤单（通过委托编号）"""
        self._ensure_connected()
        try:
            # easytrader 通常用 cancel_entrust(entrust_no)
            self._user.cancel_entrust(order_id)
            log.info("EasyTrader 撤单成功: %s", order_id)
            return True
        except Exception as e:
            log.error("EasyTrader 撤单失败: %s", e)
            self._last_error = str(e)
            return False

    def get_orders(self, status_filter: Optional[OrderStatus] = None) -> List[OrderInfo]:
        """获取委托列表"""
        self._ensure_connected()
        try:
            entrusts = self._user.entrust
            orders = []
            for e in entrusts:
                # easytrader 返回的状态映射
                status_str = e.get("委托状态", "")
                status_map = {
                    "已报": OrderStatus.SUBMITTED,
                    "部成": OrderStatus.PARTIAL,
                    "已成": OrderStatus.FILLED,
                    "已撤": OrderStatus.CANCELLED,
                    "废单": OrderStatus.REJECTED,
                }
                status = status_map.get(status_str, OrderStatus.PENDING)

                if status_filter and status != status_filter:
                    continue

                direction = OrderDirection.BUY if "买" in str(e.get("买卖标志", "")) else OrderDirection.SELL

                orders.append(OrderInfo(
                    order_id=str(e.get("委托编号", "")),
                    symbol=str(e.get("证券代码", "")),
                    direction=direction,
                    price=float(e.get("委托价格", 0)),
                    shares=int(e.get("委托数量", 0)),
                    status=status,
                    filled_shares=int(e.get("成交数量", 0)),
                    filled_price=float(e.get("成交价格", 0)),
                    create_time=str(e.get("委托时间", "")),
                    update_time=str(e.get("委托时间", "")),
                ))
            return orders
        except Exception as e:
            log.error("获取委托列表失败: %s", e)
            self._last_error = str(e)
            return []
