# 📈 A股量化交易系统 v2.0 (Quant Trading System for A-Shares)

参考 vnpy / qlib / freqtrade 架构设计的完整A股量化交易框架。

## 系统架构

```
quant-trading/
├── main.py              # 主程序入口 (CLI)
├── config.py            # 全局配置
├── requirements.txt     # 依赖
│
├── data/                # 数据层
│   ├── provider.py      # akshare数据源
│   └── alpha158.py      # Alpha158因子库 (100+因子) + 数据清洗管线
│
├── strategy/            # 策略层
│   ├── engine.py        # 5种基础策略 + 策略工厂
│   └── advanced.py      # 5种高级策略 (海龟/动量反转/资金流向等)
│
├── backtest/            # 回测引擎
│   └── engine.py        # 事件驱动, T+1, 佣金/印花税/滑点
│
├── risk/                # 风控模块
│   ├── manager.py       # 基础风控 (VaR/夏普/回撤)
│   └── advanced.py      # 高级风控 (ROI表/ATR止损/凯利仓位/参数优化)
│
├── signal/              # 信号聚合
│   └── aggregator.py    # 多策略加权融合
│
├── portfolio/           # 组合管理
│   └── manager.py       # 持仓跟踪/行业暴露
│
└── dashboard/           # Web仪表盘
    └── app.py           # Flask + ECharts 可视化
```

## 快速开始

```bash
cd G:/AI学习资料/quant-trading
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 回测单只股票
python main.py --stock 600519

# 指定策略
python main.py --stock 600519 --strategy turtle

# 多股票对比
python main.py --stocks 600519,000858,000568

# 参数优化
python main.py --stock 600519 --strategy dual_ma --optimize

# 启动Web仪表盘
python main.py --stock 600519 --dashboard

# 全市场扫描
python main.py --scan
```

## 10种内置策略

| 策略 | 原理 | 来源 |
|------|------|------|
| `dual_ma` | 双均线交叉 (5/20日) | 经典 |
| `macd` | MACD金叉死叉 | 经典 |
| `boll_mr` | 布林带+RSI均值回归 | 经典 |
| `vol_breakout` | 放量突破N日新高 | 经典 |
| `multi_factor` | KDJ+RSI+MACD+均线+量能 | 综合 |
| `turtle` | 海龟交易 (唐奇安通道突破) | 经典 |
| `momentum_reversal` | 超跌反转+动量 | 学术 |
| `money_flow` | 资金流向+量能 | 量化 |
| `boll_breakout` | 布林带突破(趋势启动) | 量化 |
| `dual_thrust` | Dual Thrust通道突破 | 经典 |

## 风控机制 (v2.0升级)

- **ROI动态止盈表** (参考freqtrade): 持仓越久止盈线越低
  - 立即: 20% | 5天: 15% | 10天: 10% | 20天: 6% | 40天: 3%
- **追踪止损**: 盈利3%后启动, 从高点回撤5%触发
- **ATR动态止损**: 基于波动率的自适应止损
- **凯利仓位**: 半凯利公式计算最优仓位
- **风险百分比仓位**: 每笔风险不超过总资金2%

## Alpha158因子库 (100+因子)

参考微软qlib, 5大类因子:
- **价格因子**: 收益率/均线/价格位置(QTLU)/均线斜率/多头排列
- **量能因子**: 量比/正负成交量/OBV/资金流向/量价相关
- **技术因子**: RSI(6/12/14/24)/MACD/KDJ/布林带/ATR/CCI/WilliamsR/ADX
- **形态因子**: K线实体/影线/涨跌停/新高新低/连涨连跌/缺口
- **统计因子**: 波动率/偏度/峰度/下行波动率/最大回撤/信息比率

## 参数优化

```bash
# 网格搜索最优参数
python main.py --stock 600519 --strategy dual_ma --optimize

# 内置滚动前向优化 (Walk-Forward) 防止过拟合
```

## 参考项目

- [vnpy](https://github.com/vnpy/vnpy) - 事件驱动架构 + 策略模板
- [qlib](https://github.com/microsoft/qlib) - Alpha158因子库 + ML管线
- [freqtrade](https://github.com/freqtrade/freqtrade) - ROI表 + 追踪止损
- [akshare](https://github.com/akfamily/akshare) - A股数据接口

