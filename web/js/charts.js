/**
 * QuantX Pro - Chart Manager
 * ECharts封装：K线图、权益曲线、回撤曲线
 * 主题：深色金融风格，红涨绿跌（中国A股习惯）
 */
const ChartManager = {
    instances: {},
    theme: {
        bgColor: 'transparent',          // 透明背景，与卡片融合
        textColor: '#94a3b8',
        axisColor: '#1e293b',
        upColor: '#ff1744',              // 红色=涨
        downColor: '#00e676',            // 绿色=跌
        ma5Color: '#f59e0b',
        ma10Color: '#3b82f6',
        ma20Color: '#a855f7',
        volumeUp: 'rgba(255, 23, 68, 0.45)',
        volumeDown: 'rgba(0, 230, 118, 0.45)',
        gridBorderColor: '#1e293b',
        tooltipBg: 'rgba(17, 24, 39, 0.96)',
        tooltipBorder: '#1e293b',
    },

    getOrCreate(domId) {
        if (this.instances[domId]) {
            this.instances[domId].dispose();
        }
        const dom = document.getElementById(domId);
        if (!dom) return null;
        const chart = echarts.init(dom, null, { renderer: 'canvas' });
        this.instances[domId] = chart;
        window.addEventListener('resize', () => chart.resize());
        return chart;
    },

    /**
     * K线图 - 专业级蜡烛图 + 成交量 + MA指标 + 买卖信号
     */
    renderKline(domId, data, signals = []) {
        const chart = this.getOrCreate(domId);
        if (!chart || !data || data.length === 0) return;

        const t = this.theme;
        const dates = data.map(d => d.date);
        const ohlc = data.map(d => [d.open, d.close, d.low, d.high]);
        const volumes = data.map((d) => ({
            value: d.volume,
            itemStyle: { color: d.close >= d.open ? t.volumeUp : t.volumeDown }
        }));

        const ma5 = this._calcMA(data, 5);
        const ma10 = this._calcMA(data, 10);
        const ma20 = this._calcMA(data, 20);

        // Process signals
        const buyPoints = [];
        const sellPoints = [];
        if (signals && signals.length) {
            signals.forEach(s => {
                const idx = dates.indexOf(s.date);
                if (idx === -1) return;
                if (s.type === 'BUY') {
                    buyPoints.push({ coord: [idx, data[idx].low * 0.995], value: s.price || data[idx].low });
                } else {
                    sellPoints.push({ coord: [idx, data[idx].high * 1.005], value: s.price || data[idx].high });
                }
            });
        }

        const option = {
            backgroundColor: t.bgColor,
            animation: false,
            tooltip: {
                trigger: 'axis',
                axisPointer: {
                    type: 'cross',
                    crossStyle: { color: '#3a4a5a' },
                    lineStyle: { color: '#3a4a5a', width: 1, type: 'dashed' }
                },
                backgroundColor: t.tooltipBg,
                borderColor: t.tooltipBorder,
                borderWidth: 1,
                textStyle: { color: '#e2e8f0', fontSize: 13, fontFamily: "'Inter', sans-serif" },
                formatter: function (params) {
                    if (!params || params.length === 0) return '';
                    const idx = params[0].dataIndex;
                    const d = data[idx];
                    if (!d) return '';
                    const chg = ((d.close - d.open) / d.open * 100).toFixed(2);
                    const color = d.close >= d.open ? t.upColor : t.downColor;
                    let html = `<div style="font-weight:600;margin-bottom:8px;font-size:14px;">${d.date}</div>`;
                    html += `<div style="display:flex;justify-content:space-between;gap:24px;">开盘<span style="color:${color};font-family:'JetBrains Mono',monospace;font-weight:600;">${d.open.toFixed(2)}</span></div>`;
                    html += `<div style="display:flex;justify-content:space-between;gap:24px;">最高<span style="color:${color};font-family:'JetBrains Mono',monospace;font-weight:600;">${d.high.toFixed(2)}</span></div>`;
                    html += `<div style="display:flex;justify-content:space-between;gap:24px;">最低<span style="color:${color};font-family:'JetBrains Mono',monospace;font-weight:600;">${d.low.toFixed(2)}</span></div>`;
                    html += `<div style="display:flex;justify-content:space-between;gap:24px;">收盘<span style="color:${color};font-family:'JetBrains Mono',monospace;font-weight:600;">${d.close.toFixed(2)}</span></div>`;
                    html += `<div style="display:flex;justify-content:space-between;gap:24px;">涨跌<span style="color:${color};font-family:'JetBrains Mono',monospace;font-weight:600;">${chg}%</span></div>`;
                    html += `<div style="display:flex;justify-content:space-between;gap:24px;">成交量<span style="font-family:'JetBrains Mono',monospace;">${d.volume ? (d.volume / 10000).toFixed(0) + '万' : '-'}</span></div>`;
                    return html;
                }
            },
            axisPointer: {
                link: [{ xAxisIndex: 'all' }],
                label: { backgroundColor: '#1e293b', color: '#e2e8f0', fontFamily: "'JetBrains Mono', monospace" }
            },
            grid: [
                { left: '60', right: '40', top: '40', height: '55%' },
                { left: '60', right: '40', top: '74%', height: '18%' }
            ],
            xAxis: [
                {
                    type: 'category', data: dates, gridIndex: 0,
                    axisLine: { lineStyle: { color: t.axisColor, width: 1 } },
                    axisLabel: { color: t.textColor, fontSize: 11, fontFamily: "'JetBrains Mono', monospace" },
                    splitLine: { show: false },
                    boundaryGap: true, min: 'dataMin', max: 'dataMax'
                },
                {
                    type: 'category', data: dates, gridIndex: 1,
                    axisLine: { lineStyle: { color: t.axisColor, width: 1 } },
                    axisLabel: { show: false },
                    splitLine: { show: false },
                    boundaryGap: true, min: 'dataMin', max: 'dataMax'
                }
            ],
            yAxis: [
                {
                    type: 'value', gridIndex: 0,
                    axisLine: { show: false },
                    axisTick: { show: false },
                    axisLabel: { color: t.textColor, fontSize: 11, fontFamily: "'JetBrains Mono', monospace" },
                    splitLine: { lineStyle: { color: t.gridBorderColor, type: 'dashed', width: 0.5 } },
                    scale: true
                },
                {
                    type: 'value', gridIndex: 1,
                    axisLine: { show: false },
                    axisTick: { show: false },
                    axisLabel: { show: false },
                    splitLine: { show: false },
                    scale: true
                }
            ],
            dataZoom: [
                {
                    type: 'inside', xAxisIndex: [0, 1],
                    start: data.length > 120 ? Math.max(0, 100 - 120 / data.length * 100) : 0,
                    end: 100, minValueSpan: 10
                },
                {
                    type: 'slider', xAxisIndex: [0, 1],
                    bottom: '2%', height: 20,
                    borderColor: t.gridBorderColor,
                    backgroundColor: 'rgba(0,0,0,0.15)',
                    fillerColor: 'rgba(59,130,246,0.1)',
                    handleStyle: { color: '#3b82f6', borderColor: '#3b82f6' },
                    textStyle: { color: t.textColor, fontSize: 10 },
                    dataBackground: {
                        lineStyle: { color: t.textColor, opacity: 0.2 },
                        areaStyle: { color: t.textColor, opacity: 0.03 }
                    }
                }
            ],
            series: [
                {
                    name: 'K线', type: 'candlestick',
                    xAxisIndex: 0, yAxisIndex: 0, data: ohlc,
                    itemStyle: {
                        color: t.upColor,
                        color0: t.downColor,
                        borderColor: t.upColor,
                        borderColor0: t.downColor,
                        borderWidth: 1
                    },
                    markPoint: {
                        symbol: 'triangle', symbolSize: 12,
                        data: [
                            ...buyPoints.map(p => ({ ...p, symbolRotate: 0, itemStyle: { color: t.upColor } })),
                            ...sellPoints.map(p => ({ ...p, symbolRotate: 180, itemStyle: { color: t.downColor } }))
                        ],
                        label: { show: false }
                    }
                },
                { name: 'MA5', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: ma5, smooth: true, lineStyle: { width: 1, color: t.ma5Color }, symbol: 'none' },
                { name: 'MA10', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: ma10, smooth: true, lineStyle: { width: 1, color: t.ma10Color }, symbol: 'none' },
                { name: 'MA20', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: ma20, smooth: true, lineStyle: { width: 1, color: t.ma20Color }, symbol: 'none' },
                {
                    name: '成交量', type: 'bar',
                    xAxisIndex: 1, yAxisIndex: 1, data: volumes, barMaxWidth: 8
                }
            ]
        };

        chart.setOption(option);
    },

    /**
     * 权益曲线图 - 策略 vs 基准
     */
    renderEquityCurve(domId, dates, equity, benchmark = null) {
        const chart = this.getOrCreate(domId);
        if (!chart) return;
        const t = this.theme;

        const series = [
            {
                name: '策略权益', type: 'line', data: equity,
                smooth: true, symbol: 'none',
                lineStyle: { width: 2, color: '#3b82f6' },
                areaStyle: {
                    color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                        { offset: 0, color: 'rgba(59,130,246,0.25)' },
                        { offset: 1, color: 'rgba(59,130,246,0.01)' }
                    ])
                }
            }
        ];

        if (benchmark) {
            series.push({
                name: '基准', type: 'line', data: benchmark,
                smooth: true, symbol: 'none',
                lineStyle: { width: 1.5, color: '#64748b', type: 'dashed' }
            });
        }

        const option = {
            backgroundColor: t.bgColor,
            tooltip: {
                trigger: 'axis',
                backgroundColor: t.tooltipBg,
                borderColor: t.tooltipBorder,
                borderWidth: 1,
                textStyle: { color: '#e2e8f0', fontSize: 13 }
            },
            legend: {
                data: series.map(s => s.name),
                textStyle: { color: t.textColor, fontSize: 12 },
                top: 8, right: 20
            },
            grid: { left: '60', right: '40', top: '50', bottom: '60' },
            xAxis: {
                type: 'category', data: dates,
                axisLine: { lineStyle: { color: t.axisColor } },
                axisLabel: { color: t.textColor, fontSize: 11, fontFamily: "'JetBrains Mono', monospace" },
                splitLine: { show: false }
            },
            yAxis: {
                type: 'value',
                axisLine: { show: false },
                axisTick: { show: false },
                axisLabel: { color: t.textColor, fontSize: 11, fontFamily: "'JetBrains Mono', monospace" },
                splitLine: { lineStyle: { color: t.gridBorderColor, type: 'dashed', width: 0.5 } },
                scale: true
            },
            dataZoom: [
                { type: 'inside' },
                { type: 'slider', bottom: 8, height: 18, borderColor: t.gridBorderColor, backgroundColor: 'rgba(0,0,0,0.1)', fillerColor: 'rgba(59,130,246,0.1)' }
            ],
            series: series
        };

        chart.setOption(option);
    },

    /**
     * 回撤曲线图
     */
    renderDrawdown(domId, dates, drawdowns) {
        const chart = this.getOrCreate(domId);
        if (!chart) return;
        const t = this.theme;

        const option = {
            backgroundColor: t.bgColor,
            tooltip: {
                trigger: 'axis',
                backgroundColor: t.tooltipBg,
                borderColor: t.tooltipBorder,
                borderWidth: 1,
                textStyle: { color: '#e2e8f0', fontSize: 13 },
                formatter: params => {
                    if (!params[0]) return '';
                    return `<div style="font-weight:600;margin-bottom:4px;">${params[0].axisValue}</div>
                            回撤: <b style="color:${t.upColor};font-family:'JetBrains Mono',monospace;">${params[0].value.toFixed(2)}%</b>`;
                }
            },
            grid: { left: '60', right: '40', top: '30', bottom: '60' },
            xAxis: {
                type: 'category', data: dates,
                axisLine: { lineStyle: { color: t.axisColor } },
                axisLabel: { color: t.textColor, fontSize: 11, fontFamily: "'JetBrains Mono', monospace" },
                splitLine: { show: false }
            },
            yAxis: {
                type: 'value',
                axisLine: { show: false },
                axisTick: { show: false },
                axisLabel: { color: t.textColor, fontSize: 11, formatter: '{value}%', fontFamily: "'JetBrains Mono', monospace" },
                splitLine: { lineStyle: { color: t.gridBorderColor, type: 'dashed', width: 0.5 } },
                inverse: true
            },
            dataZoom: [
                { type: 'inside' },
                { type: 'slider', bottom: 8, height: 18, borderColor: t.gridBorderColor, backgroundColor: 'rgba(0,0,0,0.1)', fillerColor: 'rgba(255,23,68,0.1)' }
            ],
            series: [{
                type: 'line', data: drawdowns,
                smooth: true, symbol: 'none',
                lineStyle: { width: 1.5, color: t.upColor },
                areaStyle: {
                    color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                        { offset: 0, color: 'rgba(255,23,68,0.3)' },
                        { offset: 1, color: 'rgba(255,23,68,0.01)' }
                    ])
                }
            }]
        };

        chart.setOption(option);
    },

    _calcMA(data, period) {
        const result = [];
        for (let i = 0; i < data.length; i++) {
            if (i < period - 1) {
                result.push(null);
            } else {
                let sum = 0;
                for (let j = 0; j < period; j++) {
                    sum += data[i - j].close;
                }
                result.push(+(sum / period).toFixed(2));
            }
        }
        return result;
    },

    resizeAll() {
        Object.values(this.instances).forEach(chart => chart.resize());
    },

    /**
     * 盈亏曲线图 - 模拟交易
     */
    renderPnlCurve(domId, data) {
        const chart = this.getOrCreate(domId);
        if (!chart || !data || data.length === 0) return;
        const t = this.theme;

        const dates = data.map(d => d.date);
        const equityValues = data.map(d => d.total_equity);
        const pnlValues = data.map(d => d.total_pnl);
        const returnPcts = data.map(d => d.total_return_pct);

        const option = {
            backgroundColor: t.bgColor,
            tooltip: {
                trigger: 'axis',
                backgroundColor: t.tooltipBg,
                borderColor: t.tooltipBorder,
                borderWidth: 1,
                textStyle: { color: '#e2e8f0', fontSize: 13 },
                formatter: function (params) {
                    if (!params || params.length === 0) return '';
                    const idx = params[0].dataIndex;
                    const d = data[idx];
                    if (!d) return '';
                    const pnlColor = d.total_pnl >= 0 ? t.upColor : t.downColor;
                    let html = `<div style="font-weight:600;margin-bottom:8px;">${d.date}</div>`;
                    html += `<div>总资产: <b style="font-family:'JetBrains Mono',monospace;">¥${d.total_equity.toLocaleString()}</b></div>`;
                    html += `<div>盈亏: <span style="color:${pnlColor};font-family:'JetBrains Mono',monospace;font-weight:600;">${d.total_pnl >= 0 ? '+' : ''}¥${d.total_pnl.toLocaleString()}</span></div>`;
                    html += `<div>收益率: <span style="color:${pnlColor};font-family:'JetBrains Mono',monospace;font-weight:600;">${d.total_return_pct.toFixed(2)}%</span></div>`;
                    html += `<div>现金: <span style="font-family:'JetBrains Mono',monospace;">¥${d.cash.toLocaleString()}</span></div>`;
                    html += `<div>持仓市值: <span style="font-family:'JetBrains Mono',monospace;">¥${d.market_value.toLocaleString()}</span></div>`;
                    return html;
                }
            },
            legend: {
                data: ['总资产', '累计收益率'],
                textStyle: { color: t.textColor, fontSize: 12 },
                top: 8, right: 20
            },
            grid: { left: '60', right: '60', top: '50', bottom: '60' },
            xAxis: {
                type: 'category', data: dates,
                axisLine: { lineStyle: { color: t.axisColor } },
                axisLabel: { color: t.textColor, fontSize: 11, fontFamily: "'JetBrains Mono', monospace" },
                splitLine: { show: false }
            },
            yAxis: [
                {
                    type: 'value', name: '金额',
                    nameTextStyle: { color: t.textColor },
                    axisLine: { show: false },
                    axisTick: { show: false },
                    axisLabel: {
                        color: t.textColor, fontSize: 11,
                        fontFamily: "'JetBrains Mono', monospace",
                        formatter: v => (v / 10000).toFixed(0) + '万'
                    },
                    splitLine: { lineStyle: { color: t.gridBorderColor, type: 'dashed', width: 0.5 } },
                    scale: true
                },
                {
                    type: 'value', name: '收益率%',
                    nameTextStyle: { color: t.textColor },
                    axisLine: { show: false },
                    axisTick: { show: false },
                    axisLabel: {
                        color: t.textColor, fontSize: 11,
                        fontFamily: "'JetBrains Mono', monospace",
                        formatter: '{value}%'
                    },
                    splitLine: { show: false }
                }
            ],
            dataZoom: [
                { type: 'inside' },
                {
                    type: 'slider', bottom: 8, height: 18,
                    borderColor: t.gridBorderColor,
                    backgroundColor: 'rgba(0,0,0,0.1)',
                    fillerColor: 'rgba(59,130,246,0.1)'
                }
            ],
            series: [
                {
                    name: '总资产', type: 'line', yAxisIndex: 0,
                    data: equityValues, smooth: true, symbol: 'none',
                    lineStyle: { width: 2, color: '#3b82f6' },
                    areaStyle: {
                        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                            { offset: 0, color: 'rgba(59,130,246,0.25)' },
                            { offset: 1, color: 'rgba(59,130,246,0.01)' }
                        ])
                    }
                },
                {
                    name: '累计收益率', type: 'line', yAxisIndex: 1,
                    data: returnPcts, smooth: true, symbol: 'none',
                    lineStyle: { width: 1.5, color: '#f59e0b' },
                    areaStyle: {
                        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                            { offset: 0, color: 'rgba(245,158,11,0.15)' },
                            { offset: 1, color: 'rgba(245,158,11,0.01)' }
                        ])
                    }
                }
            ]
        };

        chart.setOption(option);
    },

    /**
     * IC柱状图 - 因子信息系数
     */
    renderICBar(domId, data) {
        const chart = this.getOrCreate(domId);
        if (!chart || !data || !data.factors || data.factors.length === 0) return;
        const t = this.theme;

        const option = {
            backgroundColor: t.bgColor,
            tooltip: {
                trigger: 'axis',
                backgroundColor: t.tooltipBg,
                borderColor: t.tooltipBorder,
                borderWidth: 1,
                textStyle: { color: '#e2e8f0', fontSize: 13 },
                formatter: function (params) {
                    if (!params[0]) return '';
                    const idx = params[0].dataIndex;
                    const factor = data.factors[idx];
                    const icMean = data.ic_mean[idx];
                    const icStd = data.ic_std[idx];
                    const icir = data.icir[idx];
                    return `<div style="font-weight:600;margin-bottom:6px;">${factor}</div>
                            <div>IC均值: <b style="font-family:'JetBrains Mono',monospace;">${icMean}</b></div>
                            <div>IC标准差: <span style="font-family:'JetBrains Mono',monospace;">${icStd}</span></div>
                            <div>ICIR: <b style="color:#00e676;font-family:'JetBrains Mono',monospace;">${icir}</b></div>`;
                }
            },
            grid: { left: '60', right: '40', top: '50', bottom: '80' },
            xAxis: {
                type: 'category', data: data.factors,
                axisLine: { lineStyle: { color: t.axisColor } },
                axisLabel: { color: t.textColor, fontSize: 10, rotate: 45 },
                splitLine: { show: false }
            },
            yAxis: {
                type: 'value', name: 'IC值',
                nameTextStyle: { color: t.textColor },
                axisLine: { show: false },
                axisTick: { show: false },
                axisLabel: { color: t.textColor, fontSize: 11, fontFamily: "'JetBrains Mono', monospace" },
                splitLine: { lineStyle: { color: t.gridBorderColor, type: 'dashed', width: 0.5 } }
            },
            series: [{
                name: 'IC均值', type: 'bar',
                data: data.ic_mean.map((v) => ({
                    value: v,
                    itemStyle: {
                        color: v >= 0 ? 'rgba(255,23,68,0.75)' : 'rgba(0,230,118,0.75)',
                        borderRadius: v >= 0 ? [4, 4, 0, 0] : [0, 0, 4, 4]
                    }
                })),
                barMaxWidth: 40,
                label: {
                    show: true, position: 'top',
                    color: t.textColor, fontSize: 10,
                    fontFamily: "'JetBrains Mono', monospace",
                    formatter: '{c}'
                }
            }]
        };

        chart.setOption(option);
    },

    /**
     * 分层累计收益曲线图
     */
    renderQuintileCurve(domId, data) {
        const chart = this.getOrCreate(domId);
        if (!chart || !data || !data.dates) return;
        const t = this.theme;

        const colors = ['#3b82f6', '#00e676', '#f59e0b', '#a855f7', '#ff1744', '#06b6d4'];
        const series = [];
        const legendData = [];

        const groupNames = Object.keys(data.groups);
        groupNames.forEach((name, idx) => {
            series.push({
                name: name, type: 'line',
                data: data.groups[name], smooth: true, symbol: 'none',
                lineStyle: { width: 1.5, color: colors[idx % colors.length] }
            });
            legendData.push(name);
        });

        if (data.long_short && data.long_short.length > 0) {
            series.push({
                name: '多空(Q1-Q5)', type: 'line',
                data: data.long_short, smooth: true, symbol: 'none',
                lineStyle: { width: 2.5, color: '#00e676', type: 'dashed' },
                areaStyle: {
                    color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                        { offset: 0, color: 'rgba(0,230,118,0.15)' },
                        { offset: 1, color: 'rgba(0,230,118,0.01)' }
                    ])
                }
            });
            legendData.push('多空(Q1-Q5)');
        }

        const option = {
            backgroundColor: t.bgColor,
            tooltip: {
                trigger: 'axis',
                backgroundColor: t.tooltipBg,
                borderColor: t.tooltipBorder,
                borderWidth: 1,
                textStyle: { color: '#e2e8f0', fontSize: 13 }
            },
            legend: {
                data: legendData,
                textStyle: { color: t.textColor, fontSize: 12 },
                top: 8, right: 20
            },
            grid: { left: '60', right: '40', top: '50', bottom: '60' },
            xAxis: {
                type: 'category', data: data.dates,
                axisLine: { lineStyle: { color: t.axisColor } },
                axisLabel: { color: t.textColor, fontSize: 11 },
                splitLine: { show: false }
            },
            yAxis: {
                type: 'value', name: '累计收益(%)',
                nameTextStyle: { color: t.textColor },
                axisLine: { show: false },
                axisTick: { show: false },
                axisLabel: { color: t.textColor, fontSize: 11, formatter: '{value}%', fontFamily: "'JetBrains Mono', monospace" },
                splitLine: { lineStyle: { color: t.gridBorderColor, type: 'dashed', width: 0.5 } }
            },
            dataZoom: [
                { type: 'inside' },
                { type: 'slider', bottom: 8, height: 18, borderColor: t.gridBorderColor, backgroundColor: 'rgba(0,0,0,0.1)', fillerColor: 'rgba(59,130,246,0.1)' }
            ],
            series: series
        };

        chart.setOption(option);
    },

    /**
     * 因子相关性热力图
     */
    renderCorrHeatmap(domId, data) {
        const chart = this.getOrCreate(domId);
        if (!chart || !data || !data.factors || data.factors.length === 0) return;
        const t = this.theme;

        const factors = data.factors;
        const matrixData = data.matrix;

        const option = {
            backgroundColor: t.bgColor,
            tooltip: {
                backgroundColor: t.tooltipBg,
                borderColor: t.tooltipBorder,
                borderWidth: 1,
                textStyle: { color: '#e2e8f0', fontSize: 13 },
                formatter: function (params) {
                    const [x, y, val] = params.data;
                    return `<div style="font-weight:600;margin-bottom:4px;">${factors[x]} × ${factors[y]}</div>
                            相关系数: <b style="color:${val >= 0 ? '#ff1744' : '#00e676'};font-family:'JetBrains Mono',monospace;">${val}</b>`;
                }
            },
            grid: { left: '80', right: '80', top: '50', bottom: '80' },
            xAxis: {
                type: 'category', data: factors,
                axisLine: { lineStyle: { color: t.axisColor } },
                axisLabel: { color: t.textColor, fontSize: 10, rotate: 45 },
                splitLine: { show: false },
                splitArea: { show: true, areaStyle: { color: ['rgba(255,255,255,0.01)', 'rgba(0,0,0,0.01)'] } }
            },
            yAxis: {
                type: 'category', data: factors,
                axisLine: { show: false },
                axisTick: { show: false },
                axisLabel: { color: t.textColor, fontSize: 10 },
                splitLine: { show: false },
                splitArea: { show: true, areaStyle: { color: ['rgba(255,255,255,0.01)', 'rgba(0,0,0,0.01)'] } }
            },
            visualMap: {
                min: -1, max: 1,
                calculable: true, orient: 'horizontal',
                left: 'center', bottom: 0,
                inRange: { color: ['#00e676', '#111827', '#ff1744'] },
                textStyle: { color: t.textColor }
            },
            series: [{
                name: '相关系数', type: 'heatmap',
                data: matrixData,
                label: {
                    show: factors.length <= 15,
                    color: '#e2e8f0', fontSize: 9,
                    fontFamily: "'JetBrains Mono', monospace",
                    formatter: function (params) { return params.data[2].toFixed(2); }
                },
                emphasis: {
                    itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0,0,0,0.5)' }
                }
            }]
        };

        chart.setOption(option);
    },

    /**
     * 因子衰减曲线
     */
    renderDecayCurve(domId, data) {
        const chart = this.getOrCreate(domId);
        if (!chart || !data || !data.factors || data.factors.length === 0) return;
        const t = this.theme;

        const colors = ['#3b82f6', '#00e676', '#f59e0b', '#a855f7', '#ff1744', '#06b6d4', '#ec4899', '#14b8a6'];
        const series = [];

        data.factors.forEach((factor, idx) => {
            series.push({
                name: factor, type: 'line',
                data: data.decay_data[factor], smooth: true,
                symbol: 'circle', symbolSize: 7,
                lineStyle: { width: 2, color: colors[idx % colors.length] },
                itemStyle: { color: colors[idx % colors.length] }
            });
        });

        const option = {
            backgroundColor: t.bgColor,
            tooltip: {
                trigger: 'axis',
                backgroundColor: t.tooltipBg,
                borderColor: t.tooltipBorder,
                borderWidth: 1,
                textStyle: { color: '#e2e8f0', fontSize: 13 }
            },
            legend: {
                data: data.factors,
                textStyle: { color: t.textColor, fontSize: 12 },
                top: 8, right: 20
            },
            grid: { left: '60', right: '40', top: '50', bottom: '40' },
            xAxis: {
                type: 'category',
                data: data.holding_periods.map(d => d + '天'),
                axisLine: { lineStyle: { color: t.axisColor } },
                axisLabel: { color: t.textColor, fontSize: 12 },
                splitLine: { show: false }
            },
            yAxis: {
                type: 'value', name: 'IC均值',
                nameTextStyle: { color: t.textColor },
                axisLine: { show: false },
                axisTick: { show: false },
                axisLabel: { color: t.textColor, fontSize: 11, fontFamily: "'JetBrains Mono', monospace" },
                splitLine: { lineStyle: { color: t.gridBorderColor, type: 'dashed', width: 0.5 } }
            },
            series: series
        };

        chart.setOption(option);
    }
};
