"""T05 M3 位置框（F1）：位置框与 getBoundingClientRect 误差 ≤ 1 像素；生成元素框图供人工核对。"""
from hands import screenshot as shot

from common import RECTS_JS, box_diff, load_expected, read_elements


async def run(ctx):
    ids = load_expected("f01_static")["ids"]
    page = await ctx.page()
    await page.navigate(ctx.url("f01_static/index.html"))
    els, _ = await read_elements(page)
    rects = {x["id"]: x for x in await page.evaluate(RECTS_JS)}
    by_name = {e.name: e for e in els}
    worst, errors = 0.0, []
    for i in ids:
        e = by_name.get(rects[i]["label"])
        if e is None:
            errors.append(f"{i} 未提取到")
            continue
        d = box_diff(e.box, rects[i]["rect"])
        worst = max(worst, d)
        if d > 1:
            errors.append(f"{i} 误差 {d:.2f}px")
    png = await shot.capture(page, full_page=True)
    out = ctx.artifact("T05_f01_boxes.png")
    out.write_bytes(shot.annotate(png, els, await shot.device_pixel_ratio(page)))
    if errors:
        return False, "; ".join(errors)
    return True, f"{len(ids)} 个元素最大误差 {worst:.2f}px；标注图 {out}"
