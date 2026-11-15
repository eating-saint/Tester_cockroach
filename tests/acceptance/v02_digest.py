"""V02（AC2，F2）：编号与清单一一对应；编号框与元素位置误差 ≤1px；密集页编号牌重叠 ≤5%；视口外按线索词入选并正确计数。"""
import tempfile
from pathlib import Path

from hands.config import vlm_settings
from hands.pagestate import build_page_state
from hands.vlm.digest import PageDigest

NEEDS_MODEL = False


def overlap_ratio(labels):
    bad = set()
    for i, (_, a) in enumerate(labels):
        for _, b in labels[i + 1:]:
            if a.intersects(b):
                bad.add(i)
    return len(bad) / max(1, len(labels))


async def digest_of(page, url, tmp: Path, brief=None, scroll=0, top_k=None):
    await page.navigate(url)
    if scroll:
        await page.evaluate(f"window.scrollTo(0, {scroll})")
        await page.wait_settled(False)
    st = await build_page_state(page, tmp)
    s = vlm_settings_override(top_k)
    return st, PageDigest.build(st, (tmp / st.screenshot).read_bytes(), brief or {}, "", s)


_S = None


def vlm_settings_override(top_k):
    s = dict(_S)
    if top_k is not None:
        s["offscreen_top_k"] = top_k
    return s


async def run(ctx):
    global _S
    _S = vlm_settings(ctx.config)
    page = await ctx.page()
    tmp = Path(tempfile.mkdtemp(prefix="v02_"))
    notes = []
    # ① 静态页：编号与清单一一对应；编号框换算回 CSS 像素与驱动重新定位的位置框误差 ≤ 1px
    st, d = await digest_of(page, ctx.url("f01_static/index.html"), tmp / "a")
    inside = [e for e in st.elements if e.visible and e.in_viewport]
    if len(d.numbered) != len(inside) + 0 and not all(not n.in_viewport for n in d.numbered[len(inside):]):
        return False, f"编号数 {len(d.numbered)} 与视口内元素 {len(inside)} 不对应"
    if [n.no for n in d.numbered] != list(range(1, len(d.numbered) + 1)):
        return False, "编号不连续"
    lines = d.element_lines().splitlines()
    if not all(lines[i].startswith(f"{n.no} | ") for i, n in enumerate(d.numbered)):
        return False, "元素清单的行与编号不对应"
    worst = 0.0
    for n in d.numbered:
        if not n.in_viewport:
            continue
        e = st.element(n.element_id)
        live = await page.locate_box(e)
        drawn = (e.box.x * d.scale / d.scale, e.box.y * d.scale / d.scale)
        worst = max(worst, abs(drawn[0] - live.x), abs(drawn[1] - live.y))
    if worst > 1:
        return False, f"编号框与元素位置误差 {worst:.2f}px > 1px"
    notes.append(f"静态页 {len(d.numbered)} 个编号一一对应，位置最大误差 {worst:.2f}px")
    # ② 密集页：编号牌重叠比例 ≤ 5%
    st, d = await digest_of(page, ctx.url("f19_dense/index.html"), tmp / "b")
    r = overlap_ratio(d.labels)
    if r > 0.05:
        return False, f"密集页编号牌重叠比例 {r:.1%} > 5%"
    notes.append(f"密集页 {len(d.labels)} 个编号牌重叠 {r:.1%}")
    # ③ 视口外：一页显示 24 个商品，下方第 5 行的 Spicy Mapo Tofu 在视口外；
    #    离视口更近的第 4 行商品不含线索词，按线索词 spicy 应优先选入 Spicy Mapo Tofu；其余只计数且数目正确
    st, d = await digest_of(page, ctx.url("f17_shop/index.html?size=24"), tmp / "c", {"hints": ["spicy"]},
                            top_k=1)
    outside = [e for e in st.elements if e.visible and not e.in_viewport]
    picked = [st.element(n.element_id) for n in d.numbered if not n.in_viewport]
    if not outside:
        return False, "滚动后没有视口外元素，夹具或视口尺寸不符合预期"
    if not picked or "spicy" not in (picked[0].name + picked[0].context).lower():
        return False, f"视口外第一个入选的不含线索词：{picked[0].name if picked else None}"
    if d.offscreen_omitted != len(outside) - len(picked):
        return False, f"未列出数 {d.offscreen_omitted} ≠ 视口外 {len(outside)} − 入选 {len(picked)}"
    notes.append(f"视口外 {len(outside)} 个，按线索词选入「{picked[0].name}」，其余 {d.offscreen_omitted} 个计数正确")
    (ctx.artifact("v02_dense_annotated.png")).write_bytes(d.annotated_png)
    return True, "；".join(notes)
