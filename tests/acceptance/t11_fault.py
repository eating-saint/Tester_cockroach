"""T11 M4 故障（F9）：404 被记为失败请求，状态码正确；console 报错被记录，内容一致。"""
from common import load_expected


async def run(ctx):
    exp = load_expected("f09_fault")
    page = await ctx.page()
    await page.navigate(ctx.url("f09_fault/index.html"))
    s = page.monitor.snapshot()
    for f in exp["failed"]:
        if not any(f["url_contains"] in r.url and r.status == f["status"] for r in s.failed):
            return False, f"未记录失败请求 {f}；实际 {[(r.url, r.status) for r in s.failed]}"
    if not any(exp["console_contains"] in c.text for c in s.console_errors):
        return False, f"console 报错未记录；实际 {[c.text for c in s.console_errors]}"
    return True, f"失败请求 {[(r.url, r.status) for r in s.failed]}；console {len(s.console_errors)} 条含 FIXTURE_ERROR_42"
