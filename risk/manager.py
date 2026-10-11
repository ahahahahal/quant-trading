"""
风险管理模块
参考 Quantopian/Riskfolio-Lib 的风控设计
"""
import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Dict, List, Optional
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import CONFIG


@dataclass
class RiskMetrics:
    """风险指标"""
    var_95: float          # 95% VaR
    var_99: float          # 99% VaR
    cvar_95: float         # 条件VaR
    max_drawdown: float    # 最大回撤
    sharpe: float          # 夏普比率
    sortino: float         # 索提诺比率
    calmar: float          # 卡尔玛比率
    volatility: float      # 年化波动率
    downside_vol: float    # 下行波动率
    beta: float            # Beta系数
    alpha: float           # Alpha收益
    information_ratio: float


class RiskManager:
    """风险管理器"""

    def __init__(self, config=None):
        self.config = config or CONFIG.risk

    def calculate_position_size(self, capital: float, price: float,
                                volatility: float, method: str = "equal_weight") -> int:
        """
        计算仓位大小

        方法:
        - equal_weight: 等权分配
        - volatility: 波动率倒数加权
        - kelly: 凯利公式
        - atr: ATR仓位管理
        """
        if method == "equal_weight":
            amount = capital * self.config.max_position_pct
        elif method == "volatility":
            # 波动率越大, 仓位越小
            target_vol = 0.2  # 目标年化波动率20%
            vol_scalar = target_vol / max(volatility, 0.01)
            amount = capital * min(vol_scalar * 0.1, self.config.max_position_pct)
        elif method == "kelly":
            # 简化凯利公式 f = p - q/b
            win_rate = 0.55  # 默认胜率
            avg_win = 0.05
            avg_loss = 0.03
            kelly = win_rate - (1 - win_rate) / (avg_win / avg_loss)
            kelly = max(min(kelly, 0.25), 0)  # 限制在0-25%
            amount = capital * kelly
        elif method == "atr":
            atr = volatility * price / np.sqrt(252)
            risk_per_trade = capital * 0.01  # 每笔风险1%
            shares = risk_per_trade / (2 * atr)
            amount = shares * price
        else:
            amount = capital * 0.2

        shares = int(amount / price / 100) * 100
        return max(shares, 0)

    def calculate_risk_metrics(self, returns: pd.Series,
                                benchmark_returns: pd.Series = None,
                                rf_rate: float = 0.03) -> RiskMetrics:
        """计算完整风险指标"""
        rf_daily = rf_rate / 252

        # 基础指标
        mean_ret = returns.mean() * 252
        vol = returns.std() * np.sqrt(252)

        # VaR
        var_95 = np.percentile(returns, 5)
        var_99 = np.percentile(returns, 1)
        cvar_95 = returns[returns <= var_95].mean()

        # 最大回撤
        cumulative = (1 + returns).cumprod()
        peak = cumulative.cummax()
        drawdown = (peak - cumulative) / peak
        max_dd = drawdown.max()

        # 夏普
        excess = returns - rf_daily
        sharpe = excess.mean() / (excess.std() + 1e-10) * np.sqrt(252)

        # 索提诺
        downside = returns[returns < 0]
        downside_vol = downside.std() * np.sqrt(252) if len(downside) > 0 else 0
        sortino = (mean_ret - rf_rate) / (downside_vol + 1e-10)

        # 卡尔玛
        calmar = mean_ret / (max_dd + 1e-10)

        # Beta / Alpha / IR
        beta, alpha, ir = 0, 0, 0
        if benchmark_returns is not None and len(benchmark_returns) > 0:
            aligned = pd.concat([returns, benchmark_returns], axis=1).dropna()
            if len(aligned) > 1:
                aligned.columns = ["ret", "bench"]
                cov = aligned["ret"].cov(aligned["bench"])
                var_bench = aligned["bench"].var()
                beta = cov / (var_bench + 1e-10)
                alpha = mean_ret - beta * (benchmark_returns.mean() * 252)
                tracking_error = (aligned["ret"] - aligned["bench"]).std() * np.sqrt(252)
                ir = (mean_ret - benchmark_returns.mean() * 252) / (tracking_error + 1e-10)

        return RiskMetrics(
            var_95=var_95, var_99=var_99, cvar_95=cvar_95,
            max_drawdown=max_dd, sharpe=sharpe, sortino=sortino,
            calmar=calmar, volatility=vol, downside_vol=downside_vol,
            beta=beta, alpha=alpha, information_ratio=ir,
        )

    def check_portfolio_risk(self, positions: Dict, total_equity: float) -> List[str]:
        """组合风险检查"""
        warnings = []

        # 单只仓位检查
        for symbol, pos in positions.items():
            pct = pos.market_value / total_equity
            if pct > self.config.max_position_pct:
                warnings.append(f"⚠️ {symbol} 仓位 {pct*100:.1f}% 超过上限 {self.config.max_position_pct*100:.0f}%")

        # 总仓位检查
        total_pos = sum(p.market_value for p in positions.values())
        total_pct = total_pos / total_equity
        if total_pct > self.config.max_total_position_pct:
            warnings.append(f"⚠️ 总仓位 {total_pct*100:.1f}% 超过上限 {self.config.max_total_position_pct*100:.0f}%")

        # 行业集中度检查(需要行业数据)
        # ...

        return warnings

    def generate_risk_report(self, returns: pd.Series,
                              benchmark_returns: pd.Series = None) -> str:
        """生成风控报告"""
        metrics = self.calculate_risk_metrics(returns, benchmark_returns)

        report = f"""
╔══════════════════════════════════════╗
║         风 险 分 析 报 告            ║
╚══════════════════════════════════════╝

📊 收益风险指标
  年化波动率:   {metrics.volatility*100:.2f}%
  下行波动率:   {metrics.downside_vol*100:.2f}%
  夏普比率:     {metrics.sharpe:.3f}
  索提诺比率:   {metrics.sortino:.3f}
  卡尔玛比率:   {metrics.calmar:.3f}

📉 风险指标
  95% VaR:      {metrics.var_95*100:.2f}% (日)
  99% VaR:      {metrics.var_99*100:.2f}% (日)
  95% CVaR:     {metrics.cvar_95*100:.2f}% (日)
  最大回撤:     {metrics.max_drawdown*100:.2f}%

📈 基准比较
  Beta系数:     {metrics.beta:.3f}
  Alpha收益:    {metrics.alpha*100:.2f}% (年化)
  信息比率:     {metrics.information_ratio:.3f}

{'='*40}
"""
        return report
