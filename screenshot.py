"""快速截图验证"""
import asyncio, os
from playwright.async_api import async_playwright

DIR = "G:/AI学习资料/quant-trading/screenshots"
os.makedirs(DIR, exist_ok=True)

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1400, "height": 900})
        
        # 首页
        await page.goto("http://localhost:8888/", timeout=10000)
        await page.wait_for_timeout(2000)
        await page.screenshot(path=f"{DIR}/01_login.png")
        print("✅ 登录页")
        
        # 登录
        await page.fill('input[placeholder*="用户名"]', 'admin')
        await page.fill('input[type="password"]', 'quant2026')
        await page.click('button:has-text("登 录")')
        await page.wait_for_timeout(3000)
        await page.screenshot(path=f"{DIR}/02_dashboard.png")
        print("✅ 仪表盘")
        
        # 行情
        await page.click('text=行情中心')
        await page.wait_for_timeout(1000)
        await page.screenshot(path=f"{DIR}/03_market.png")
        print("✅ 行情中心")
        
        # 策略
        await page.click('text=策略管理')
        await page.wait_for_timeout(1000)
        await page.screenshot(path=f"{DIR}/04_strategy.png")
        print("✅ 策略管理")
        
        # 回测
        await page.click('text=回测分析')
        await page.wait_for_timeout(1000)
        await page.screenshot(path=f"{DIR}/05_backtest.png")
        print("✅ 回测分析")
        
        # 模拟交易
        await page.click('text=模拟交易')
        await page.wait_for_timeout(1000)
        await page.screenshot(path=f"{DIR}/06_trading.png")
        print("✅ 模拟交易")
        
        # 因子研究
        await page.click('text=因子研究')
        await page.wait_for_timeout(1000)
        await page.screenshot(path=f"{DIR}/07_research.png")
        print("✅ 因子研究")
        
        await browser.close()
        print(f"\n🎉 7张截图保存在: {DIR}/")

asyncio.run(main())
