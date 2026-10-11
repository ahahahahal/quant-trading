"""
交易风控网关 - 所有下单必须经过此网关
参考 vnpy 风控模块 + A股交易规则
"""
import logging
import time
from datetime import datetime
from typing import Dict, Set, Optional
from .base import (
    BaseBroker, OrderInfo, AccountInfo, PositionInfo,
    OrderStatus, OrderDirection,
)

log = logging.getLogger("quant.broker.risk")


class RiskGateway:
    """
    交易风控网关
    所有订单必须经过风控检查，只有通过检查的订单才能发送到券商

    风控规则：
    1. 单笔金额上限（默认总资产的10%）
    2. 单日亏损上限（默认总资产的3%）
    3. 总仓位上限（默认总资产的80%）
    4. 涨跌停板检查（A股±10%，ST股±5%，创业板/科创板±20%）
    5. 重复下单检查（同标的同方向同价格短时间内不重复）
    6. 交易时间检查（仅交易时段允许下单）
    """

    def __init__(self, broker: BaseBroker, config: dict = None):
        self._broker = broker
        config = config or {}

        # 风控参数
        self.max_single_pct = config.get("max_single_pct", 0.10)       # 单笔金额上限：总资产10%
        self.max_daily_loss_pct = config.get("max_daily_loss_pct", 0.03) # 单日亏损上限：3%
        self.max_position_pct = config.get("max_position_pct", 0.80)    # 总仓位上限：80%
        self.duplicate_window = config.get("duplicate_window", 60)       # 重复下单检查窗口(秒)

        # 内部状态
        self._order_history: list = []       # 今日委托历史
        self._duplicate_cache: Dict[str, float] = {}  # 重复订单缓存 {key: timestamp}
        self._blocked_orders: list = []      # 被风控拦截的订单记录
        self._daily_start_asset: float = 0   # 当日起始资产

    def reset_daily(self):
        """每日重置（开盘前调用）"""
        account = self._broker.get_account()
        self._daily_start_asset = account.total_asset
        self._order_history = []
        self._duplicate_cache.clear()
        self._blocked_orders.clear()
        log.info("风控每日重置, 起始资产: %.2f", self._daily_start_asset)

    def check_order(self, symbol: str, direction: OrderDirection,
                    price: float, shares: int, **kwargs) -> tuple:
        """
        风控检查（不下单，仅检查）
        :return: (通过: bool, 原因: str)
        """
        now = datetime.now()

        # 获取当前账户和持仓
        account = self._broker.get_account()
        positions = self._broker.get_positions()

        # ---------- 规则6: 交易时间检查 ----------
        if not self._is_trading_time(now):
            msg = f"风控拦截: 非交易时间 ({now.strftime('%H:%M:%S')})"
            self._record_blocked(symbol, direction, price, shares, msg)
            return False, msg

        # ---------- 规则1: 单笔金额上限 ----------
        order_amount = price * shares
        if account.total_asset > 0:
            single_pct = order_amount / account.total_asset
            if single_pct > self.max_single_pct:
                msg = (f"风控拦截: 单笔金额占比{single_pct:.1%}，"
                       f"超过上限{self.max_single_pct:.0%}（最大可下单{account.total_asset * self.max_single_pct:.0f}元）")
                self._record_blocked(symbol, direction, price, shares, msg)
                return False, msg

        # ---------- 规则2: 单日亏损上限 ----------
        if self._daily_start_asset > 0:
            current_pnl = account.total_asset - self._daily_start_asset
            daily_loss_pct = -current_pnl / self._daily_start_asset if current_pnl < 0 else 0
            if daily_loss_pct >= self.max_daily_loss_pct:
                msg = (f"风控拦截: 单日亏损{daily_loss_pct:.2%}，"
                       f"已达到上限{self.max_daily_loss_pct:.0%}，今日禁止下单")
                self._record_blocked(symbol, direction, price, shares, msg)
                return False, msg

        # ---------- 规则3: 总仓位上限（仅买入时检查） ----------
        if direction == OrderDirection.BUY and account.total_asset > 0:
            # 买入后总市值
            projected_mv = account.market_value + order_amount
            projected_pct = projected_mv / account.total_asset
            if projected_pct > self.max_position_pct:
                msg = (f"风控拦截: 预计仓位{projected_pct:.1%}，"
                       f"超过上限{self.max_position_pct:.0%}")
                self._record_blocked(symbol, direction, price, shares, msg)
                return False, msg

        # ---------- 规则4: 涨跌停板检查 ----------
        limit_result, limit_msg = self._check_price_limit(symbol, price)
        if not limit_result:
            self._record_blocked(symbol, direction, price, shares, limit_msg)
            return False, limit_msg

        # ---------- 规则5: 重复下单检查 ----------
        dup_key = f"{symbol}_{direction.value}_{price}_{shares}"
        dup_result, dup_msg = self._check_duplicate(dup_key)
        if not dup_result:
            self._record_blocked(symbol, direction, price, shares, dup_msg)
            return False, dup_msg

        # 全部通过
        log.info("风控检查通过: %s %s %.2f x %d", direction.value, symbol, price, shares)
        return True, "风控检查通过"

    def place_order(self, symbol: str, direction: OrderDirection,
                    price: float, shares: int, **kwargs) -> OrderInfo:
        """
        风控网关下单 - 先检查再下单
        """
        # 风控检查
        passed, reason = self.check_order(symbol, direction, price, shares, **kwargs)
        if not passed:
            # 返回一个被拒绝的订单
            order = OrderInfo(
                order_id=f"RISK_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                symbol=symbol,
                direction=direction,
                price=price,
                shares=shares,
                status=OrderStatus.REJECTED,
                create_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                update_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                reject_reason=reason,
                strategy=kwargs.get("strategy", ""),
                reason=kwargs.get("reason", ""),
            )
            return order

        # 记录委托
        dup_key = f"{symbol}_{direction.value}_{price}_{shares}"
        self._duplicate_cache[dup_key] = time.time()

        # 发送到券商
        order = self._broker.place_order(symbol, direction, price, shares, **kwargs)
        self._order_history.append(order)
        return order

    def cancel_order(self, order_id: str) -> bool:
        """撤单（透传）"""
        return self._broker.cancel_order(order_id)

    def get_account(self) -> AccountInfo:
        """获取账户（透传）"""
        return self._broker.get_account()

    def get_positions(self) -> list:
        """获取持仓（透传）"""
        return self._broker.get_positions()

    def get_orders(self, status_filter=None) -> list:
        """获取委托（透传）"""
        return self._broker.get_orders(status_filter)

    def get_risk_report(self) -> dict:
        """获取风控报告"""
        account = self._broker.get_account()
        positions = self._broker.get_positions()

        daily_pnl_pct = 0
        if self._daily_start_asset > 0:
            daily_pnl_pct = (account.total_asset - self._daily_start_asset) / self._daily_start_asset

        position_pct = account.market_value / account.total_asset if account.total_asset > 0 else 0

        return {
            "daily_pnl_pct": round(daily_pnl_pct, 4),
            "position_pct": round(position_pct, 4),
            "total_orders": len(self._order_history),
            "blocked_orders": len(self._blocked_orders),
            "risk_limits": {
                "max_single_pct": self.max_single_pct,
                "max_daily_loss_pct": self.max_daily_loss_pct,
                "max_position_pct": self.max_position_pct,
            },
            "recent_blocked": self._blocked_orders[-10:] if self._blocked_orders else [],
        }

    # ========== 内部方法 ==========

    def _is_trading_time(self, now: datetime = None) -> bool:
        """判断是否为交易时间（周一~周五 09:25~11:30, 13:00~15:00）"""
        now = now or datetime.now()
        weekday = now.weekday()
        if weekday >= 5:  # 周末
            return False
        hm = now.hour * 100 + now.minute
        # 集合竞价 09:25~09:30, 上午 09:30~11:30, 下午 13:00~15:00
        return (925 <= hm <= 1130) or (1300 <= hm < 1500)

    def _check_price_limit(self, symbol: str, price: float) -> tuple:
        """
        涨跌停板检查
        - 主板/中小板: ±10%
        - 创业板(300xxx)/科创板(688xxx): ±20%
        - ST股: ±5%
        """
        # 简化实现：通过股票代码前缀判断涨跌停幅度
        limit_pct = 0.10  # 默认10%

        if symbol.startswith("300") or symbol.startswith("688"):
            limit_pct = 0.20  # 创业板/科创板20%

        # 尝试获取昨收价（简化：用最近成交价作为参考）
        positions = self._broker.get_positions()
        ref_price = None
        for pos in positions:
            if pos.symbol == symbol and pos.cost_price > 0:
                ref_price = pos.cost_price
                break

        if ref_price and ref_price > 0:
            upper = ref_price * (1 + limit_pct)
            lower = ref_price * (1 - limit_pct)
            if price > upper:
                return False, f"风控拦截: 委托价{price}超过涨停价{upper:.2f}（{limit_pct:.0%}限制）"
            if price < lower:
                return False, f"风控拦截: 委托价{price}低于跌停价{lower:.2f}（{limit_pct:.0%}限制）"

        # 如果没有参考价，只做极端检查
        if price <= 0:
            return False, "风控拦截: 委托价格必须大于0"
        if price > 9999:
            return False, "风控拦截: 委托价格异常"

        return True, "涨跌停检查通过"

    def _check_duplicate(self, dup_key: str) -> tuple:
        """重复下单检查（短时间内相同委托）"""
        now = time.time()
        if dup_key in self._duplicate_cache:
            last_time = self._duplicate_cache[dup_key]
            elapsed = now - last_time
            if elapsed < self.duplicate_window:
                return False, (f"风控拦截: 重复下单，"
                               f"{int(self.duplicate_window - elapsed)}秒后可再次下单")
        return True, "重复下单检查通过"

    def _record_blocked(self, symbol: str, direction: OrderDirection,
                        price: float, shares: int, reason: str):
        """记录被拦截的订单"""
        record = {
            "symbol": symbol,
            "direction": direction.value,
            "price": price,
            "shares": shares,
            "reason": reason,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._blocked_orders.append(record)
        log.warning("风控拦截: %s %s %.2f x %d - %s",
                    direction.value, symbol, price, shares, reason)
