"""T23 换行的行内链接：链接折成两行时，外接矩形中心落在非链接文字上；点击必须落在链接文字上并生效。
（C28 实跑 g02：Playwright 驱动按外接矩形中心点 Google 结果链接，点到空白处，连点 32 次无反应。）"""
from hands.actions import ActionExecutor

from common import load_expected, read_elements


async def run(ctx):
    exp = load_expected("f16_inline_link")
    page = await ctx.page()
    await page.navigate(ctx.url("f16_inline_link/index.html"))
    els, _ = await read_elements(page)
    e = next((x for x in els if x.role == "link" and x.name == exp["link_name"]), None)
    if e is None:
        return False, f"未读到链接 {exp['link_name']}（共 {len(els)} 个元素）"
    # 夹具有效性：外接矩形中心确实不在链接上
    cx, cy = e.box.x + e.box.width / 2, e.box.y + e.box.height / 2
    on_link = await page.evaluate(
        f"(() => {{ const t = document.elementFromPoint({cx}, {cy}); return !!(t && t.closest('#lk')); }})()")
    if on_link:
        return False, f"夹具无效：外接矩形 {e.box.to_dict()} 的中心在链接上"
    r = await ActionExecutor(page).click(e)
    got = await page.evaluate("document.getElementById('result').textContent")
    if not r.ok or got != exp["result"]:
        return False, f"点击结果 {got!r}，点击位置 {r.data}（{r.error}）"
    return True, f"外接矩形 {e.box.to_dict()} 中心不在链接上；点击落在 {r.data}，result={got}"
