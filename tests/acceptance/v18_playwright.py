"""V18（AC18，N6）：Playwright 驱动下，大模型完成仿电商任务至少 1 次。"""
from hands.driver import DriverUnavailable, create_browser

from vcommon import cfg_with, run_case

NEEDS_MODEL = True


async def run(ctx):
    await ctx.close_browser()                      # 同一个用户目录，先关掉 CDP 浏览器
    try:
        b = create_browser(cfg_with(ctx, "vlm", driver="playwright"))
    except DriverUnavailable as e:
        return False, str(e)
    await b.start()
    try:
        await b.new_page()
        rows = []
        for i in range(2):                          # 最多试 2 次，有 1 次完成即通过
            res, rec, cdir = await run_case(ctx, "v01_shop_extract.yaml", browser=b, label=f"v18-{i + 1}")
            rows.append(f"#{i + 1} {'通过' if res.passed else '失败（' + res.reason + '）'} {res.steps} 步")
            if res.passed:
                return True, f"Playwright 驱动：{'；'.join(rows)}；抽取 {[(x['field'], x['value']) for x in res.extractions]}"
        return False, "Playwright 驱动：" + "；".join(rows)
    finally:
        await b.stop()
