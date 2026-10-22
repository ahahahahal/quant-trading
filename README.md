# 📈 A股量化交易系统 v2.0

> **一句话**：一个把「取行情 → 算因子 → 出信号 → 回测 → 风控 → 选股 → 看板」全链路串起来的 A 股量化框架，参考 vnpy / qlib / freqtrade 的架构设计。**零依赖门槛，装好 `akshare` + `pandas` 就能跑。**

它解决的是这个痛点：很多人学量化只有零散策略脚本，没有一套**能复用的完整骨架**。这个项目给了你：

- ✅ 一个能直接跑的命令行入口（回测 / 扫描 / 优化 / 看板一条命令切换）
- ✅ 一套标准分层架构（数据 / 策略 / 回测 / 风控 / 组合 / 看板）
- ✅ 10 种开箱即用的策略 + 100+ 因子库
- ✅ 带 T+1、佣金、印花税、滑点的**真实 A 股回测**（不是理想化回测）

---

## 🚀 快速开始

### 1. 装依赖

```bash
cd quant-trading
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

> 国内用户用清华镜像会快很多。核心依赖只有 `akshare`（行情）、`pandas`/`numpy`（计算）、`flask`（看板）、`pyecharts`（图表）。

### 2. 跑第一个回测

```bash
# 回测贵州茅台（600519），默认策略
python main.py --stock 600519

# 指定策略回测（海龟交易）
python main.py --stock 600519 --strategy turtle

# 多只股票对比
python main.py --stocks 600519,000858,000568
```

### 3. 看结果

回测跑完会在命令行直接打印**收益、回撤、夏普比率**等核心指标。想看可视化看板：

```bash
python main.py --stock 600519 --dashboard
```

浏览器会打开一个 Flask + ECharts 的 K 线看板，带买卖点标注。

---

## 🧩 系统架构

```
quant-trading/
├── main.py              # 主程序入口（CLI，一条命令切换所有功能）
├── config.py            # 全局配置（资金、费率、风控参数）
├── requirements.txt     # 依赖
│
├── data/                # 数据层
│   ├── provider.py      # akshare 数据源封装
│   └── alpha158.py      # Alpha158 因子库（100+ 因子）+ 数据清洗管线
│
├── strategy/            # 策略层
│   ├── engine.py        # 5 种基础策略 + 策略工厂（注册机制）
│   └── advanced.py      # 5 种高级策略（海龟/动量反转/资金流向等）
│
├── backtest/            # 回测引擎
│   └── engine.py        # 事件驱动，T+1，佣金/印花税/滑点
│
├── risk/                # 风控模块
│   ├── manager.py       # 基础风控（VaR/夏普/回撤）
│   └── advanced.py      # 高级风控（ROI 表/ATR 止损/凯利仓位/参数优化）
│
├── signals/             # 信号聚合
│   └── aggregator.py    # 多策略加权融合
│
├── portfolio/           # 组合管理
│   └── manager.py       # 持仓跟踪/行业暴露
│
├── ml/                  # 机器学习选股（XGBoost / LightGBM）
│
└── dashboard/           # Web 仪表盘（Flask + ECharts）
```

**核心设计**：策略通过「工厂 + 注册」机制接入，你写一个新策略只要 `@register_strategy` 一下，回测引擎、风控、看板全部自动支持，不用改任何别的代码。

---

## 📊 10 种内置策略

| 策略 | 原理 | 适合场景 |
|------|------|---------|
| `dual_ma` | 双均线交叉（5/20 日） | 趋势跟随入门 |
| `macd` | MACD 金叉死叉 | 经典趋势 |
| `boll_mr` | 布林带 + RSI 均值回归 | 震荡市 |
| `vol_breakout` | 放量突破 N 日新高 | 突破行情 |
| `multi_factor` | KDJ+RSI+MACD+均线+量能综合打分 | 稳健综合 |
| `turtle` | 海龟交易（唐奇安通道突破） | 长周期趋势 |
| `momentum_reversal` | 超跌反转 + 动量 | 抄底反弹 |
| `money_flow` | 资金流向 + 量能 | 主力跟踪 |
| `boll_breakout` | 布林带突破（趋势启动） | 趋势启动 |
| `dual_thrust` | Dual Thrust 通道突破 | 日内/波段 |

---

## 🛡️ 风控机制（v2.0 升级）

不是"裸奔回测"，内置了 freqtrade 级别的风控：

- **ROI 动态止盈表**：持仓越久止盈线越低（立即 20% → 40 天 3%）
- **追踪止损**：盈利 3% 后启动，从高点回撤 5% 触发
- **ATR 动态止损**：基于波动率的自适应止损
- **凯利仓位**：半凯利公式计算最优仓位
- **风险百分比仓位**：每笔风险不超过总资金 2%

---

## 🧬 Alpha158 因子库（100+ 因子）

参考微软 qlib 的 Alpha158，分 5 大类：

- **价格因子**：收益率 / 均线 / 价格位置（QTLU）/ 均线斜率 / 多头排列
- **量能因子**：量比 / 正负成交量 / OBV / 资金流向 / 量价相关
- **技术因子**：RSI(6/12/14/24) / MACD / KDJ / 布林带 / ATR / CCI / WilliamsR / ADX
- **形态因子**：K 线实体 / 影线 / 涨跌停 / 新高新低 / 连涨连跌 / 缺口
- **统计因子**：波动率 / 偏度 / 峰度 / 下行波动率 / 最大回撤 / 信息比率

---

## 🔧 进阶用法

```bash
# 参数优化（网格搜索最优参数）
python main.py --stock 600519 --strategy dual_ma --optimize

# 全市场扫描（跑所有策略，找出当前最值得关注的票）
python main.py --scan

# 全市场扫描 + 指定策略
python main.py --scan --strategy multi_factor
```

参数优化内置了**滚动前向优化（Walk-Forward）**，防止过拟合。

---

## ⚠️ 免责声明

本项目仅供**学习研究**使用，不构成任何投资建议。A 股有风险，回测收益不代表实盘收益，请勿直接用于实盘交易。

---

## 📚 参考项目

| 项目 | 借鉴了什么 |
|------|-----------|
| [vnpy](https://github.com/vnpy/vnpy) | 事件驱动架构 + 策略模板 |
| [qlib](https://github.com/microsoft/qlib) | Alpha158 因子库 + ML 管线 |
| [freqtrade](https://github.com/freqtrade/freqtrade) | ROI 表 + 追踪止损 |
| [akshare](https://github.com/akfamily/akshare) | A 股数据接口 |
