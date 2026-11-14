"""T01 M1 启动和连接：10 次启动全部成功；CDP 驱动下 /json/version 可访问（Playwright 驱动用管道连接，改为检查页面可用）；
关闭后无残留进程。"""
import httpx

from hands.driver import create_browser

from common import chrome_procs


async def run(ctx):
    await ctx.close_browser()      # 同一 profile 不能被两个实例同时使用
    for i in range(1, 11):
        b = create_browser(ctx.config)
        await b.start()
        if b.driver == "cdp":
            async with httpx.AsyncClient() as c:
                r = await c.get(f"http://127.0.0.1:{b.port}/json/version", timeout=5)
            if r.status_code != 200 or "webSocketDebuggerUrl" not in r.json():
                await b.stop()
                return False, f"第 {i} 次 /json/version 异常: {r.status_code}"
        else:
            page = await b.new_page()
            if await page.evaluate("1+1") != 2:
                await b.stop()
                return False, f"第 {i} 次页面不可用"
        await b.stop()
        left = chrome_procs(ctx.config.profile_dir)
        if left:
            return False, f"第 {i} 次关闭后残留进程 {left}"
    return True, "10/10 启动-连接-关闭成功，无残留进程"
