"""
测试 Web 服务新增端点: 回测报告/历史查询/WebSocket
"""
import pytest
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """创建测试客户端"""
    from web_server import app
    return TestClient(app)


@pytest.fixture
def auth_headers(client):
    """获取认证 headers"""
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "quant2026"})
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}


class TestBacktestReport:
    """回测报告端点测试"""

    @patch("web_server.get_data")
    @patch("web_server.create_strategy")
    def test_report_returns_html(self, mock_strategy, mock_data, client, auth_headers, mock_stock_df):
        mock_data.return_value = mock_stock_df
        mock_strategy.return_value = MagicMock()

        # 让策略返回空信号列表
        from strategy.engine import TradeSignal
        mock_strategy.return_value.run.return_value = []

        resp = client.get(
            "/api/backtest/report?symbol=600519&strategy=dual_ma&start=20250101&end=20260526",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        html = resp.text
        assert "回测报告" in html
        assert "600519" in html
        assert "权益曲线" in html
        assert "回撤曲线" in html
        assert "交易记录" in html

    def test_report_requires_auth(self, client):
        resp = client.get("/api/backtest/report?symbol=600519&strategy=dual_ma")
        assert resp.status_code == 401


class TestHistoryTrades:
    """历史交易记录查询测试"""

    def test_history_trades_empty(self, client):
        """无数据时返回空列表"""
        # 使用一个不会匹配的过滤条件
        resp = client.get("/api/history/trades?symbol=NONEXISTENT_XYZ_999")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_history_trades_with_limit(self, client):
        resp = client.get("/api/history/trades?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert len(data["data"]) <= 5

    def test_history_trades_response_format(self, client):
        resp = client.get("/api/history/trades")
        data = resp.json()
        assert "data" in data
        assert "count" in data


class TestHistoryPicks:
    """历史选股记录查询测试"""

    def test_history_picks_empty(self, client):
        resp = client.get("/api/history/picks?date=2099-01-01")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_history_picks_response_format(self, client):
        resp = client.get("/api/history/picks")
        data = resp.json()
        assert "data" in data
        assert "count" in data

    def test_history_picks_with_limit(self, client):
        resp = client.get("/api/history/picks?limit=3")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) <= 3


class TestWebSocketQuotes:
    """WebSocket 实时行情测试"""

    def test_ws_connect(self, client):
        """测试 WebSocket 能否建立连接"""
        with client.websocket_connect("/ws/quotes") as ws:
            # 连接成功后发送 ping
            ws.send_text("ping")
            data = ws.receive_json()
            assert data["type"] == "pong"

    def test_ws_connect_and_disconnect(self, client):
        """测试连接和断开不报错"""
        with client.websocket_connect("/ws/quotes") as ws:
            ws.send_text("ping")
            data = ws.receive_json()
            assert data["type"] == "pong"
        # 正常断开, 不应抛异常


class TestQuoteManager:
    """QuoteManager 单元测试"""

    def test_is_trading_time_weekday(self):
        from web_server import QuoteManager
        import datetime
        # 模拟周一 10:00
        with patch("web_server.datetime") as mock_dt:
            mock_dt.now.return_value = datetime.datetime(2025, 6, 2, 10, 0, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime.datetime(*a, **kw)
            assert QuoteManager.is_trading_time() is True

    def test_is_trading_time_weekend(self):
        from web_server import QuoteManager
        import datetime
        # 模拟周六
        with patch("web_server.datetime") as mock_dt:
            mock_dt.now.return_value = datetime.datetime(2025, 6, 7, 10, 0, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime.datetime(*a, **kw)
            assert QuoteManager.is_trading_time() is False

    def test_is_trading_time_lunch_break(self):
        from web_server import QuoteManager
        import datetime
        # 模拟周一 12:00 (午休)
        with patch("web_server.datetime") as mock_dt:
            mock_dt.now.return_value = datetime.datetime(2025, 6, 2, 12, 0, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime.datetime(*a, **kw)
            assert QuoteManager.is_trading_time() is False

    def test_is_trading_time_after_close(self):
        from web_server import QuoteManager
        import datetime
        # 模拟周一 16:00 (收盘后)
        with patch("web_server.datetime") as mock_dt:
            mock_dt.now.return_value = datetime.datetime(2025, 6, 2, 16, 0, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime.datetime(*a, **kw)
            assert QuoteManager.is_trading_time() is False


class TestExistingEndpointsStillWork:
    """确认原有端点不受影响"""

    def test_index_page(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_stock_hot(self, client):
        resp = client.get("/api/stock/hot")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 20

    def test_strategy_list(self, client):
        resp = client.get("/api/strategy/list")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 10

    def test_dashboard_summary(self, client):
        resp = client.get("/api/dashboard/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == "2.0"

    def test_auth_login(self, client):
        resp = client.post("/api/auth/login", json={"username": "admin", "password": "quant2026"})
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
