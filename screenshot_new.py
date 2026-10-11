"""快速截图验证新UI"""
import asyncio, os
from playwright.async_api import async_playwright

DIR = "G:/AI学习资料/quant-trading/screenshots"
os.makedirs(DIR, exist_ok=True)

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        
        # 首页 - 用domcontentloaded避免CDN加载超时
        await page.goto("http://localhost:8888/", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)
        await page.screenshot(path=f"{DIR}/new_01_login.png")
        print("✅ 登录页")
        
        # 登录
        try:
            await page.fill('input[placeholder*="用户名"]', 'admin')
            await page.fill('input[type="password"]', 'quant2026')
            await page.click('button:has-text("登 录")')
            await page.wait_for_timeout(4000)
            await page.screenshot(path=f"{DIR}/new_02_dashboard.png")
            print("✅ 仪表盘")
        except Exception as e:
            print(f"⚠️ 登录: {e}")
            await page.screenshot(path=f"{DIR}/new_02_error.png")
        
        # 各页面截图
        pages = [
            ("行情中心", "new_03_market.png"),
            ("策略管理", "new_04_strategy.png"),
            ("回测分析", "new_05_backtest.png"),
            ("模拟交易", "new_06_trading.png"),
            ("因子研究", "new_07_research.png"),
        ]
        for name, fname in pages:
            try:
                await page.click(f'text={name}')
                await page.wait_for_timeout(1500)
                await page.screenshot(path=f"{DIR}/{fname}")
                print(f"✅ {name}")
            except Exception as e:
                print(f"⚠️ {name}: {e}")
        
        await browser.close()
        print(f"\n🎉 截图保存在: {DIR}/")

asyncio.run(main())
