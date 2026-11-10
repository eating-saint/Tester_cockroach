"""T10 M7 滚动（F8）：滚到底以后，第二页内容出现在元素列表里。"""
from hands.actions import ActionExecutor

from common import load_expected, read_elements


async def run(ctx):
    exp = load_expected("f08_lazy")["page2_link_texts"]
    page = await ctx.page()
    await page.navigate(ctx.url("f08_lazy/index.html"))
    els, _ = await read_elements(page)
    if any(e.name in exp for e in els):
        return False, "滚动前就出现了第二页内容"
    r = await ActionExecutor(page).scroll(to_bottom=True)
    els, _ = await read_elements(page)
    names = {e.name for e in els}
    missing = [t for t in exp if t not in names]
    if missing:
        return False, f"滚到底后缺少 {missing}（{r.to_dict()}）"
    return True, f"滚动到 y={r.data['scroll_y']}，第二页 {len(exp)} 项全部出现"
