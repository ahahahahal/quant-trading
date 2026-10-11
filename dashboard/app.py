"""
Web仪表盘 - 回测结果可视化
基于Flask + ECharts
"""
from flask import Flask, render_template_string, jsonify, request
import pandas as pd
import numpy as np
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))


app = Flask(__name__)

# 全局存储回测结果
_backtest_results = {}


def set_results(results: dict):
    global _backtest_results
    _backtest_results = results


DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>A股量化交易系统 - 仪表盘</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, 'Microsoft YaHei', sans-serif;
               background: #0f1117; color: #e0e0e0; }
        .header { background: linear-gradient(135deg, #1a1a2e, #16213e);
                  padding: 20px 30px; border-bottom: 1px solid #333; }
        .header h1 { font-size: 24px; color: #00d4aa; }
        .header p { color: #888; margin-top: 5px; }
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
        .stats-grid { display: grid; grid-template-columns: repeat(4, 1fr);
                      gap: 15px; margin-bottom: 20px; }
        .stat-card { background: #1a1a2e; border-radius: 10px; padding: 20px;
                     border: 1px solid #333; }
        .stat-card .label { color: #888; font-size: 13px; }
        .stat-card .value { font-size: 28px; font-weight: bold; margin-top: 5px; }
        .stat-card .value.positive { color: #ff4757; }
        .stat-card .value.negative { color: #2ed573; }
        .chart-row { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 20px; }
        .chart-box { background: #1a1a2e; border-radius: 10px; padding: 15px;
                     border: 1px solid #333; }
        .chart-box h3 { color: #aaa; margin-bottom: 10px; font-size: 14px; }
        .chart-container { width: 100%; height: 350px; }
        .full-width { grid-column: 1 / -1; }
        .trades-table { width: 100%; border-collapse: collapse; margin-top: 10px; }
        .trades-table th { background: #16213e; padding: 10px; text-align: left;
                           color: #00d4aa; font-size: 13px; }
        .trades-table td { padding: 8px 10px; border-bottom: 1px solid #222;
                           font-size: 13px; }
        .trades-table tr:hover { background: #1a2a3e; }
        .buy { color: #ff4757; } .sell { color: #2ed573; }
        .strategy-btn { padding: 8px 16px; margin: 3px; background: #16213e;
                        color: #aaa; border: 1px solid #333; border-radius: 5px;
                        cursor: pointer; }
        .strategy-btn.active { background: #00d4aa; color: #000; }
    </style>
</head>
<body>
    <div class="header">
        <h1>📈 A股量化交易系统</h1>
        <p>Quantitative Trading System for A-Shares</p>
    </div>

    <div class="container">
        <!-- 核心指标 -->
        <div class="stats-grid" id="statsGrid"></div>

        <!-- 图表区域 -->
        <div class="chart-row">
            <div class="chart-box full-width">
                <h3>📊 权益曲线 vs 基准</h3>
                <div class="chart-container" id="equityChart"></div>
            </div>
        </div>
        <div class="chart-row">
            <div class="chart-box">
                <h3>📉 回撤曲线</h3>
                <div class="chart-container" id="drawdownChart"></div>
            </div>
            <div class="chart-box">
                <h3>📊 日收益率分布</h3>
                <div class="chart-container" id="returnDistChart"></div>
            </div>
        </div>
        <div class="chart-row">
            <div class="chart-box">
                <h3>🎯 交易信号分布</h3>
                <div class="chart-container" id="signalChart"></div>
            </div>
            <div class="chart-box">
                <h3>💰 仓位变化</h3>
                <div class="chart-container" id="positionChart"></div>
            </div>
        </div>

        <!-- 交易记录 -->
        <div class="chart-box" style="margin-bottom: 20px;">
            <h3>📋 交易记录</h3>
            <div style="max-height: 400px; overflow-y: auto;">
                <table class="trades-table" id="tradesTable">
                    <thead>
                        <tr>
                            <th>日期</th><th>股票</th><th>方向</th>
                            <th>价格</th><th>数量</th><th>金额</th>
                            <th>佣金</th><th>策略</th><th>原因</th>
                        </tr>
                    </thead>
                    <tbody></tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        // 从后端获取数据
        const data = {{ data | tojson }};

        // 渲染统计卡片
        function renderStats(stats) {
            const grid = document.getElementById('statsGrid');
            const items = [
                {label: '总收益率', value: stats['总收益率'], positive: parseFloat(stats['总收益率']) > 0},
                {label: '年化收益率', value: stats['年化收益率'], positive: parseFloat(stats['年化收益率']) > 0},
                {label: '夏普比率', value: stats['夏普比率'], positive: parseFloat(stats['夏普比率']) > 1},
                {label: '最大回撤', value: stats['最大回撤'], positive: false},
                {label: '胜率', value: stats['胜率'], positive: parseFloat(stats['胜率']) > 50},
                {label: '卡尔玛比率', value: stats['卡尔玛比率'], positive: parseFloat(stats['卡尔玛比率']) > 1},
                {label: '交易次数', value: stats['交易次数'], positive: true},
                {label: '总盈亏', value: '¥' + stats['总盈亏'], positive: parseFloat(stats['总盈亏']) > 0},
            ];
            grid.innerHTML = items.map(i => `
                <div class="stat-card">
                    <div class="label">${i.label}</div>
                    <div class="value ${i.positive ? 'positive' : 'negative'}">${i.value}</div>
                </div>
            `).join('');
        }

        // 权益曲线
        function renderEquityChart(eq) {
            const chart = echarts.init(document.getElementById('equityChart'));
            chart.setOption({
                backgroundColor: 'transparent',
                tooltip: { trigger: 'axis' },
                legend: { data: ['策略权益', '基准'], textStyle: {color: '#aaa'} },
                grid: { left: 60, right: 30, top: 40, bottom: 30 },
                xAxis: { type: 'category', data: eq.dates, axisLabel: {color: '#666'} },
                yAxis: { type: 'value', axisLabel: {color: '#666', formatter: v => (v/10000).toFixed(0)+'万'} },
                series: [
                    { name: '策略权益', type: 'line', data: eq.equity, lineStyle: {color: '#00d4aa', width: 2},
                      areaStyle: {color: {type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                        colorStops: [{offset: 0, color: 'rgba(0,212,170,0.3)'}, {offset: 1, color: 'rgba(0,212,170,0)'}]}},
                      showSymbol: false },
                    { name: '基准', type: 'line', data: eq.benchmark, lineStyle: {color: '#666', width: 1, type: 'dashed'},
                      showSymbol: false },
                ]
            });
            window.addEventListener('resize', () => chart.resize());
        }

        // 回撤曲线
        function renderDrawdownChart(eq) {
            const chart = echarts.init(document.getElementById('drawdownChart'));
            chart.setOption({
                backgroundColor: 'transparent',
                tooltip: { trigger: 'axis', formatter: p => p[0].name + '<br/>回撤: ' + (p[0].value*100).toFixed(2) + '%' },
                grid: { left: 60, right: 30, top: 20, bottom: 30 },
                xAxis: { type: 'category', data: eq.dates, axisLabel: {color: '#666'} },
                yAxis: { type: 'value', axisLabel: {color: '#666', formatter: v => (v*100).toFixed(0)+'%'},
                         max: 0 },
                series: [{
                    type: 'line', data: eq.drawdown, lineStyle: {color: '#ff4757', width: 1},
                    areaStyle: {color: 'rgba(255,71,87,0.2)'},
                    showSymbol: false,
                }]
            });
            window.addEventListener('resize', () => chart.resize());
        }

        // 收益率分布
        function renderReturnDist(returns) {
            const chart = echarts.init(document.getElementById('returnDistChart'));
            const bins = {};
            returns.forEach(r => {
                const b = (Math.round(r * 1000) / 10).toFixed(1);
                bins[b] = (bins[b] || 0) + 1;
            });
            const sorted = Object.entries(bins).sort((a,b) => parseFloat(a[0]) - parseFloat(b[0]));
            chart.setOption({
                backgroundColor: 'transparent',
                tooltip: {},
                grid: { left: 50, right: 20, top: 20, bottom: 30 },
                xAxis: { type: 'category', data: sorted.map(s => s[0]+'%'), axisLabel: {color: '#666', rotate: 45, fontSize: 10} },
                yAxis: { type: 'value', axisLabel: {color: '#666'} },
                series: [{
                    type: 'bar', data: sorted.map(s => ({
                        value: s[1],
                        itemStyle: { color: parseFloat(s[0]) >= 0 ? '#ff4757' : '#2ed573' }
                    })),
                }]
            });
            window.addEventListener('resize', () => chart.resize());
        }

        // 信号分布
        function renderSignalChart(signals) {
            const chart = echarts.init(document.getElementById('signalChart'));
            chart.setOption({
                backgroundColor: 'transparent',
                tooltip: { trigger: 'item' },
                series: [{
                    type: 'pie', radius: ['40%', '70%'],
                    data: [
                        {value: signals.buy, name: '买入信号', itemStyle: {color: '#ff4757'}},
                        {value: signals.sell, name: '卖出信号', itemStyle: {color: '#2ed573'}},
                    ],
                    label: { color: '#aaa' },
                }]
            });
        }

        // 仓位变化
        function renderPositionChart(eq) {
            const chart = echarts.init(document.getElementById('positionChart'));
            chart.setOption({
                backgroundColor: 'transparent',
                tooltip: { trigger: 'axis' },
                grid: { left: 50, right: 20, top: 20, bottom: 30 },
                xAxis: { type: 'category', data: eq.dates, axisLabel: {color: '#666'} },
                yAxis: { type: 'value', axisLabel: {color: '#666'} },
                series: [{
                    type: 'bar', data: eq.positions,
                    itemStyle: { color: '#5352ed' },
                }]
            });
        }

        // 交易记录
        function renderTrades(trades) {
            const tbody = document.querySelector('#tradesTable tbody');
            tbody.innerHTML = trades.map(t => `
                <tr>
                    <td>${t.date}</td>
                    <td>${t.symbol}</td>
                    <td class="${t.direction === 'BUY' ? 'buy' : 'sell'}">
                        ${t.direction === 'BUY' ? '买入' : '卖出'}</td>
                    <td>¥${t.price.toFixed(2)}</td>
                    <td>${t.shares}</td>
                    <td>¥${t.amount.toFixed(0)}</td>
                    <td>¥${(t.commission + t.stamp_tax).toFixed(2)}</td>
                    <td>${t.strategy}</td>
                    <td>${t.reason || '-'}</td>
                </tr>
            `).join('');
        }

        // 初始化
        renderStats(data.stats);
        renderEquityChart(data.equity);
        renderDrawdownChart(data.equity);
        renderReturnDist(data.returns);
        renderSignalChart(data.signals);
        renderPositionChart(data.equity);
        renderTrades(data.trades);
    </script>
</body>
</html>
"""


@app.route("/")
def dashboard():
    if not _backtest_results:
        return "<h1>请先运行回测</h1><p>在Python中运行: python main.py --backtest</p>"

    data = _backtest_results
    return render_template_string(DASHBOARD_HTML, data=data)


@app.route("/api/results")
def api_results():
    return jsonify(_backtest_results)


def prepare_dashboard_data(engine, stats) -> dict:
    """将回测引擎数据转为仪表盘格式"""
    eq_df = engine.get_equity_df()
    trades_df = engine.get_trades_df()

    # 权益曲线数据
    equity_data = {
        "dates": eq_df["date"].tolist(),
        "equity": eq_df["total_equity"].tolist(),
        "benchmark": eq_df["benchmark_value"].tolist(),
        "drawdown": (-eq_df["drawdown"]).tolist(),
        "positions": eq_df["positions"].tolist(),
    }

    # 日收益率
    returns = engine.daily_returns

    # 信号统计
    buy_count = len([t for t in engine.trades if t.direction == "BUY"])
    sell_count = len([t for t in engine.trades if t.direction == "SELL"])
    signals_data = {"buy": buy_count, "sell": sell_count}

    # 交易记录
    trades_list = trades_df.to_dict("records") if not trades_df.empty else []

    return {
        "stats": stats,
        "equity": equity_data,
        "returns": returns,
        "signals": signals_data,
        "trades": trades_list,
    }


def run_dashboard(data: dict, port: int = 5088):
    """启动仪表盘"""
    set_results(data)
    print(f"\n🌐 仪表盘已启动: http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
