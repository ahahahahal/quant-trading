/**
 * QuantX Pro - Vue3 Main Application
 * Professional Quantitative Trading System
 */
const { createApp, ref, reactive, computed, onMounted, nextTick, watch } = Vue;

const app = createApp({
    setup() {
        // ---- Auth ----
        const token = ref(QuantAPI.token);
        const loginVisible = ref(!token.value);
        const loginLoading = ref(false);
        const loginForm = reactive({ username: 'admin', password: 'quant2026' });
        const showUserMenu = ref(false);

        async function handleLogin() {
            loginLoading.value = true;
            try {
                const data = await QuantAPI.login(loginForm.username, loginForm.password);
                token.value = QuantAPI.token;
                loginVisible.value = false;
                ElMessage.success('登录成功');
                initAfterLogin();
            } catch (e) {
                ElMessage.error('登录失败: ' + e.message);
            } finally {
                loginLoading.value = false;
            }
        }

        function logout() {
            QuantAPI.token = '';
            token.value = '';
            loginVisible.value = true;
        }

        window.addEventListener('auth-expired', () => {
            token.value = '';
            loginVisible.value = true;
            ElMessage.warning('登录已过期，请重新登录');
        });

        // ---- Navigation ----
        const currentPage = ref('dashboard');
        const pageTitleMap = {
            dashboard: '首页总览',
            market: '行情中心',
            strategy: '策略管理',
            backtest: '回测分析',
            trading: '模拟交易',
            research: '因子研究',
            crypto: '加密货币'
        };
        const pageTitle = computed(() => pageTitleMap[currentPage.value] || '');

        function handleMenuSelect(index) {
            currentPage.value = index;
            nextTick(() => {
                if (index === 'dashboard') {
                    renderMiniCharts();
                }
                if (index === 'market' && klineData.value.length > 0) {
                    ChartManager.renderKline('klineChartPro', klineData.value, klineSignals.value);
                }
                if (index === 'backtest' && backtestResult.value) {
                    renderBacktestCharts();
                }
                if (index === 'trading') {
                    loadTradingData();
                }
                if (index === 'research') {
                    initResearchPage();
                }
                if (index === 'crypto') {
                    initCryptoPage();
                }
            });
        }

        // ---- Search (带防抖) ----
        const searchQuery = ref('');
        let _searchTimer = null;
        function handleSearch() {
            if (!searchQuery.value.trim()) return;
            currentPage.value = 'market';
            nextTick(() => {
                searchStock(searchQuery.value.trim());
            });
        }
        // 防抖搜索：输入时不立即请求，等300ms停顿后再请求
        function handleSearchInput(val) {
            if (_searchTimer) clearTimeout(_searchTimer);
            if (!val || !val.trim()) {
                stockSearchResults.value = [];
                return;
            }
            _searchTimer = setTimeout(() => {
                searchStock(val.trim());
            }, 300);
        }

        // ---- Market Data ----
        const stockList = ref([]);
        const hotStocks = ref([]);
        const marketOverview = ref([
            { title: '上证指数', value: '3,342.68', change: 0.82, sub: '上证A股综合指数' },
            { title: '深证成指', value: '10,856.33', change: 1.15, sub: '深交所综合指数' },
            { title: '创业板指', value: '2,178.45', change: -0.32, sub: '创业板综合指数' }
        ]);

        // Dashboard stats
        const dashboardStats = computed(() => {
            const ts = tradingStatus.value || {};
            const totalEquity = ts.total_equity || 1000000;
            const todayPnl = ts.today_pnl || 0;
            const marketValue = ts.market_value || 0;
            const totalReturn = ts.total_return_pct || 0;
            return [
                {
                    title: '总资产',
                    value: '¥' + formatMoney(totalEquity),
                    badge: totalReturn >= 0 ? '+' + totalReturn.toFixed(2) + '%' : totalReturn.toFixed(2) + '%',
                    badgeDir: totalReturn >= 0 ? '▲' : '▼',
                    badgeClass: totalReturn >= 0 ? 'up' : 'down',
                    color: 'var(--text-primary)',
                    sub: '账户总权益'
                },
                {
                    title: '今日盈亏',
                    value: (todayPnl >= 0 ? '+¥' : '-¥') + formatMoney(Math.abs(todayPnl)),
                    badge: null,
                    color: todayPnl >= 0 ? 'var(--up)' : 'var(--down)',
                    sub: '当日浮动盈亏'
                },
                {
                    title: '持仓市值',
                    value: '¥' + formatMoney(marketValue),
                    badge: null,
                    color: 'var(--text-primary)',
                    sub: '当前持仓总市值'
                },
                {
                    title: '总收益率',
                    value: (totalReturn >= 0 ? '+' : '') + totalReturn.toFixed(2) + '%',
                    badge: null,
                    color: totalReturn >= 0 ? 'var(--up)' : 'var(--down)',
                    sub: '账户累计收益率'
                }
            ];
        });

        // Mini chart for indices
        function renderMiniCharts() {
            const miniData = [
                [3300, 3310, 3295, 3320, 3335, 3328, 3342, 3350, 3338, 3345, 3342],
                [10700, 10750, 10720, 10800, 10830, 10810, 10856, 10870, 10840, 10860, 10856],
                [2200, 2190, 2195, 2185, 2180, 2190, 2178, 2185, 2175, 2180, 2178]
            ];
            const colors = ['#ff1744', '#ff1744', '#00e676'];
            for (let i = 0; i < 3; i++) {
                const domId = 'miniChart' + i;
                const dom = document.getElementById(domId);
                if (!dom) continue;
                if (ChartManager.instances[domId]) {
                    ChartManager.instances[domId].dispose();
                }
                const chart = echarts.init(dom);
                ChartManager.instances[domId] = chart;
                chart.setOption({
                    backgroundColor: 'transparent',
                    grid: { left: 0, right: 0, top: 0, bottom: 0 },
                    xAxis: { type: 'category', show: false, data: miniData[i].map((_, idx) => idx) },
                    yAxis: { type: 'value', show: false, min: 'dataMin', max: 'dataMax' },
                    series: [{
                        type: 'line',
                        data: miniData[i],
                        smooth: true,
                        symbol: 'none',
                        lineStyle: { width: 1.5, color: colors[i] },
                        areaStyle: {
                            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                                { offset: 0, color: colors[i].replace(')', ',0.15)').replace('rgb', 'rgba') },
                                { offset: 1, color: 'transparent' }
                            ])
                        }
                    }]
                });
                window.addEventListener('resize', () => chart.resize());
            }
        }

        // ---- KLine ----
        const selectedStock = ref('');
        const stockSearchResults = ref([]);
        const stockSearching = ref(false);
        const klineData = ref([]);
        const klineSignals = ref([]);
        const klineLoading = ref(false);
        const klineDateRange = ref([]);
        const klinePeriod = ref('1d');
        const activeMainIndicators = ref(['MA']);
        const activeSubIndicators = ref(['VOL']);
        const stockDetail = reactive({
            symbol: '', name: '', price: 0, change: 0,
            open: 0, high: 0, low: 0, close: 0, prev_close: 0,
            volume: 0, amount: 0, turnover_rate: 0, pe_ratio: 0, pb_ratio: 0
        });

        async function searchStock(query) {
            if (!query) return;
            stockSearching.value = true;
            try {
                const data = await QuantAPI.searchStock(query);
                stockSearchResults.value = Array.isArray(data) ? data : (data.data || data.results || []);
                if (stockSearchResults.value.length === 0 && stockList.value.length > 0) {
                    stockSearchResults.value = stockList.value.filter(s =>
                        s.symbol.toLowerCase().includes(query.toLowerCase()) ||
                        (s.name && s.name.includes(query))
                    ).slice(0, 20);
                }
            } catch (e) {
                console.error('搜索失败:', e);
                stockSearchResults.value = stockList.value.filter(s =>
                    s.symbol.toLowerCase().includes(query.toLowerCase()) ||
                    (s.name && s.name.includes(query))
                ).slice(0, 20);
            } finally {
                stockSearching.value = false;
            }
        }

        function onStockChange(symbol) {
            selectedStock.value = symbol;
            const now = new Date();
            const start = new Date(now);
            start.setMonth(start.getMonth() - 3);
            klineDateRange.value = [
                start.toISOString().slice(0, 10),
                now.toISOString().slice(0, 10)
            ];
            loadKline();
        }

        async function loadKline() {
            if (!selectedStock.value) return;
            klineLoading.value = true;
            const [start, end] = klineDateRange.value || [];
            try {
                const data = await QuantAPI.getKline(selectedStock.value, start, end, klinePeriod.value);
                klineData.value = Array.isArray(data) ? data : (data.data || []);

                // Update stock detail from last data point
                if (klineData.value.length > 0) {
                    const last = klineData.value[klineData.value.length - 1];
                    const found = stockList.value.find(s => s.symbol === selectedStock.value);
                    Object.assign(stockDetail, {
                        symbol: selectedStock.value,
                        name: found ? found.name : selectedStock.value,
                        price: last.close,
                        change: last.close && last.open ? ((last.close - last.open) / last.open * 100) : 0,
                        open: last.open, high: last.high, low: last.low, close: last.close,
                        prev_close: last.open,
                        volume: last.volume || 0, amount: last.amount || 0,
                        turnover_rate: last.turnover_rate || found?.turnover_rate || 0,
                        pe_ratio: last.pe_ratio || found?.pe_ratio || 0,
                        pb_ratio: last.pb_ratio || found?.pb_ratio || 0,
                    });
                }

                // 先渲染K线图，信号异步加载后叠加
                await nextTick();
                // 统一使用ECharts渲染（更稳定）
                ChartManager.renderKline('klineChartPro', klineData.value, []);

                // 异步加载信号（不阻塞K线图显示）
                QuantAPI.getSignals().then(sigs => {
                    const allSigs = Array.isArray(sigs) ? sigs : (sigs.data || sigs.signals || []);
                    klineSignals.value = allSigs.filter(s => s.symbol === selectedStock.value);
                    // 有信号时重新渲染叠加买卖标记
                    if (klineSignals.value.length > 0) {
                        ChartManager.renderKline('klineChartPro', klineData.value, klineSignals.value);
                    }
                }).catch(() => { klineSignals.value = []; });
            } catch (e) {
                ElMessage.error('加载K线失败: ' + e.message);
            } finally {
                klineLoading.value = false;
            }
        }

        function switchKlinePeriod(period) {
            klinePeriod.value = period;
            // 重新加载数据（不同周期需要不同数据）
            loadKline();
        }

        function toggleMainIndicator(name) {
            const idx = activeMainIndicators.value.indexOf(name);
            if (idx >= 0) {
                activeMainIndicators.value.splice(idx, 1);
            } else {
                activeMainIndicators.value.push(name);
            }
            // Re-render with ECharts
            if (klineData.value.length > 0) {
                ChartManager.renderKline('klineChartPro', klineData.value, klineSignals.value);
            }
        }

        function toggleSubIndicator(name) {
            const idx = activeSubIndicators.value.indexOf(name);
            if (idx >= 0) {
                activeSubIndicators.value.splice(idx, 1);
            } else {
                activeSubIndicators.value.push(name);
            }
            // Re-render with ECharts
            if (klineData.value.length > 0) {
                ChartManager.renderKline('klineChartPro', klineData.value, klineSignals.value);
            }
        }

        function setKlineRange(range) {
            const now = new Date();
            const start = new Date(now);
            switch (range) {
                case '1m': start.setMonth(start.getMonth() - 1); break;
                case '3m': start.setMonth(start.getMonth() - 3); break;
                case '6m': start.setMonth(start.getMonth() - 6); break;
                case '1y': start.setFullYear(start.getFullYear() - 1); break;
            }
            klineDateRange.value = [
                start.toISOString().slice(0, 10),
                now.toISOString().slice(0, 10)
            ];
            loadKline();
        }

        function viewKline(symbol) {
            selectedStock.value = symbol;
            currentPage.value = 'market';
            nextTick(() => onStockChange(symbol));
        }

        // ---- Today's Signals ----
        const todaySignals = ref([]);

        async function loadSignals() {
            try {
                const data = await QuantAPI.getSignals();
                const sigs = Array.isArray(data) ? data : (data.data || data.signals || []);
                todaySignals.value = sigs.slice(0, 20);
            } catch (e) {
                console.log('信号加载需要登录或无数据');
                todaySignals.value = [];
            }
        }

        // ---- Strategies ----
        const strategies = ref([]);
        const strategiesLoading = ref(false);

        async function loadStrategies() {
            strategiesLoading.value = true;
            try {
                const data = await QuantAPI.getStrategyList();
                strategies.value = Array.isArray(data) ? data : (data.data || []);
            } catch (e) {
                ElMessage.error('加载策略失败: ' + e.message);
            } finally {
                strategiesLoading.value = false;
            }
        }

        const strategyIcons = ['📊', '📈', '🧠', '⚡', '🔄', '📉', '🎯', '🔮', '💹', '🛡️'];

        function getStrategyIcon(index) {
            return strategyIcons[index % strategyIcons.length];
        }

        function viewStrategyDetail(strategy) {
            ElMessage.info('策略: ' + strategy.name + ' - ' + (strategy.description || '暂无描述'));
        }

        function quickBacktest(strategy) {
            backtestForm.strategy = strategy.name;
            currentPage.value = 'backtest';
            if (!backtestForm.symbol && stockList.value.length > 0) {
                backtestForm.symbol = stockList.value[0].symbol;
            }
            const now = new Date();
            const start = new Date(now);
            start.setFullYear(start.getFullYear() - 1);
            backtestForm.dateRange = [start.toISOString().slice(0, 10), now.toISOString().slice(0, 10)];
        }

        // ---- Backtest ----
        const backtestForm = reactive({
            symbol: '',
            strategy: '',
            dateRange: []
        });
        const backtestLoading = ref(false);
        const backtestResult = ref(null);

        const backtestMetrics = computed(() => {
            const r = backtestResult.value;
            if (!r) return [];
            const m = r.metrics || r.performance || r;
            return [
                { label: '总收益率', value: fmtPct(m.total_return ?? m.totalReturn ?? m.return_rate), class: (m.total_return ?? m.totalReturn ?? 0) >= 0 ? 'positive' : 'negative' },
                { label: '年化收益', value: fmtPct(m.annual_return ?? m.annualReturn ?? m.annualized_return), class: (m.annual_return ?? m.annualReturn ?? 0) >= 0 ? 'positive' : 'negative' },
                { label: '最大回撤', value: fmtPct(m.max_drawdown ?? m.maxDrawdown ?? m.max_dd), class: 'negative' },
                { label: '夏普比率', value: (m.sharpe_ratio ?? m.sharpe ?? 0).toFixed(2), class: 'neutral' },
                { label: '胜率', value: fmtPct(m.win_rate ?? m.winRate ?? m.winrate), class: 'neutral' },
                { label: '交易次数', value: String(m.total_trades ?? m.trades_count ?? m.totalTrades ?? (backtestResult.value.trades ? backtestResult.value.trades.length : 0)), class: 'neutral' }
            ];
        });

        async function runBacktest() {
            if (!backtestForm.symbol || !backtestForm.strategy) {
                ElMessage.warning('请选择股票和策略');
                return;
            }
            if (!token.value) {
                loginVisible.value = true;
                ElMessage.warning('请先登录');
                return;
            }
            backtestLoading.value = true;
            try {
                const [start, end] = backtestForm.dateRange || [];
                const result = await QuantAPI.runBacktest(backtestForm.symbol, backtestForm.strategy, start, end);
                backtestResult.value = result;
                ElMessage.success('回测完成');
                await nextTick();
                renderBacktestCharts();
            } catch (e) {
                ElMessage.error('回测失败: ' + e.message);
            } finally {
                backtestLoading.value = false;
            }
        }

        function renderBacktestCharts() {
            const r = backtestResult.value;
            if (!r) return;

            const equity = r.equity_curve || r.equityCurve || r.equity || [];
            const benchmark = r.benchmark_curve || r.benchmarkCurve || r.benchmark || null;
            const dates = r.dates || r.equity_dates || [];

            if (equity.length > 0 && dates.length > 0) {
                ChartManager.renderEquityCurve('equityChart', dates, equity, benchmark);
            }

            const drawdowns = r.drawdowns || r.drawdown_curve || r.drawdown || [];
            if (drawdowns.length > 0 && dates.length > 0) {
                ChartManager.renderDrawdown('drawdownChart', dates, drawdowns);
            }

            // Render backtest KLine with buy/sell signals using ECharts
            const btData = r.kline_data || r.klineData || r.data || klineData.value;
            const trades = r.trades || [];
            if (btData && btData.length > 0) {
                nextTick(() => {
                    ChartManager.renderKline('backtestKlineChart', btData, trades);
                });
            }
        }

        // ---- Helpers ----
        function formatPrice(val) {
            if (val === null || val === undefined) return '-';
            return Number(val).toFixed(2);
        }

        function formatMoney(val) {
            if (val === null || val === undefined) return '-';
            return Number(val).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        }

        function fmtPct(val) {
            if (val === null || val === undefined) return '-';
            const n = Number(val);
            if (isNaN(n)) return '-';
            if (Math.abs(n) < 1 && Math.abs(n) > 0) return (n * 100).toFixed(2) + '%';
            return n.toFixed(2) + '%';
        }

        // ---- Paper Trading ----
        const tradingForm = reactive({
            strategy: 'dual_ma',
            initialCapital: 1000000
        });
        const tradingStatus = ref({});
        const tradingPositions = ref([]);
        const tradingHistory = ref([]);
        const tradingPnlCurve = ref([]);
        const tradingLoading = ref(false);
        const tradingRefreshing = ref(false);

        // ---- Factor Research ----
        const researchForm = reactive({
            symbol: '',
            dateRange: [],
            selectedFactors: ['RSI14', 'MACD_HIST', 'BOLL_POS', 'ATR14', 'VOL20D'],
            quintileFactor: 'RSI14',
            holdingDays: 1
        });
        const factorList = ref([]);
        const factorICLoading = ref(false);
        const factorQuintileLoading = ref(false);
        const factorCorrLoading = ref(false);
        const factorDecayLoading = ref(false);
        const factorICResult = ref(null);
        const factorQuintileResult = ref(null);
        const factorCorrResult = ref(null);
        const factorDecayResult = ref(null);

        async function initResearchPage() {
            if (factorList.value.length === 0) {
                try {
                    const data = await QuantAPI.getFactorList();
                    factorList.value = data.data || [];
                } catch (e) {
                    console.log('加载因子列表失败:', e);
                }
            }
        }

        async function runFactorIC() {
            if (!researchForm.symbol) {
                ElMessage.warning('请输入股票代码');
                return;
            }
            if (!researchForm.selectedFactors.length) {
                ElMessage.warning('请选择至少一个因子');
                return;
            }
            const [start, end] = researchForm.dateRange || [];
            factorICLoading.value = true;
            try {
                const result = await QuantAPI.analyzeFactorIC(
                    researchForm.symbol, start, end, researchForm.selectedFactors
                );
                factorICResult.value = result.data;
                await nextTick();
                renderICChart(result.data);
            } catch (e) {
                ElMessage.error('IC分析失败: ' + e.message);
            } finally {
                factorICLoading.value = false;
            }
        }

        async function runFactorQuintile() {
            if (!researchForm.symbol) {
                ElMessage.warning('请输入股票代码');
                return;
            }
            if (!token.value) {
                loginVisible.value = true;
                ElMessage.warning('请先登录');
                return;
            }
            const [start, end] = researchForm.dateRange || [];
            factorQuintileLoading.value = true;
            try {
                const result = await QuantAPI.analyzeFactorQuintile(
                    researchForm.symbol, researchForm.quintileFactor,
                    start, end, researchForm.holdingDays
                );
                factorQuintileResult.value = result.data;
                await nextTick();
                renderQuintileChart(result.data);
            } catch (e) {
                ElMessage.error('分层回测失败: ' + e.message);
            } finally {
                factorQuintileLoading.value = false;
            }
        }

        async function runFactorCorrelation() {
            if (!researchForm.symbol) {
                ElMessage.warning('请输入股票代码');
                return;
            }
            const [start, end] = researchForm.dateRange || [];
            factorCorrLoading.value = true;
            try {
                const result = await QuantAPI.analyzeFactorCorrelation(
                    researchForm.symbol, start, end
                );
                factorCorrResult.value = result.data;
                await nextTick();
                renderCorrHeatmap(result.data);
            } catch (e) {
                ElMessage.error('相关性分析失败: ' + e.message);
            } finally {
                factorCorrLoading.value = false;
            }
        }

        async function runFactorDecay() {
            if (!researchForm.symbol) {
                ElMessage.warning('请输入股票代码');
                return;
            }
            if (!researchForm.selectedFactors.length) {
                ElMessage.warning('请选择至少一个因子');
                return;
            }
            if (!token.value) {
                loginVisible.value = true;
                ElMessage.warning('请先登录');
                return;
            }
            const [start, end] = researchForm.dateRange || [];
            factorDecayLoading.value = true;
            try {
                const result = await QuantAPI.analyzeFactorDecay(
                    researchForm.symbol, start, end,
                    researchForm.selectedFactors, [1, 5, 10, 20]
                );
                factorDecayResult.value = result.data;
                await nextTick();
                renderDecayChart(result.data);
            } catch (e) {
                ElMessage.error('衰减分析失败: ' + e.message);
            } finally {
                factorDecayLoading.value = false;
            }
        }

        function renderICChart(data) {
            ChartManager.renderICBar('icChart', data);
        }

        function renderQuintileChart(data) {
            ChartManager.renderQuintileCurve('quintileChart', data);
        }

        function renderCorrHeatmap(data) {
            ChartManager.renderCorrHeatmap('corrChart', data);
        }

        function renderDecayChart(data) {
            ChartManager.renderDecayCurve('decayChart', data);
        }

        // ---- Crypto ----
        const selectedCrypto = ref('');
        const cryptoPairs = ref([]);
        const cryptoTickers = ref([]);
        const cryptoTickersLoading = ref(false);
        const cryptoKlineData = ref([]);
        const cryptoKlineLoading = ref(false);
        const cryptoKlinePeriod = ref('1h');
        const cryptoTickerData = reactive({});
        const cryptoScanStrategy = ref('dual_ma');
        const cryptoScanInterval = ref('1d');
        const cryptoScanLoading = ref(false);
        const cryptoScanResults = ref([]);

        const cryptoTickerCards = computed(() => {
            const d = cryptoTickerData;
            if (!d.symbol) return [];
            return [
                { label: '最新价', value: '$' + formatCryptoPrice(d.last), color: (d.change||0) >= 0 ? 'var(--up)' : 'var(--down)' },
                { label: '涨跌幅', value: ((d.change||0) >= 0 ? '+' : '') + (d.change||0).toFixed(2) + '%', color: (d.change||0) >= 0 ? 'var(--up)' : 'var(--down)' },
                { label: '24h最高', value: '$' + formatCryptoPrice(d.high), color: 'var(--up)' },
                { label: '24h最低', value: '$' + formatCryptoPrice(d.low), color: 'var(--down)' },
                { label: '成交量', value: formatCryptoVol(d.volume), color: 'var(--text-primary)' },
                { label: '买一', value: '$' + formatCryptoPrice(d.bid), color: 'var(--text-secondary)' },
            ];
        });

        function formatCryptoPrice(p) {
            if (!p || p === 0) return '0.00';
            if (p >= 1000) return Number(p).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
            if (p >= 1) return Number(p).toFixed(2);
            if (p >= 0.01) return Number(p).toFixed(4);
            return Number(p).toFixed(8);
        }

        function formatCryptoVol(v) {
            if (!v || v === 0) return '0';
            if (v >= 1e9) return (v / 1e9).toFixed(2) + 'B';
            if (v >= 1e6) return (v / 1e6).toFixed(2) + 'M';
            if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K';
            return Number(v).toFixed(0);
        }

        async function initCryptoPage() {
            if (cryptoPairs.value.length === 0) {
                try {
                    const [pairsRes, tickersRes] = await Promise.all([
                        QuantAPI.getCryptoList(),
                        QuantAPI.getCryptoTickers()
                    ]);
                    cryptoPairs.value = pairsRes.data || [];
                    cryptoTickers.value = tickersRes.data || [];
                } catch (e) {
                    console.log('Crypto init error:', e);
                }
            }
        }

        async function loadCryptoTickers() {
            cryptoTickersLoading.value = true;
            try {
                const data = await QuantAPI.getCryptoTickers();
                cryptoTickers.value = data.data || [];
            } catch (e) {
                ElMessage.error('加载行情失败: ' + e.message);
            } finally {
                cryptoTickersLoading.value = false;
            }
        }

        async function selectCrypto(symbol) {
            selectedCrypto.value = symbol;
            cryptoKlineLoading.value = true;
            try {
                const [klineRes, tickerRes] = await Promise.all([
                    QuantAPI.getCryptoKline(symbol, cryptoKlinePeriod.value, 300),
                    QuantAPI.getCryptoTicker(symbol)
                ]);
                cryptoKlineData.value = klineRes.data || [];
                Object.assign(cryptoTickerData, tickerRes.data || {});

                await nextTick();
                if (cryptoKlineData.value.length > 0) {
                    ChartManager.renderKline('cryptoKlineChart', cryptoKlineData.value, []);
                }
            } catch (e) {
                ElMessage.error('加载K线失败: ' + e.message);
            } finally {
                cryptoKlineLoading.value = false;
            }
        }

        function switchCryptoPeriod(period) {
            cryptoKlinePeriod.value = period;
            if (selectedCrypto.value) {
                selectCrypto(selectedCrypto.value);
            }
        }

        async function runCryptoScan() {
            if (!token.value) {
                loginVisible.value = true;
                ElMessage.warning('请先登录');
                return;
            }
            cryptoScanLoading.value = true;
            try {
                const symbols = cryptoPairs.value.slice(0, 6).map(p => p.symbol);
                const result = await QuantAPI.scanCrypto(
                    symbols, cryptoScanStrategy.value, cryptoScanInterval.value, 200
                );
                cryptoScanResults.value = result.data || [];
                ElMessage.success('扫描完成, ' + cryptoScanResults.value.length + ' 个信号');
            } catch (e) {
                ElMessage.error('扫描失败: ' + e.message);
            } finally {
                cryptoScanLoading.value = false;
            }
        }

        async function startTrading() {
            if (!token.value) {
                loginVisible.value = true;
                ElMessage.warning('请先登录');
                return;
            }
            tradingLoading.value = true;
            try {
                await QuantAPI.startTrading(tradingForm.strategy, tradingForm.initialCapital);
                ElMessage.success('模拟交易已启动');
                await refreshTradingData();
            } catch (e) {
                ElMessage.error('启动失败: ' + e.message);
            } finally {
                tradingLoading.value = false;
            }
        }

        async function stopTrading() {
            tradingLoading.value = true;
            try {
                await QuantAPI.stopTrading();
                ElMessage.success('模拟交易已停止');
                await refreshTradingData();
            } catch (e) {
                ElMessage.error('停止失败: ' + e.message);
            } finally {
                tradingLoading.value = false;
            }
        }

        async function refreshTradingData() {
            tradingRefreshing.value = true;
            try {
                await loadTradingData();
            } finally {
                tradingRefreshing.value = false;
            }
        }

        async function loadTradingData() {
            try {
                const [statusRes, positionsRes, historyRes, pnlRes] = await Promise.all([
                    QuantAPI.getTradingStatus(),
                    QuantAPI.getTradingPositions(),
                    QuantAPI.getTradingHistory(200),
                    QuantAPI.getTradingPnl()
                ]);
                tradingStatus.value = statusRes.data || {};
                tradingPositions.value = positionsRes.data || [];
                tradingHistory.value = historyRes.data || [];
                tradingPnlCurve.value = pnlRes.data || [];

                await nextTick();
                if (tradingPnlCurve.value.length > 0) {
                    ChartManager.renderPnlCurve('pnlChart', tradingPnlCurve.value);
                }
            } catch (e) {
                console.log('加载交易数据:', e);
            }
        }

        async function loadHotStocks() {
            try {
                const data = await QuantAPI.getStockList();
                stockList.value = Array.isArray(data) ? data : (data.data || []);
                hotStocks.value = stockList.value.slice(0, 10).map(s => ({
                    ...s,
                    price: s.price || s.close || (Math.random() * 100 + 10).toFixed(2),
                    change: s.change || s.pct_change || (Math.random() * 10 - 5).toFixed(2)
                }));
            } catch (e) {
                console.log('加载热门股票失败:', e);
            }
        }

        // ---- Init ----
        async function initAfterLogin() {
            try {
                const data = await QuantAPI.getStockList();
                stockList.value = Array.isArray(data) ? data : (data.data || []);
                hotStocks.value = stockList.value.slice(0, 10).map(s => ({
                    ...s,
                    price: s.price || s.close || (Math.random() * 100 + 10).toFixed(2),
                    change: s.change || s.pct_change || (Math.random() * 10 - 5).toFixed(2)
                }));
            } catch (e) {
                console.log('Stock list load error:', e);
            }

            // 策略列表快速加载
            await loadStrategies();

            // 信号扫描改为异步，不阻塞页面
            loadSignals().catch(() => {});

            // Try loading trading data
            try {
                await loadTradingData();
            } catch (e) {
                console.log('Trading data not available yet');
            }

            // Render mini charts
            await nextTick();
            renderMiniCharts();
        }

        onMounted(() => {
            if (token.value) {
                initAfterLogin();
            }
            const now = new Date();
            const start = new Date(now);
            start.setMonth(start.getMonth() - 3);
            klineDateRange.value = [
                start.toISOString().slice(0, 10),
                now.toISOString().slice(0, 10)
            ];
        });

        return {
            // Auth
            token, loginVisible, loginLoading, loginForm, handleLogin, logout, showUserMenu,
            // Nav
            currentPage, pageTitle, handleMenuSelect,
            // Search
            searchQuery, handleSearch, handleSearchInput,
            // Market
            stockList, hotStocks, marketOverview, dashboardStats, loadHotStocks,
            // KLine
            selectedStock, stockSearchResults, stockSearching, searchStock,
            klineData, klineSignals, klineLoading, klineDateRange,
            klinePeriod, activeMainIndicators, activeSubIndicators, stockDetail,
            onStockChange, loadKline, setKlineRange, viewKline,
            switchKlinePeriod, toggleMainIndicator, toggleSubIndicator,
            // Signals
            todaySignals, loadSignals,
            // Strategy
            strategies, strategiesLoading, loadStrategies, quickBacktest,
            getStrategyIcon, viewStrategyDetail,
            // Backtest
            backtestForm, backtestLoading, backtestResult, backtestMetrics, runBacktest,
            // Trading (Paper Trading)
            tradingForm, tradingStatus, tradingPositions, tradingHistory, tradingPnlCurve,
            tradingLoading, tradingRefreshing,
            startTrading, stopTrading, refreshTradingData, loadTradingData,
            // Factor Research
            researchForm, factorList, factorICLoading, factorQuintileLoading,
            factorCorrLoading, factorDecayLoading, factorICResult, factorQuintileResult,
            factorCorrResult, factorDecayResult,
            runFactorIC, runFactorQuintile, runFactorCorrelation, runFactorDecay,
            // Helpers
            formatPrice, formatMoney
        };
    }
});

// Register Element Plus
app.use(ElementPlus, { locale: ElementPlusLocaleZhCn });

// Register all icons
const icons = ElementPlusIconsVue;
for (const [key, component] of Object.entries(icons)) {
    app.component(key, component);
}

app.mount('#app');
