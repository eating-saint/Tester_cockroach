"""T03 M3 元素数量（F1）：按钮 / 输入框 / 链接数量与 expected.json 完全一致。"""
from collections import Counter

from common import load_expected, read_elements


async def run(ctx):
    exp = load_expected("f01_static")["counts"]
    page = await ctx.page()
    await page.navigate(ctx.url("f01_static/index.html"))
    els, _ = await read_elements(page)
    got = Counter(e.role for e in els if e.visible)
    got = {k: got.get(k, 0) for k in exp}
    if got != exp:
        return False, f"期望 {exp}，实际 {got}"
    return True, f"{got}"
