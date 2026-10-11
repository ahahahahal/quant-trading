"""
券商交易接口模块
- BaseBroker: 券商抽象基类
- SimulatedBroker: 模拟券商（默认，安全测试用）
- EasyTraderBroker: 真实券商（需安装easytrader）
- RiskGateway: 交易风控网关（所有订单必须经过）
"""

from .base import BaseBroker, OrderInfo, AccountInfo, PositionInfo, OrderStatus, OrderDirection
from .simulated_broker import SimulatedBroker
from .risk_gateway import RiskGateway

# EasyTraderBroker 延迟导入（依赖可能未安装）
def get_easytrader_broker():
    """获取真实券商实例（需安装easytrader）"""
    from .easytrader_broker import EasyTraderBroker
    return EasyTraderBroker

__all__ = [
    "BaseBroker", "OrderInfo", "AccountInfo", "PositionInfo",
    "OrderStatus", "OrderDirection",
    "SimulatedBroker", "RiskGateway",
    "get_easytrader_broker",
]
