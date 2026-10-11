/**
 * QuantX Pro - API Client
 * 封装所有后端API调用
 */
const QuantAPI = {
    BASE_URL: '',  // 同域，无需前缀
    _token: localStorage.getItem('quant_token') || '',

    get token() { return this._token; },
    set token(val) {
        this._token = val;
        if (val) localStorage.setItem('quant_token', val);
        else localStorage.removeItem('quant_token');
    },

    async _request(method, url, body = null, needAuth = false) {
        const headers = { 'Content-Type': 'application/json' };
        if (needAuth && this._token) {
            headers['Authorization'] = 'Bearer ' + this._token;
        }
        const opts = { method, headers };
        if (body) opts.body = JSON.stringify(body);
        try {
            const resp = await fetch(this.BASE_URL + url, opts);
            if (resp.status === 401) {
                throw new Error('UNAUTHORIZED');
            }
            if (!resp.ok) {
                const errBody = await resp.json().catch(() => ({}));
                throw new Error(errBody.detail || errBody.message || `HTTP ${resp.status}`);
            }
            return await resp.json();
        } catch (e) {
            if (e.message === 'UNAUTHORIZED') {
                this.token = '';
                window.dispatchEvent(new CustomEvent('auth-expired'));
            }
            throw e;
        }
    },

    // Auth
    async login(username, password) {
        const data = await this._request('POST', '/api/auth/login', { username, password });
        if (data.token || data.access_token) {
            this.token = data.token || data.access_token;
        }
        return data;
    },

    // Stock
    async getStockList() {
        return this._request('GET', '/api/stock/list');
    },

    async getKline(symbol, start, end, period) {
        const params = new URLSearchParams({ symbol });
        if (start) params.set('start', start);
        if (end) params.set('end', end);
        if (period) params.set('period', period);
        return this._request('GET', '/api/stock/kline?' + params.toString());
    },

    async searchStock(q) {
        return this._request('GET', '/api/stock/search?q=' + encodeURIComponent(q));
    },

    // Strategy
    async getStrategyList() {
        return this._request('GET', '/api/strategy/list');
    },

    // Backtest
    async runBacktest(symbol, strategy, start, end) {
        return this._request('POST', '/api/backtest/run', { symbol, strategy, start, end }, true);
    },

    // Signals
    async getSignals() {
        return this._request('GET', '/api/scan/signals', null, true);
    },

    // Trading (Paper Trading)
    async startTrading(strategy, initialCapital) {
        return this._request('POST', '/api/trading/start', {
            strategy, initial_capital: initialCapital
        }, true);
    },

    async stopTrading() {
        return this._request('POST', '/api/trading/stop', null, true);
    },

    async getTradingStatus() {
        return this._request('GET', '/api/trading/status');
    },

    async getTradingPositions() {
        return this._request('GET', '/api/trading/positions');
    },

    async getTradingHistory(limit) {
        return this._request('GET', '/api/trading/history' + (limit ? '?limit=' + limit : ''));
    },

    async getTradingPnl() {
        return this._request('GET', '/api/trading/pnl');
    },

    // Factor Research
    async getFactorList() {
        return this._request('GET', '/api/research/factor-list');
    },

    async analyzeFactorIC(symbol, start, end, factors) {
        return this._request('POST', '/api/research/factor-ic', {
            symbol, start, end, factors
        });
    },

    async analyzeFactorQuintile(symbol, factor, start, end, holdingDays, nGroups) {
        return this._request('POST', '/api/research/factor-quintile', {
            symbol, factor, start, end,
            holding_days: holdingDays || 1,
            n_groups: nGroups || 5
        }, true);
    },

    async analyzeFactorCorrelation(symbol, start, end) {
        const params = new URLSearchParams({ symbol });
        if (start) params.set('start', start);
        if (end) params.set('end', end);
        return this._request('GET', '/api/research/factor-corr?' + params.toString());
    },

    async analyzeFactorDecay(symbol, start, end, factors, holdingPeriods) {
        return this._request('POST', '/api/research/factor-decay', {
            symbol, start, end, factors,
            holding_periods: holdingPeriods || [1, 5, 10, 20]
        }, true);
    },

    // Crypto
    async getCryptoList() {
        return this._request('GET', '/api/crypto/list');
    },

    async getCryptoKline(symbol, interval, limit) {
        const params = new URLSearchParams({ symbol });
        if (interval) params.set('interval', interval);
        if (limit) params.set('limit', limit);
        return this._request('GET', '/api/crypto/kline?' + params.toString());
    },

    async getCryptoTicker(symbol) {
        return this._request('GET', '/api/crypto/ticker?symbol=' + encodeURIComponent(symbol));
    },

    async getCryptoTickers() {
        return this._request('GET', '/api/crypto/tickers');
    },

    async getCryptoOrderbook(symbol, limit) {
        const params = new URLSearchParams({ symbol });
        if (limit) params.set('limit', limit);
        return this._request('GET', '/api/crypto/orderbook?' + params.toString());
    },

    async getCryptoFundingRate(symbol) {
        return this._request('GET', '/api/crypto/funding-rate?symbol=' + encodeURIComponent(symbol));
    },

    async scanCrypto(symbols, strategy, interval, limit) {
        return this._request('POST', '/api/crypto/scan', {
            symbols, strategy, interval, limit
        }, true);
    },

    async getCryptoTrades(symbol, limit) {
        const params = new URLSearchParams({ symbol });
        if (limit) params.set('limit', limit);
        return this._request('GET', '/api/crypto/trades?' + params.toString());
    }
};
