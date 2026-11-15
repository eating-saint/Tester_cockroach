"""V03（AC3，F3）：描述含导航栏、搜索区、商品卡片（或结果列表）；弹窗打开判有遮挡、关闭后判无；同页复用；滚动超过一屏重新描述。"""
import json
import tempfile
from pathlib import Path

from hands.config import vlm_settings
from hands.decision import DecisionInput
from hands.pagestate import build_page_state
from hands.tasktree import TaskTree
from hands.vlm.client import OllamaClient
from hands.vlm.decider import VlmDecider
from hands.vlm.describe_cache import DescribeCache
from hands.vlm.digest import PageDigest

NEEDS_MODEL = True


async def describe(dec, page, tmp: Path):
    tmp.mkdir(parents=True, exist_ok=True)
    st = await build_page_state(page, tmp)
    dg = PageDigest.build(st, (tmp / st.screenshot).read_bytes(), {}, "", dec.s)
    dec.cache = DescribeCache(dec.s)                  # 每次都重新描述
    return await dec._describe(st, dg, None, tmp / "vlm")


async def run(ctx):
    page = await ctx.page()
    s = vlm_settings(ctx.config)
    dec = VlmDecider(ctx.config, OllamaClient(s))
    tmp = Path(tempfile.mkdtemp(prefix="v03_"))
    notes = []
    # ① 区块
    await page.navigate(ctx.url("f17_shop/index.html"))
    d = await describe(dec, page, tmp / "a")
    kinds = {r["kind"] for r in d.regions}
    need = [{"导航栏"}, {"搜索区"}, {"商品卡片", "结果列表"}]
    miss = [k for k in need if not (k & kinds)]
    if miss:
        return False, f"描述缺少区块 {miss}，实际 {sorted(kinds)}"
    notes.append(f"区块 {sorted(kinds)}")
    # ② 弹窗遮挡
    await page.navigate(ctx.url("f17_shop/index.html?popup=1"))
    d1 = await describe(dec, page, tmp / "b")
    await page.evaluate("document.getElementById('close-popup').click()")
    await page.wait_settled(False)
    d2 = await describe(dec, page, tmp / "c")
    if not d1.overlay or d2.overlay:
        return False, f"遮挡判断错误：弹窗打开 overlay={d1.overlay}，关闭后 overlay={d2.overlay}"
    notes.append("弹窗打开判有遮挡、关闭后判无")
    # ③ 同页复用（经过完整的 decide 流程，看 describe.json）
    from hands.vlm.client import ScriptedClient
    sc = ScriptedClient([json.dumps({"regions": [], "overlay": False}),
                         json.dumps({"reason": "r", "tool": "scroll", "arguments": {"direction": "down", "pixels": 100}}),
                         json.dumps({"reason": "r", "tool": "back", "arguments": {}})])
    dd = VlmDecider(ctx.config, sc)
    await page.navigate(ctx.url("f17_shop/index.html"))
    for i in (1, 2):
        sd = tmp / f"r{i}"
        st = await build_page_state(page, sd)
        await dd.decide(DecisionInput(st, TaskTree("g").view(), step_dir=sd))
    reused = json.loads((tmp / "r2" / "vlm" / "describe.json").read_text(encoding="utf-8"))["reused"]
    if not reused:
        return False, "同一页面第二步没有复用描述"
    notes.append("同页第二步复用描述")
    # ④ 滚动超过一屏重新描述（长页 f08）
    cache = DescribeCache(s)
    await page.navigate(ctx.url("f08_lazy/index.html"))
    st0 = await build_page_state(page, tmp / "s0")
    from hands.vlm.describe_cache import PageDescription
    cache.store(st0, PageDescription())
    vh = st0.viewport.height
    await page.evaluate(f"window.scrollTo(0, {int(vh * 1.5)})")
    await page.wait_settled(False)
    st1 = await build_page_state(page, tmp / "s1")
    need, why = cache.need(st1, None)
    if not need:
        return False, f"滚动 1.5 屏后没有重新描述（{why}，滚动到 y={st1.viewport.y}）"
    notes.append(f"滚动 1.5 屏后重新描述（{why}）")
    return True, "；".join(notes)
