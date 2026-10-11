/**
 * QuantX Pro - KLineChart Professional K-Line Manager
 * 使用KLineChart库替换ECharts实现专业级K线图
 * 支持：分时图、日K、周K、月K切换
 * 支持：MA、BOLL、MACD、KDJ、RSI、VOL技术指标
 * 支持：买卖信号标注
 */
const KLineManager = {
    instances: {},
    currentSymbol: '',
    currentPeriod: '1d',

    // 颜色主题
    colors: {
        up: '#ff1744',           // 红涨
        down: '#00e676',         // 绿跌
        ma5: '#f59e0b',
        ma10: '#3b82f6',
        ma20: '#a855f7',
        ma60: '#06b6d4',
        bg: '#111827',
        gridColor: '#1e293b',
        textColor: '#94a3b8',
        crosshairColor: '#3a4a5a',
    },

    /**
     * 获取或创建KLineChart实例
     */
    getOrCreate(domId) {
        if (this.instances[domId]) {
            this.instances[domId].remove();
            this.instances[domId] = null;
        }
        const dom = document.getElementById(domId);
        if (!dom) return null;

        const chart = klinecharts.init(dom, {
            styles: {
                grid: {
                    show: true,
                    horizontal: {
                        show: true,
                        size: 1,
                        color: this.colors.gridColor,
                        style: 'dashed',
                    },
                    vertical: {
                        show: true,
                        size: 1,
                        color: this.colors.gridColor,
                        style: 'dashed',
                    },
                },
                candle: {
                    type: 'candle_solid',
                    bar: {
                        upColor: this.colors.up,
                        downColor: this.colors.down,
                        noChangeColor: '#94a3b8',
                        upBorderColor: this.colors.up,
                        downBorderColor: this.colors.down,
                        noChangeBorderColor: '#94a3b8',
                    },
                    area: {
                        lineColor: '#3b82f6',
                        lineSize: 1.5,
                        value: 'close',
                        backgroundColor: [{
                            offset: 0,
                            color: 'rgba(59,130,246,0.25)',
                        }, {
                            offset: 1,
                            color: 'rgba(59,130,246,0.01)',
                        }],
                    },
                    priceMark: {
                        show: true,
                        high: {
                            show: true,
                            color: this.colors.textColor,
                            textSize: 10,
                            textFamily: 'JetBrains Mono, monospace',
                        },
                        low: {
                            show: true,
                            color: this.colors.textColor,
                            textSize: 10,
                            textFamily: 'JetBrains Mono, monospace',
                        },
                        last: {
                            show: true,
                            upColor: this.colors.up,
                            downColor: this.colors.down,
                            noChangeColor: '#94a3b8',
                            line: {
                                show: true,
                                style: 'dashed',
                                size: 1,
                            },
                            text: {
                                show: true,
                                size: 11,
                                family: 'JetBrains Mono, monospace',
                                weight: 'bold',
                                paddingLeft: 4,
                                paddingRight: 4,
                                paddingTop: 2,
                                paddingBottom: 2,
                                borderRadius: 2,
                            },
                        },
                    },
                    tooltip: {
                        showRule: 'always',
                        showType: 'standard',
                        text: {
                            size: 12,
                            family: 'Inter, sans-serif',
                            weight: 'normal',
                            color: '#e2e8f0',
                            marginLeft: 8,
                            marginTop: 6,
                            marginRight: 8,
                            marginBottom: 0,
                        },
                    },
                },
                indicator: {
                    tooltip: {
                        showRule: 'always',
                        showType: 'standard',
                        text: {
                            size: 11,
                            family: 'JetBrains Mono, monospace',
                            weight: 'normal',
                            color: '#e2e8f0',
                            marginLeft: 8,
                            marginTop: 6,
                            marginRight: 8,
                            marginBottom: 0,
                        },
                    },
                },
                xAxis: {
                    show: true,
                    size: 'auto',
                    axisLine: {
                        show: true,
                        color: this.colors.gridColor,
                        size: 1,
                    },
                    tickText: {
                        show: true,
                        color: this.colors.textColor,
                        size: 10,
                        family: 'JetBrains Mono, monospace',
                    },
                    tickLine: {
                        show: true,
                        size: 1,
                        color: this.colors.gridColor,
                    },
                },
                yAxis: {
                    show: true,
                    size: 'auto',
                    position: 'right',
                    type: 'normal',
                    axisLine: {
                        show: true,
                        color: this.colors.gridColor,
                        size: 1,
                    },
                    tickText: {
                        show: true,
                        color: this.colors.textColor,
                        size: 10,
                        family: 'JetBrains Mono, monospace',
                    },
                    tickLine: {
                        show: true,
                        size: 1,
                        color: this.colors.gridColor,
                    },
                },
                separator: {
                    size: 1,
                    color: this.colors.gridColor,
                    fill: true,
                    activeBackgroundColor: 'rgba(59,130,246,0.15)',
                },
                crosshair: {
                    show: true,
                    horizontal: {
                        show: true,
                        line: {
                            show: true,
                            style: 'dashed',
                            size: 1,
                            color: this.colors.crosshairColor,
                        },
                        text: {
                            show: true,
                            color: '#e2e8f0',
                            size: 11,
                            family: 'JetBrains Mono, monospace',
                            weight: 'bold',
                            paddingLeft: 4,
                            paddingRight: 4,
                            paddingTop: 2,
                            paddingBottom: 2,
                            borderRadius: 2,
                            backgroundColor: '#1e293b',
                        },
                    },
                    vertical: {
                        show: true,
                        line: {
                            show: true,
                            style: 'dashed',
                            size: 1,
                            color: this.colors.crosshairColor,
                        },
                        text: {
                            show: true,
                            color: '#e2e8f0',
                            size: 11,
                            family: 'JetBrains Mono, monospace',
                            weight: 'bold',
                            paddingLeft: 4,
                            paddingRight: 4,
                            paddingTop: 2,
                            paddingBottom: 2,
                            borderRadius: 2,
                            backgroundColor: '#1e293b',
                        },
                    },
                },
            },
        });

        this.instances[domId] = chart;
        window.addEventListener('resize', () => {
            if (this.instances[domId]) {
                this.instances[domId].resize();
            }
        });

        return chart;
    },

    /**
     * 将后端K线数据转换为KLineChart格式
     */
    convertData(data) {
        if (!data || !Array.isArray(data)) return [];
        return data.map(d => {
            // 支持多种日期格式
            let timestamp;
            if (d.timestamp) {
                timestamp = typeof d.timestamp === 'number' ? d.timestamp : new Date(d.timestamp).getTime();
            } else if (d.date) {
                timestamp = new Date(d.date).getTime();
            } else {
                return null;
            }
            return {
                timestamp,
                open: Number(d.open),
                high: Number(d.high),
                low: Number(d.low),
                close: Number(d.close),
                volume: Number(d.volume || 0),
                turnover: Number(d.amount || d.turnover || 0),
            };
        }).filter(Boolean).sort((a, b) => a.timestamp - b.timestamp);
    },

    /**
     * 渲染专业K线图
     * @param {string} domId - DOM容器ID
     * @param {Array} data - K线数据
     * @param {Array} signals - 买卖信号
     * @param {Array} mainIndicators - 主图指标 ['MA', 'BOLL']
     * @param {Array} subIndicators - 副图指标 ['MACD', 'KDJ', 'RSI', 'VOL']
     */
    renderKline(domId, data, signals = [], mainIndicators = ['MA'], subIndicators = ['VOL']) {
        const chart = this.getOrCreate(domId);
        if (!chart || !data || data.length === 0) return;

        const klineData = this.convertData(data);
        if (klineData.length === 0) return;

        // 设置数据
        chart.applyNewData(klineData);

        // 添加主图指标
        mainIndicators.forEach(indicator => {
            this.addMainIndicator(chart, indicator);
        });

        // 添加副图指标
        subIndicators.forEach(indicator => {
            this.addSubIndicator(chart, indicator);
        });

        // 添加买卖信号标记
        if (signals && signals.length > 0) {
            this.addTradeSignals(chart, data, signals);
        }

        // 保存当前状态
        this.currentData = data;
        this.currentSignals = signals;

        return chart;
    },

    /**
     * 添加主图指标（叠加在K线上）
     */
    addMainIndicator(chart, name) {
        if (!chart) return;
        try {
            switch (name) {
                case 'MA':
                    chart.createIndicator('MA', false, { id: 'candle_pane' });
                    break;
                case 'BOLL':
                    chart.createIndicator('BOLL', false, { id: 'candle_pane' });
                    break;
            }
        } catch (e) {
            console.warn('添加主图指标失败:', name, e);
        }
    },

    /**
     * 添加副图指标（独立窗口）
     */
    addSubIndicator(chart, name) {
        if (!chart) return;
        try {
            chart.createIndicator(name);
        } catch (e) {
            console.warn('添加副图指标失败:', name, e);
        }
    },

    /**
     * 移除指标
     */
    removeAllIndicators(chart) {
        if (!chart) return;
        try {
            const paneIds = chart.getPaneIds ? chart.getPaneIds() : [];
            // 移除所有非主窗口的指标
            const indicators = chart.getIndicators ? chart.getIndicators() : [];
            indicators.forEach(ind => {
                try {
                    chart.removeIndicator(ind.paneId || 'candle_pane', ind.name);
                } catch (e) { /* ignore */ }
            });
        } catch (e) {
            console.warn('移除指标失败:', e);
        }
    },

    /**
     * 添加买卖信号标注
     */
    addTradeSignals(chart, rawData, signals) {
        if (!chart || !signals || signals.length === 0) return;

        // 自定义覆盖层 - 买卖信号标记
        klinecharts.registerOverlay({
            name: 'buySignal',
            draw: ({ ctx, data, barSpace, visibleRange, xAxis, yAxis }) => {
                const { from, to } = visibleRange;
                const kLineDataList = chart.getDataList();
                for (let i = from; i < to; i++) {
                    const kLineData = kLineDataList[i];
                    if (!kLineData) continue;
                    const signal = data.find(s => {
                        const ts = typeof s.timestamp === 'number' ? s.timestamp : new Date(s.date).getTime();
                        return Math.abs(ts - kLineData.timestamp) < 86400000 * 2;
                    });
                    if (!signal) continue;
                    const x = xAxis.convertToPixel(i);
                    const y = yAxis.convertToPixel(kLineData.low * 0.995);
                    // 画三角形
                    ctx.beginPath();
                    ctx.moveTo(x, y - 12);
                    ctx.lineTo(x - 6, y);
                    ctx.lineTo(x + 6, y);
                    ctx.closePath();
                    ctx.fillStyle = '#ff1744';
                    ctx.fill();
                    // 画文字
                    ctx.font = '10px JetBrains Mono, monospace';
                    ctx.fillStyle = '#ff1744';
                    ctx.textAlign = 'center';
                    ctx.fillText('B', x, y - 14);
                }
            },
        });

        klinecharts.registerOverlay({
            name: 'sellSignal',
            draw: ({ ctx, data, barSpace, visibleRange, xAxis, yAxis }) => {
                const { from, to } = visibleRange;
                const kLineDataList = chart.getDataList();
                for (let i = from; i < to; i++) {
                    const kLineData = kLineDataList[i];
                    if (!kLineData) continue;
                    const signal = data.find(s => {
                        const ts = typeof s.timestamp === 'number' ? s.timestamp : new Date(s.date).getTime();
                        return Math.abs(ts - kLineData.timestamp) < 86400000 * 2;
                    });
                    if (!signal) continue;
                    const x = xAxis.convertToPixel(i);
                    const y = yAxis.convertToPixel(kLineData.high * 1.005);
                    // 画倒三角形
                    ctx.beginPath();
                    ctx.moveTo(x, y + 12);
                    ctx.lineTo(x - 6, y);
                    ctx.lineTo(x + 6, y);
                    ctx.closePath();
                    ctx.fillStyle = '#00e676';
                    ctx.fill();
                    // 画文字
                    ctx.font = '10px JetBrains Mono, monospace';
                    ctx.fillStyle = '#00e676';
                    ctx.textAlign = 'center';
                    ctx.fillText('S', x, y + 20);
                }
            },
        });

        const buySignals = signals
            .filter(s => s.type === 'BUY' || s.direction === 'BUY')
            .map(s => ({
                timestamp: typeof s.timestamp === 'number' ? s.timestamp : new Date(s.date).getTime(),
                ...s,
            }));

        const sellSignals = signals
            .filter(s => s.type === 'SELL' || s.direction === 'SELL')
            .map(s => ({
                timestamp: typeof s.timestamp === 'number' ? s.timestamp : new Date(s.date).getTime(),
                ...s,
            }));

        if (buySignals.length > 0) {
            chart.createOverlay({
                name: 'buySignal',
                points: buySignals.map(s => ({ timestamp: s.timestamp })),
                extendData: buySignals,
                styles: {
                    line: { style: 'solid', color: '#ff1744', size: 1 },
                },
            });
        }

        if (sellSignals.length > 0) {
            chart.createOverlay({
                name: 'sellSignal',
                points: sellSignals.map(s => ({ timestamp: s.timestamp })),
                extendData: sellSignals,
                styles: {
                    line: { style: 'solid', color: '#00e676', size: 1 },
                },
            });
        }
    },

    /**
     * 切换K线周期
     */
    switchPeriod(domId, data, signals, period, mainIndicators, subIndicators) {
        this.currentPeriod = period;
        // 重新渲染
        this.renderKline(domId, data, signals, mainIndicators, subIndicators);
    },

    /**
     * 清除所有指标并重新添加
     */
    refreshIndicators(domId, mainIndicators, subIndicators) {
        const chart = this.instances[domId];
        if (!chart) return;

        // 移除所有副图面板
        try {
            // KLineChart API: 获取所有面板，移除非主面板
            const panes = document.querySelectorAll(`#${domId} .klinecharts-pane`);
            // 通过重建来刷新
        } catch (e) { /* ignore */ }

        // 重新添加指标
        mainIndicators.forEach(ind => this.addMainIndicator(chart, ind));
        subIndicators.forEach(ind => this.addSubIndicator(chart, ind));
    },

    /**
     * 销毁实例
     */
    dispose(domId) {
        if (this.instances[domId]) {
            try {
                this.instances[domId].remove();
            } catch (e) {
                try {
                    klinecharts.dispose(document.getElementById(domId));
                } catch (e2) { /* ignore */ }
            }
            this.instances[domId] = null;
        }
    },

    /**
     * 渲染回测K线图（含买卖信号标注）
     */
    renderBacktestKline(domId, data, trades = []) {
        const signals = trades.map(t => ({
            date: t.date,
            type: t.type || t.direction,
            price: t.price,
            timestamp: new Date(t.date).getTime(),
        }));
        return this.renderKline(domId, data, signals, ['MA'], ['VOL', 'MACD']);
    },
};
