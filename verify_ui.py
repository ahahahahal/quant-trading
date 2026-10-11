"""
UI验证脚本 - 使用Playwright截图验证量化交易系统界面
"""
import asyncio
import os
from playwright.async_api import async_playwright

SCREENSHOT_DIR = "G:/AI学习资料/quant-trading/screenshots"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1400, "height": 900})
        
        # 1. Visit homepage
        print("[1/5] 访问首页...")
        await page.goto("http://localhost:8888/", wait_until="networkidle", timeout=15000)
        await page.wait_for_timeout(2000)
        await page.screenshot(path=f"{SCREENSHOT_DIR}/01_login.png")
        print("  ✅ 登录页面截图完成")
        
        # 2. Login
        print("[2/5] 登录...")
        try:
            await page.fill('input[placeholder*="用户名"]', 'admin')
            await page.fill('input[type="password"]', 'quant2026')
            await page.click('button:has-text("登 录")')
            await page.wait_for_timeout(3000)
            await page.screenshot(path=f"{SCREENSHOT_DIR}/02_dashboard.png")
            print("  ✅ 首页仪表盘截图完成")
        except Exception as e:
            print(f"  ⚠️ 登录异常: {e}")
            await page.screenshot(path=f"{SCREENSHOT_DIR}/02_login_error.png")
        
        # 3. Market page
        print("[3/5] 行情中心...")
        try:
            await page.click('text=行情中心')
            await page.wait_for_timeout(1500)
            await page.screenshot(path=f"{SCREENSHOT_DIR}/03_market.png")
            print("  ✅ 行情中心截图完成")
        except Exception as e:
            print(f"  ⚠️ 行情中心异常: {e}")
        
        # 4. Strategy page
        print("[4/5] 策略管理...")
        try:
            await page.click('text=策略管理')
            await page.wait_for_timeout(1500)
            await page.screenshot(path=f"{SCREENSHOT_DIR}/04_strategy.png")
            print("  ✅ 策略管理截图完成")
        except Exception as e:
            print(f"  ⚠️ 策略管理异常: {e}")
        
        # 5. Backtest page
        print("[5/5] 回测分析...")
        try:
            await page.click('text=回测分析')
            await page.wait_for_timeout(1500)
            await page.screenshot(path=f"{SCREENSHOT_DIR}/05_backtest.png")
            print("  ✅ 回测分析截图完成")
        except Exception as e:
            print(f"  ⚠️ 回测分析异常: {e}")
        
        await browser.close()
        print("\n🎉 全部截图完成！")
        print(f"截图保存在: {SCREENSHOT_DIR}/")

asyncio.run(main())
