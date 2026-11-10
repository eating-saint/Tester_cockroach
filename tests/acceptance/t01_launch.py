"""T01 M1 启动和连接：10 次启动全部成功；/json/version 可访问；关闭后无残留进程。"""
import httpx

from hands.browser import BrowserManager

from common import chrome_procs


async def run(ctx):
    await ctx.close_browser()      # 同一 profile 不能被两个实例同时使用
    for i in range(1, 11):
        b = BrowserManager(ctx.config)
        await b.start()
        async with httpx.AsyncClient() as c:
            r = await c.get(f"http://127.0.0.1:{b.port}/json/version", timeout=5)
        if r.status_code != 200 or "webSocketDebuggerUrl" not in r.json():
            await b.stop()
            return False, f"第 {i} 次 /json/version 异常: {r.status_code}"
        await b.stop()
        left = chrome_procs(ctx.config.profile_dir)
        if left:
            return False, f"第 {i} 次关闭后残留进程 {left}"
    return True, "10/10 启动-连接-关闭成功，无残留进程"
