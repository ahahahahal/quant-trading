"""
模拟交易模块
- PaperTrader: 模拟交易引擎，基于实时行情执行策略信号
- 持仓管理、交易日志、账户快照、SQLite持久化
- 风控集成：止损止盈
"""
from trading.paper_trader import PaperTrader

__all__ = ["PaperTrader"]
