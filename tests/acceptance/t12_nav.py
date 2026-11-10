"""T12 M7 跳转（F11）：能跟随新页面或新标签页，URL 正确；后退能回到原页面。"""
from hands.actions import ActionExecutor

from common import load_expected, read_elements


async def run(ctx):
    exp = load_expected("f11_nav")
    page = await ctx.page()
    ax = ActionExecutor(page)
    await page.navigate(ctx.url("f11_nav/index.html"))

    async def click(name):
        els, _ = await read_elements(ax.page)
        return await ax.click(next(e for e in els if e.name == name))

    r = await click("同页跳转")
    url = await ax.page.url()
    if exp["same_url_contains"] not in url:
        return False, f"同页跳转后 URL {url}"
    await ax.back()
    url = await ax.page.url()
    if exp["origin_url_contains"] not in url:
        return False, f"后退后 URL {url}"
    r = await click("新标签页")
    url = await ax.page.url()
    if "new_tab" not in r.changes or exp["blank_url_contains"] not in url:
        return False, f"新标签页未跟随：changes={r.changes} url={url}"
    await ax.back()
    url = await ax.page.url()
    if exp["origin_url_contains"] not in url:
        return False, f"新标签页后退后 URL {url}"
    return True, "同页跳转/新标签页均跟随正确，后退回到原页面"
