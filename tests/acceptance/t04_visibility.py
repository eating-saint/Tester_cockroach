"""T04 M3 可见性（F2）：每个元素的"可见""可用"标记全部正确。"""
from common import RECTS_JS, load_expected, read_elements


async def run(ctx):
    exp = load_expected("f02_hidden")["elements"]
    page = await ctx.page()
    await page.navigate(ctx.url("f02_hidden/index.html"))
    els, _ = await read_elements(page)
    labels = {x["id"]: x["label"] for x in await page.evaluate(RECTS_JS)}
    by_name = {e.name: e for e in els}
    errors = []
    for dom_id, want in exp.items():
        e = by_name.get(labels.get(dom_id))
        if e is None:
            if want["visible"] is False:     # display:none 不出现在列表中也算对
                continue
            errors.append(f"{dom_id} 未提取到")
            continue
        if e.visible != want["visible"] or e.enabled != want["enabled"]:
            errors.append(f"{dom_id}: visible={e.visible} enabled={e.enabled}，期望 {want}")
    return (not errors), ("; ".join(errors) or f"{len(exp)} 个元素标记全部正确")
