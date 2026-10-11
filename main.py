"""
A股量化交易系统 - 主程序入口
用法:
    python main.py --stock 600519 --backtest          # 回测单只股票
    python main.py --stock 600519 --dashboard          # 回测+仪表盘
    python main.py --scan --strategy multi_factor      # 全市场扫描
    python main.py --stocks 600519,000858,000568       # 多股票回测
    python main.py --stock 600519 --optimize           # 参数优化
"""
import argparse
import sys
from pathlib import Path

# 确保项目根目录在path中
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from config import CONFIG
from data.provider import AShareDataProvider, FactorCalculator, get_data
from data.alpha158 import Alpha158, DataPipeline
from strategy.engine import (
    create_strategy, run_all_strategies, STRATEGY_REGISTRY,
    DualMAStrategy, MACDStrategy, BollingerMeanReversion,
    VolumeBreakoutStrategy, MultiFactorStrategy,
)
# 导入新策略 (触发注册)
import strategy.advanced
from backtest.engine import BacktestEngine
from risk.manager import RiskManager
from risk.advanced import AdvancedRiskManager, ParameterOptimizer
from signals.aggregator import SignalAggregator
from portfolio.manager import PortfolioManager


def print_banner():
    print("""
╔══════════════════════════════════════════════════╗
║        A股量化交易系统 v2.0                       ║
║        Quant Trading System for A-Shares         ║
║                                                  ║
║  策略: 双均线|MACD|布林带|放量突破|多因子          ║
║        海龟|动量反转|资金流向|DualThrust           ║
║  因子: Alpha158 (100+技术因子)                    ║
║  风控: ROI动态止盈|ATR止损|追踪止损|凯利仓位      ║
║  优化: 网格搜索|滚动前向优化                      ║
║  数据: akshare (免费实时行情)                      ║
╚══════════════════════════════════════════════════╝
""")


def run_single_backtest(symbol: str, strategy_name: str = "multi_factor",
                        start: str = None, end: str = None,
                        show_dashboard: bool = False):
    """单股回测"""
    print(f"\n{'='*50}")
    print(f"📊 回测: {symbol} | 策略: {strategy_name}")
    print(f"📅 区间: {start or CONFIG.backtest.start_date} ~ {end or CONFIG.backtest.end_date}")
    print(f"💰 初始资金: ¥{CONFIG.backtest.initial_capital:,.0f}")
    print(f"{'='*50}\n")

    # 1. 获取数据
    print("[1/4] 获取行情数据...")
    df = get_data(symbol, start, end)
    if df.empty:
        print(f"❌ 获取 {symbol} 数据失败!")
        return None
    print(f"  ✅ 获取 {len(df)} 条日线数据")

    # 2. 创建策略
    print(f"[2/4] 加载策略: {strategy_name}")
    strategy = create_strategy(strategy_name)

    # 3. 获取基准数据
    print("[3/4] 获取基准数据 (沪深300)...")
    provider = AShareDataProvider()
    benchmark_df = provider.get_index_daily("000300", start, end)

    # 4. 运行回测
    print("[4/4] 运行回测引擎...\n")
    engine = BacktestEngine()
    stats = engine.run(df, strategy, benchmark_df)

    # 输出结果
    print("╔══════════════════════════════════════╗")
    print("║         回 测 结 果                   ║")
    print("╚══════════════════════════════════════╝")
    for k, v in stats.items():
        print(f"  {k:　<10s}: {v}")
    print()

    # 交易记录
    trades_df = engine.get_trades_df()
    if not trades_df.empty:
        print(f"\n📋 交易记录 (共 {len(trades_df)} 笔):")
        print(trades_df.to_string(index=False))

    # 风险报告
    returns = engine.get_equity_df()["total_equity"].pct_change().dropna()
    if len(returns) > 0:
        rm = RiskManager()
        bm_returns = None
        if benchmark_df is not None and "close" in benchmark_df.columns:
            bm_returns = benchmark_df["close"].pct_change().dropna()
        print(rm.generate_risk_report(returns, bm_returns))

    # 启动仪表盘
    if show_dashboard:
        from dashboard.app import prepare_dashboard_data, run_dashboard
        dashboard_data = prepare_dashboard_data(engine, stats)
        run_dashboard(dashboard_data)

    return engine, stats


def run_multi_stock_backtest(symbols: list, strategy_name: str = "multi_factor"):
    """多股票回测对比"""
    print(f"\n{'='*50}")
    print(f"📊 多股票回测对比: {len(symbols)} 只股票")
    print(f"{'='*50}\n")

    results = {}
    for sym in symbols:
        print(f"\n--- {sym} ---")
        result = run_single_backtest(sym, strategy_name, show_dashboard=False)
        if result:
            engine, stats = result
            results[sym] = stats

    # 汇总对比
    if results:
        print(f"\n\n{'='*60}")
        print("📊 回测结果对比")
        print(f"{'='*60}")
        print(f"{'股票':>10s} {'总收益':>10s} {'年化':>10s} {'夏普':>8s} {'最大回撤':>10s} {'胜率':>8s}")
        print("-" * 60)
        for sym, s in results.items():
            print(f"{sym:>10s} {s.get('总收益率','N/A'):>10s} {s.get('年化收益率','N/A'):>10s} "
                  f"{s.get('夏普比率','N/A'):>8s} {s.get('最大回撤','N/A'):>10s} {s.get('胜率','N/A'):>8s}")

    return results


def run_strategy_scan(symbols: list = None):
    """全市场策略扫描"""
    print("\n🔍 全市场策略扫描模式")
    provider = AShareDataProvider()

    if symbols is None:
        print("获取A股列表...")
        stock_list = provider.get_stock_list()
        # 筛选: 市值>100亿, 日均成交>5000万
        if "market_cap" in stock_list.columns:
            stock_list = stock_list[stock_list["market_cap"] > 1e10]
        if "amount" in stock_list.columns:
            stock_list = stock_list[stock_list["amount"] > 5e7]
        symbols = stock_list["symbol"].tolist()[:50]  # 取前50
        print(f"筛选出 {len(symbols)} 只股票")

    aggregator = SignalAggregator()
    provider = AShareDataProvider()

    all_signals = []
    for i, sym in enumerate(symbols):
        print(f"[{i+1}/{len(symbols)}] 扫描 {sym}...")
        df = get_data(sym)
        if df.empty:
            continue
        signals = aggregator.aggregate(df)
        # 只看最新信号
        latest = [s for s in signals if s.date == signals[-1].date] if signals else []
        all_signals.extend(latest)

    if all_signals:
        print(f"\n📊 扫描结果: 发现 {len(all_signals)} 个最新信号")
        summary = aggregator.get_signal_summary(all_signals)
        print(summary.to_string(index=False))
    else:
        print("未发现信号")

    return all_signals


def run_optimize(symbol: str, strategy_name: str = "dual_ma",
                 start: str = None, end: str = None):
    """策略参数优化"""
    print(f"\n🔧 参数优化: {symbol} | 策略: {strategy_name}")
    print(f"{'='*50}\n")

    # 获取数据
    df = get_data(symbol, start, end)
    if df.empty:
        print(f"❌ 获取 {symbol} 数据失败!")
        return

    # 定义参数网格
    param_grids = {
        "dual_ma": {"short_window": [3, 5, 7, 10], "long_window": [15, 20, 25, 30]},
        "macd": {},
        "boll_mr": {"rsi_threshold_low": [25, 30, 35], "rsi_threshold_high": [65, 70, 75]},
        "vol_breakout": {"breakout_window": [10, 15, 20, 25], "vol_ratio_min": [1.5, 2.0, 2.5]},
        "turtle": {"entry_window": [15, 20, 25, 30], "exit_window": [5, 10, 15]},
        "momentum_reversal": {"short_lookback": [3, 5, 7], "long_lookback": [40, 60, 80]},
        "dual_thrust": {"lookback": [3, 4, 5], "k1": [0.4, 0.5, 0.6], "k2": [0.4, 0.5, 0.6]},
    }

    grid = param_grids.get(strategy_name, {})
    if not grid:
        print(f"⚠️ 策略 {strategy_name} 无可优化参数")
        return

    strategy_cls = STRATEGY_REGISTRY[strategy_name]

    print(f"参数网格: {grid}")
    print(f"开始网格搜索...\n")

    results = ParameterOptimizer.grid_search(strategy_cls, df, grid, metric="sharpe")

    if results:
        print(f"\n📊 优化结果 (Top 10):")
        print(f"{'排名':>4s} {'参数':>40s} {'收益率':>10s} {'夏普':>8s} {'回撤':>10s} {'胜率':>8s}")
        print("-" * 80)
        for i, r in enumerate(results[:10]):
            params_str = str({k: v for k, v in r.items()
                              if k not in ["total_return", "sharpe", "max_drawdown", "win_rate", "trades"]})
            print(f"{i+1:>4d} {params_str:>40s} {r['total_return']:>9.2f}% {r['sharpe']:>8.3f} "
                  f"{r['max_drawdown']:>9.2f}% {r['win_rate']:>7.1f}%")

        print(f"\n✅ 最优参数: {results[0]}")

        # 用最优参数跑一次完整回测
        best = results[0]
        best_params = {k: v for k, v in best.items()
                       if k not in ["total_return", "sharpe", "max_drawdown", "win_rate", "trades"]}
        print(f"\n用最优参数回测...")
        strategy = strategy_cls(**best_params)
        engine = BacktestEngine()
        stats = engine.run(df, strategy)
        print("\n╔══════════════════════════════════════╗")
        print("║     最优参数回测结果                   ║")
        print("╚══════════════════════════════════════╝")
        for k, v in stats.items():
            print(f"  {k}: {v}")
    else:
        print("优化失败, 无有效结果")


def main():
    parser = argparse.ArgumentParser(description="A股量化交易系统")
    parser.add_argument("--stock", type=str, help="股票代码, 如 600519")
    parser.add_argument("--stocks", type=str, help="多只股票, 逗号分隔, 如 600519,000858")
    parser.add_argument("--strategy", type=str, default="multi_factor",
                        choices=list(STRATEGY_REGISTRY.keys()),
                        help="策略名称")
    parser.add_argument("--start", type=str, help="开始日期 20230101")
    parser.add_argument("--end", type=str, help="结束日期 20251231")
    parser.add_argument("--dashboard", action="store_true", help="启动Web仪表盘")
    parser.add_argument("--scan", action="store_true", help="全市场扫描")
    parser.add_argument("--optimize", action="store_true", help="参数优化")
    parser.add_argument("--list-strategies", action="store_true", help="列出所有策略")
    parser.add_argument("--port", type=int, default=5088, help="仪表盘端口")

    args = parser.parse_args()

    print_banner()

    if args.list_strategies:
        print("可用策略:")
        for name in STRATEGY_REGISTRY:
            s = create_strategy(name)
            doc = s.__class__.__doc__
            desc = doc.strip().split("\n")[0] if doc else ""
            print(f"  - {name}: {desc}")
        return

    if args.optimize:
        run_optimize(args.stock or "600519", args.strategy, args.start, args.end)
        return

    if args.scan:
        run_strategy_scan()
        return

    if args.stocks:
        symbols = [s.strip() for s in args.stocks.split(",")]
        run_multi_stock_backtest(symbols, args.strategy)
        return

    if args.stock:
        run_single_backtest(args.stock, args.strategy, args.start, args.end, args.dashboard)
        return

    # 默认: 茅台回测演示
    print("💡 未指定股票, 运行演示回测: 贵州茅台 (600519)")
    run_single_backtest("600519", "multi_factor", show_dashboard=args.dashboard)


if __name__ == "__main__":
    main()
