"""T22 封闭 Shadow DOM（Playwright 方案 F3 / AC3）：封闭 Shadow DOM 里的按钮能读到（in_shadow 为真）并点中。"""
from hands.actions import ActionExecutor

from common import load_expected, read_elements


async def run(ctx):
    b = load_expected("f15_closed_shadow")["button"]
    page = await ctx.page()
    await page.navigate(ctx.url("f15_closed_shadow/index.html"))
    els, _ = await read_elements(page)
    e = next((x for x in els if x.name == b["name"]), None)
    if e is None:
        return False, f"未读到 {b['name']}（共 {len(els)} 个元素）"
    if not e.in_shadow:
        return False, f"{b['name']} 的 in_shadow 标记为假"
    r = await ActionExecutor(page).click(e)
    got = await page.evaluate("document.getElementById('result').textContent")
    if not r.ok or got != b["result"]:
        return False, f"点击结果 {got!r}（{r.error}）"
    return True, f"读到封闭影子按钮 {e.box.to_dict()}，点击后 result={got}"
