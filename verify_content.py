"""
UI内容验证脚本 - 读取页面文字内容
"""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1400, "height": 900})
        
        # 1. Login
        print("=" * 50)
        print("[1] 登录页面验证")
        print("=" * 50)
        await page.goto("http://localhost:8888/", wait_until="networkidle", timeout=15000)
        await page.wait_for_timeout(2000)
        
        # Check login dialog exists
        login_dialog = await page.query_selector('.el-dialog')
        print(f"  登录弹窗: {'✅ 存在' if login_dialog else '❌ 未找到'}")
        
        title = await page.title()
        print(f"  页面标题: {title}")
        
        # Do login
        await page.fill('input[placeholder*="用户名"]', 'admin')
        await page.fill('input[type="password"]', 'quant2026')
        await page.click('button:has-text("登 录")')
        await page.wait_for_timeout(3000)
        
        # 2. Dashboard
        print("\n" + "=" * 50)
        print("[2] 首页仪表盘验证")
        print("=" * 50)
        
        # Check sidebar menu items
        menu_items = await page.query_selector_all('.el-menu-item')
        for item in menu_items:
            text = await item.inner_text()
            print(f"  菜单项: {text.strip()}")
        
        # Check for dashboard cards/content
        content = await page.inner_text('main')
        lines = [l.strip() for l in content.split('\n') if l.strip()]
        print(f"\n  页面内容 ({len(lines)} 行):")
        for line in lines[:20]:
            print(f"    {line}")
        
        # 3. Market page
        print("\n" + "=" * 50)
        print("[3] 行情中心验证")
        print("=" * 50)
        await page.click('text=行情中心')
        await page.wait_for_timeout(1500)
        
        content = await page.inner_text('main')
        lines = [l.strip() for l in content.split('\n') if l.strip()]
        print(f"  页面内容 ({len(lines)} 行):")
        for line in lines[:15]:
            print(f"    {line}")
        
        # 4. Strategy page
        print("\n" + "=" * 50)
        print("[4] 策略管理验证")
        print("=" * 50)
        await page.click('text=策略管理')
        await page.wait_for_timeout(1500)
        
        content = await page.inner_text('main')
        lines = [l.strip() for l in content.split('\n') if l.strip()]
        print(f"  页面内容 ({len(lines)} 行):")
        for line in lines[:20]:
            print(f"    {line}")
        
        # 5. Backtest page
        print("\n" + "=" * 50)
        print("[5] 回测分析验证")
        print("=" * 50)
        await page.click('text=回测分析')
        await page.wait_for_timeout(1500)
        
        content = await page.inner_text('main')
        lines = [l.strip() for l in content.split('\n') if l.strip()]
        print(f"  页面内容 ({len(lines)} 行):")
        for line in lines[:15]:
            print(f"    {line}")
        
        # 6. Check for errors in console
        print("\n" + "=" * 50)
        print("[6] 控制台错误检查")
        print("=" * 50)
        errors = []
        page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
        await page.reload(wait_until="networkidle")
        await page.wait_for_timeout(3000)
        
        if errors:
            print(f"  ⚠️ 发现 {len(errors)} 个控制台错误:")
            for e in errors[:5]:
                print(f"    - {e[:100]}")
        else:
            print("  ✅ 无控制台错误")
        
        await browser.close()
        print("\n🎉 验证完成!")

asyncio.run(main())
