"""T07 M2 超时（F4）：超时设为 5 秒时按时返回"超时"，并给出未完成的请求地址。"""
from common import load_expected


async def run(ctx):
    exp = load_expected("f04_slow")
    page = await ctx.page()
    w = await page.navigate(ctx.url("f04_slow/index.html"), timeout=exp["timeout_s"])
    if w.status != "timeout":
        return False, f"状态 {w.status}，期望 timeout"
    if not (exp["timeout_s"] - 1 <= w.elapsed_ms / 1000 <= exp["timeout_s"] + 1):
        return False, f"耗时 {w.elapsed_ms}ms 不在 5±1s 内"
    if not any(exp["slow_url_contains"] in u for u in w.pending_urls):
        return False, f"未完成请求 {w.pending_urls} 中没有慢请求"
    return True, f"{w.elapsed_ms}ms 返回 timeout，未完成请求 {w.pending_urls}"
