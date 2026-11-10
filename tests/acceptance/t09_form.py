"""T09 M7 表单（F7）：输入 → 提交 → 读到的结果文字与输入内容一致。"""
from hands.actions import ActionExecutor

from common import load_expected, read_elements


async def run(ctx):
    exp = load_expected("f07_form")
    page = await ctx.page()
    ax = ActionExecutor(page)
    await page.navigate(ctx.url("f07_form/index.html"))
    els, _ = await read_elements(page)
    box = next(e for e in els if e.role == "textbox")
    r = await ax.type(box, exp["input"])
    if not r.ok:
        return False, f"输入失败: {r.error}"
    els, _ = await read_elements(page)
    r = await ax.click(next(e for e in els if e.name == "提交"))
    if not r.ok or not r.effective:
        return False, f"提交无效果: {r.to_dict()}"
    _, body = await read_elements(page)
    want = exp["output_prefix"] + exp["input"]
    if want not in body:
        return False, f"结果中没有 {want!r}"
    return True, f"读到 {want!r}"
