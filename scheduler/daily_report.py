"""
每日报告生成器
生成包含：推荐股票、持仓监控、市场概览的HTML报告
"""
import logging
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("quant.scheduler.report")

ROOT = Path(__file__).parent.parent
REPORTS_DIR = ROOT / "reports"


class DailyReporter:
    """
    每日选股报告生成器

    报告内容：
    - 今日推荐股票（代码/名称/策略/信号强度/目标价/止损价）
    - 持仓监控报告（代码/买入价/现价/盈亏/持有天数/风控建议）
    - 市场概览（三大指数涨跌/涨跌家数/成交额）
    - 报告保存为HTML格式到 reports/ 目录
    """

    def __init__(self):
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    def generate_report(self, date: str = None) -> dict:
        """
        生成每日报告

        Args:
            date: 报告日期，默认今天

        Returns:
            包含报告数据和HTML路径的字典
        """
        report_date = date or datetime.now().strftime("%Y-%m-%d")
        log.info("开始生成每日报告，日期: %s", report_date)

        # 1. 获取今日推荐股票
        picks = self._get_today_picks(report_date)

        # 2. 获取持仓监控数据
        holdings = self._get_holdings_report(report_date)

        # 3. 获取市场概览
        market = self._get_market_overview()

        # 4. 获取今日信号
        signals = self._get_today_signals(report_date)

        # 5. 生成HTML报告
        report_data = {
            "date": report_date,
            "picks": picks,
            "holdings": holdings,
            "market": market,
            "signals": signals,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        html_path = self._generate_html_report(report_data)

        log.info("每日报告已生成: %s", html_path)
        return {
            "date": report_date,
            "html_path": str(html_path),
            "data": report_data,
        }

    def _get_today_picks(self, date: str) -> List[dict]:
        """获取今日推荐股票"""
        try:
            import sqlite3
            db_path = ROOT / "cache" / "scheduler.db"
            if not db_path.exists():
                return []

            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT symbol, name, strategy, signal_strength, price,
                       target_price, stop_loss, reasons
                FROM scheduler_picks
                WHERE date = ?
                ORDER BY signal_strength DESC
            """, (date,))

            picks = []
            for row in cursor.fetchall():
                r = dict(row)
                try:
                    r["reasons"] = json.loads(r.get("reasons", "[]"))
                except (json.JSONDecodeError, TypeError):
                    r["reasons"] = []
                picks.append(r)

            conn.close()
            return picks
        except Exception as e:
            log.warning("获取选股结果失败: %s", e)
            return []

    def _get_holdings_report(self, date: str) -> List[dict]:
        """
        获取持仓监控报告
        从历史选股和交易记录中推断持仓状态
        """
        holdings = []
        try:
            from data.provider import AShareDataProvider
            provider = AShareDataProvider()

            # 从最近的选股结果中获取持仓模拟数据
            import sqlite3
            db_path = ROOT / "cache" / "scheduler.db"
            if not db_path.exists():
                return []

            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 获取最近5天的选股结果（模拟持仓）
            cursor.execute("""
                SELECT DISTINCT symbol, name, MIN(date) as buy_date, price as buy_price
                FROM scheduler_picks
                WHERE date >= date(?, '-7 days')
                GROUP BY symbol
                ORDER BY buy_date DESC
                LIMIT 10
            """, (date,))
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                return []

            # 获取现价
            for row in rows:
                sym = row["symbol"]
                buy_date = row["buy_date"]
                buy_price = row["buy_price"]

                try:
                    df = provider.get_stock_daily(sym)
                    if df.empty:
                        continue
                    current_price = float(df.iloc[-1]["close"])

                    # 计算盈亏
                    pnl = current_price - buy_price
                    pnl_pct = (pnl / buy_price) * 100 if buy_price > 0 else 0

                    # 持有天数
                    buy_dt = datetime.strptime(buy_date, "%Y-%m-%d")
                    hold_days = (datetime.now() - buy_dt).days

                    # 风控建议
                    risk_advice = self._get_risk_advice(pnl_pct, hold_days)

                    holdings.append({
                        "symbol": sym,
                        "name": row["name"],
                        "buy_date": buy_date,
                        "buy_price": round(buy_price, 2),
                        "current_price": round(current_price, 2),
                        "pnl": round(pnl, 2),
                        "pnl_pct": round(pnl_pct, 2),
                        "hold_days": hold_days,
                        "risk_advice": risk_advice,
                    })
                except Exception as e:
                    log.debug("获取 %s 持仓数据失败: %s", sym, e)

        except Exception as e:
            log.warning("获取持仓报告失败: %s", e)

        return holdings

    def _get_risk_advice(self, pnl_pct: float, hold_days: int) -> str:
        """根据盈亏和持有天数生成风控建议"""
        if pnl_pct <= -8:
            return "⛔ 止损信号：建议立即止损"
        elif pnl_pct <= -5:
            return "⚠️ 风险提示：接近止损线，注意观察"
        elif pnl_pct >= 20:
            return "🎯 止盈信号：建议分批止盈"
        elif pnl_pct >= 15:
            return "📈 盈利丰厚：可设置移动止盈"
        elif pnl_pct >= 5 and hold_days >= 10:
            return "✅ 持有建议：趋势良好，继续持有"
        elif hold_days >= 20 and pnl_pct < 3:
            return "⏰ 时间止损：持有时间较长，考虑换仓"
        else:
            return "📊 正常持有：继续观察"

    def _get_market_overview(self) -> dict:
        """获取市场概览（三大指数、涨跌家数、成交额）"""
        market = {
            "indices": [],
            "stats": {"up_count": 0, "down_count": 0, "flat_count": 0, "total_amount": 0},
        }

        try:
            from data.provider import AShareDataProvider
            provider = AShareDataProvider()

            # 获取三大指数数据
            index_codes = [
                ("000001", "上证指数"),
                ("399001", "深证成指"),
                ("399006", "创业板指"),
            ]

            for code, name in index_codes:
                try:
                    import akshare as ak
                    if code.startswith("000"):
                        df = ak.stock_zh_index_daily(symbol=f"sh{code}")
                    else:
                        df = ak.stock_zh_index_daily(symbol=f"sz{code}")

                    if not df.empty:
                        latest = df.iloc[-1]
                        prev = df.iloc[-2] if len(df) > 1 else df.iloc[-1]

                        close = float(latest["close"])
                        prev_close = float(prev["close"])
                        change_pct = ((close - prev_close) / prev_close) * 100

                        market["indices"].append({
                            "name": name,
                            "code": code,
                            "close": round(close, 2),
                            "change_pct": round(change_pct, 2),
                            "volume": int(latest.get("volume", 0)),
                        })
                except Exception as e:
                    log.debug("获取指数 %s 失败: %s", name, e)

            # 获取涨跌家数
            try:
                stock_list = provider.get_stock_list()
                if not stock_list.empty and "pct_change" in stock_list.columns:
                    pcts = stock_list["pct_change"].dropna()
                    market["stats"]["up_count"] = int((pcts > 0).sum())
                    market["stats"]["down_count"] = int((pcts < 0).sum())
                    market["stats"]["flat_count"] = int((pcts == 0).sum())

                if "amount" in stock_list.columns:
                    total_amount = stock_list["amount"].sum()
                    market["stats"]["total_amount"] = round(float(total_amount) / 1e8, 2)  # 转换为亿元
            except Exception as e:
                log.debug("获取涨跌家数失败: %s", e)

        except Exception as e:
            log.warning("获取市场概览失败: %s", e)

        return market

    def _get_today_signals(self, date: str) -> List[dict]:
        """获取今日扫描到的信号"""
        try:
            import sqlite3
            db_path = ROOT / "cache" / "scheduler.db"
            if not db_path.exists():
                return []

            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT symbol, signal_type, price, score, strategy, reason, time
                FROM scheduler_signals
                WHERE date = ?
                ORDER BY score DESC
                LIMIT 20
            """, (date,))

            signals = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return signals
        except Exception as e:
            log.warning("获取今日信号失败: %s", e)
            return []

    def _generate_html_report(self, data: dict) -> Path:
        """生成HTML格式报告"""
        date = data["date"]
        report_file = REPORTS_DIR / f"daily_report_{date}.html"

        # 构建推荐股票表格行
        picks_rows = ""
        for i, pick in enumerate(data.get("picks", []), 1):
            strength = pick.get("signal_strength", 0)
            strength_color = "#22c55e" if strength >= 0.7 else "#f59e0b" if strength >= 0.5 else "#ef4444"
            reasons = pick.get("reasons", [])
            reason_text = "; ".join(reasons[:3]) if reasons else "-"

            picks_rows += f"""
            <tr>
                <td>{i}</td>
                <td class="code">{pick.get('symbol', '')}</td>
                <td>{pick.get('name', '')}</td>
                <td>{pick.get('strategy', '-')}</td>
                <td><span class="strength" style="color:{strength_color}">{strength:.0%}</span></td>
                <td>¥{pick.get('price', 0):.2f}</td>
                <td class="up">¥{pick.get('target_price', 0):.2f}</td>
                <td class="down">¥{pick.get('stop_loss', 0):.2f}</td>
                <td class="reason">{reason_text}</td>
            </tr>"""

        # 构建持仓监控表格行
        holdings_rows = ""
        for h in data.get("holdings", []):
            pnl_class = "up" if h.get("pnl", 0) >= 0 else "down"
            pnl_sign = "+" if h.get("pnl", 0) >= 0 else ""
            risk = h.get("risk_advice", "")

            holdings_rows += f"""
            <tr>
                <td class="code">{h.get('symbol', '')}</td>
                <td>{h.get('name', '')}</td>
                <td>{h.get('buy_date', '')}</td>
                <td>¥{h.get('buy_price', 0):.2f}</td>
                <td>¥{h.get('current_price', 0):.2f}</td>
                <td class="{pnl_class}">{pnl_sign}¥{h.get('pnl', 0):.2f}</td>
                <td class="{pnl_class}">{pnl_sign}{h.get('pnl_pct', 0):.2f}%</td>
                <td>{h.get('hold_days', 0)}天</td>
                <td class="advice">{risk}</td>
            </tr>"""

        # 构建指数数据
        indices_html = ""
        for idx in data.get("market", {}).get("indices", []):
            change_class = "up" if idx.get("change_pct", 0) >= 0 else "down"
            sign = "+" if idx.get("change_pct", 0) >= 0 else ""
            indices_html += f"""
            <div class="index-card">
                <div class="index-name">{idx['name']}</div>
                <div class="index-value">{idx.get('close', 0):.2f}</div>
                <div class="{change_class}">{sign}{idx.get('change_pct', 0):.2f}%</div>
            </div>"""

        # 市场统计
        stats = data.get("market", {}).get("stats", {})

        # 今日信号表格
        signals_rows = ""
        for s in data.get("signals", []):
            sig_type = s.get("signal_type", "")
            sig_class = "up" if sig_type == "BUY" else "down" if sig_type == "SELL" else ""
            sig_label = "买入" if sig_type == "BUY" else "卖出" if sig_type == "SELL" else "持有"

            signals_rows += f"""
            <tr>
                <td>{s.get('time', '')}</td>
                <td class="code">{s.get('symbol', '')}</td>
                <td><span class="tag {sig_class}">{sig_label}</span></td>
                <td>¥{s.get('price', 0):.2f}</td>
                <td>{s.get('score', 0):.2f}</td>
                <td>{s.get('strategy', '')}</td>
                <td>{s.get('reason', '')}</td>
            </tr>"""

        # 生成HTML
        html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>每日量化报告 - {date}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: -apple-system, 'Microsoft YaHei', 'PingFang SC', sans-serif;
    background: #0a0e17; color: #e2e8f0; padding: 20px; line-height: 1.6;
}}
.container {{ max-width: 1200px; margin: 0 auto; }}
h1 {{
    text-align: center; font-size: 24px; margin-bottom: 8px;
    background: linear-gradient(90deg, #3b82f6, #a855f7);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}}
.subtitle {{ text-align: center; color: #64748b; font-size: 13px; margin-bottom: 24px; }}
.section {{
    background: #111827; border: 1px solid #1e293b; border-radius: 10px;
    padding: 20px; margin-bottom: 20px;
}}
.section h2 {{
    font-size: 16px; color: #60a5fa; margin-bottom: 14px;
    padding-bottom: 8px; border-bottom: 1px solid #1e293b;
}}
.section h2 .icon {{ margin-right: 6px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
th {{
    background: #1a2332; padding: 8px 10px; text-align: left;
    color: #94a3b8; font-weight: 500; position: sticky; top: 0;
}}
td {{ padding: 7px 10px; border-bottom: 1px solid #1e293b; }}
tr:hover {{ background: rgba(59,130,246,.04); }}
.code {{ font-family: 'Consolas', monospace; color: #60a5fa; }}
.up {{ color: #22c55e; }}
.down {{ color: #ef4444; }}
.strength {{ font-weight: 700; }}
.reason {{ font-size: 11px; color: #94a3b8; max-width: 200px; }}
.advice {{ font-size: 11px; }}
.tag {{
    display: inline-block; padding: 2px 8px; border-radius: 4px;
    font-size: 10px; font-weight: 600;
}}
.tag.up {{ background: rgba(34,197,94,.15); color: #22c55e; }}
.tag.down {{ background: rgba(239,68,68,.15); color: #ef4444; }}

.indices {{ display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }}
.index-card {{
    flex: 1; min-width: 200px; background: #0f172a;
    border: 1px solid #1e293b; border-radius: 8px;
    padding: 14px; text-align: center;
}}
.index-name {{ font-size: 12px; color: #94a3b8; margin-bottom: 4px; }}
.index-value {{ font-size: 20px; font-weight: 700; margin-bottom: 2px; }}

.stats-row {{
    display: flex; gap: 12px; flex-wrap: wrap; margin-top: 12px;
}}
.stat-item {{
    flex: 1; min-width: 120px; background: #0f172a;
    border: 1px solid #1e293b; border-radius: 6px;
    padding: 10px; text-align: center;
}}
.stat-label {{ font-size: 11px; color: #64748b; }}
.stat-value {{ font-size: 16px; font-weight: 700; margin-top: 2px; }}

.footer {{
    text-align: center; color: #475569; font-size: 10px;
    margin-top: 30px; padding-top: 12px; border-top: 1px solid #1e293b;
}}
@media (max-width: 768px) {{
    .indices {{ flex-direction: column; }}
    .stats-row {{ flex-direction: column; }}
    table {{ font-size: 11px; }}
    td, th {{ padding: 5px 6px; }}
}}
</style>
</head>
<body>
<div class="container">
<h1>📊 每日量化交易报告</h1>
<div class="subtitle">{date} | 生成时间: {data.get('generated_at', '')}</div>

<!-- 市场概览 -->
<div class="section">
    <h2><span class="icon">🏦</span>市场概览</h2>
    <div class="indices">{indices_html if indices_html else '<div class="index-card"><div class="index-name">暂无指数数据</div></div>'}</div>
    <div class="stats-row">
        <div class="stat-item">
            <div class="stat-label">上涨家数</div>
            <div class="stat-value up">{stats.get('up_count', 0)}</div>
        </div>
        <div class="stat-item">
            <div class="stat-label">下跌家数</div>
            <div class="stat-value down">{stats.get('down_count', 0)}</div>
        </div>
        <div class="stat-item">
            <div class="stat-label">平盘家数</div>
            <div class="stat-value">{stats.get('flat_count', 0)}</div>
        </div>
        <div class="stat-item">
            <div class="stat-label">总成交额</div>
            <div class="stat-value">{stats.get('total_amount', 0):.0f}亿</div>
        </div>
    </div>
</div>

<!-- 今日推荐 -->
<div class="section">
    <h2><span class="icon">🎯</span>今日推荐股票 ({len(data.get('picks', []))}只)</h2>
    <div style="overflow-x:auto">
    <table>
        <thead><tr>
            <th>#</th><th>代码</th><th>名称</th><th>策略</th><th>信号强度</th>
            <th>当前价</th><th>目标价</th><th>止损价</th><th>推荐理由</th>
        </tr></thead>
        <tbody>{picks_rows if picks_rows else '<tr><td colspan="9" style="text-align:center;color:#64748b">暂无推荐数据（选股任务将在交易日09:00执行）</td></tr>'}</tbody>
    </table>
    </div>
</div>

<!-- 持仓监控 -->
<div class="section">
    <h2><span class="icon">💼</span>持仓监控 ({len(data.get('holdings', []))}只)</h2>
    <div style="overflow-x:auto">
    <table>
        <thead><tr>
            <th>代码</th><th>名称</th><th>买入日期</th><th>买入价</th>
            <th>现价</th><th>盈亏</th><th>盈亏%</th><th>持有天数</th><th>风控建议</th>
        </tr></thead>
        <tbody>{holdings_rows if holdings_rows else '<tr><td colspan="9" style="text-align:center;color:#64748b">暂无持仓数据</td></tr>'}</tbody>
    </table>
    </div>
</div>

<!-- 今日信号 -->
<div class="section">
    <h2><span class="icon">📡</span>今日扫描信号 ({len(data.get('signals', []))}个)</h2>
    <div style="overflow-x:auto">
    <table>
        <thead><tr>
            <th>时间</th><th>代码</th><th>信号</th><th>价格</th>
            <th>评分</th><th>策略</th><th>原因</th>
        </tr></thead>
        <tbody>{signals_rows if signals_rows else '<tr><td colspan="7" style="text-align:center;color:#64748b">暂无信号数据（盘中扫描将在交易日09:30-15:00执行）</td></tr>'}</tbody>
    </table>
    </div>
</div>

<div class="footer">
    A股量化交易系统 v2.0 | 本报告由定时任务自动生成，仅供参考，不构成投资建议<br>
    {data.get('generated_at', '')}
</div>
</div>
</body>
</html>"""

        report_file.write_text(html_content, encoding="utf-8")
        return report_file
