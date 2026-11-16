"""桌面驱动行为验收（d03–d06、d09、d10；对应 checklist C3–C6、C9、C10）。会真实移动鼠标：运行期间勿碰电脑。

python tests/acceptance/d_desktop.py [--only C3,C5]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dcommon as dc  # noqa: E402
from hands.actions import ActionExecutor  # noqa: E402
from hands.confirm import ConfirmPolicy  # noqa: E402
from hands.desktop.page import OutsideWindow  # noqa: E402
from hands.pagestate import build_page_state  # noqa: E402
from hands.recorder import RunRecorder  # noqa: E402
from hands.runner import CaseRunner, CaseSpec  # noqa: E402
from hands.vlm.client import FuncClient  # noqa: E402
from hands.vlm.decider import VlmDecider  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), str(detail)))
    print(f"{'通过' if ok else '失败'}  {name}  {detail}", flush=True)


def find(state, name, role=None):
    return next((e for e in state.elements if e.name == name and (role is None or e.role == role)), None)


async def text_of(page) -> str:
    st = await build_page_state(page)
    return st.body_text


async def c3(page, out):
    await page.navigate(dc.BASE + "/f20_desktop/index.html")
    st = await build_page_state(page, out / "c3")
    errs = []
    for i, nm in enumerate(["坐标一", "坐标二", "坐标三", "坐标四", "坐标五", "坐标六"], 1):
        e = find(st, nm, "button")
        want = {1: (40, 40), 2: (400, 40), 3: (900, 40), 4: (40, 200), 5: (600, 420), 6: (1000, 500)}[i]
        if e is None:
            errs.append(f"{nm} 缺失")
            continue
        errs.append(max(abs(e.box.x - want[0]), abs(e.box.y - want[1]), abs(e.box.width - 120), abs(e.box.height - 40)))
    nums = [x for x in errs if isinstance(x, (int, float))]
    check("C3.1 元素位置误差 ≤ 3 像素（100% 缩放）", len(nums) == 6 and max(nums) <= 3,
          f"最大误差 {max(nums) if nums else '-'}，缩放 {st.device_pixel_ratio}")
    check("C3.3 浏览器界面不在清单、网址正确",
          not any("地址和搜索栏" in e.name for e in st.elements) and st.url.endswith("/f20_desktop/index.html"), st.url)
    pw = next((e for e in st.elements if e.is_password), None)
    st.to_json(out / "c3" / "pagestate.json")
    check("C3.4 密码框被标出且没有值", pw is not None and pw.value is None and
          "P@ss-fixture-123" not in (out / "c3" / "pagestate.json").read_text(encoding="utf-8"), pw and pw.name)
    from PIL import Image
    im = Image.open(out / "c3" / "screenshot.png")
    fr = page.frame()
    check("C3.5 截图尺寸等于内容区", im.size == (fr.content_w, fr.content_h), f"{im.size} / {(fr.content_w, fr.content_h)}")
    return st


async def c4(page, out):
    await page.navigate(dc.BASE + "/f20_desktop/canvas.html")
    st = await build_page_state(page, out / "c4")
    grid = [e for e in st.elements if e.source == "grid"]
    check("C4.1 画布页自动出现网格", len(grid) > 10, f"{len(grid)} 格")
    cell = next((e for e in grid if e.box.contains(600, 330)), None)
    ex = ActionExecutor(page)
    r = await ex.click(cell) if cell else None
    await asyncio.sleep(0.5)
    check("C4.2 选中按钮所在格子后按钮被点中", r is not None and r.ok and "已点击" in await page.title(),
          f"{cell and cell.name} → {await page.title()}")
    await page.navigate(dc.BASE + "/f20_desktop/index.html")
    st = await build_page_state(page)
    asked = []

    async def deny(req):
        asked.append(req)
        return False
    ex2 = ActionExecutor(page, confirm=ConfirmPolicy.from_config({}, True), confirm_fn=deny)
    from hands.desktop.grid import make_grid
    blank = next(e for e in make_grid(*page.frame().css_size, 12, 8) if e.box.contains(760, 300))
    r2 = await ex2.click(blank)
    check("C4.3 网格落点查不到元素时要确认", bool(asked) and asked[0].kind == "unverifiable_point" and not r2.ok,
          asked and asked[0].kind)
    page.request_grid()
    st2 = await build_page_state(page)
    g2 = [e for e in st2.elements if e.source == "grid"]
    overlap = [e for e in g2 if any(b.box.intersects(e.box) and (min(b.box.x + b.box.width, e.box.x + e.box.width) - max(b.box.x, e.box.x)) * (min(b.box.y + b.box.height, e.box.y + e.box.height) - max(b.box.y, e.box.y)) > e.box.width * e.box.height * 0.3 for b in st2.elements if b.source == "uia" and b.role == "button")]
    check("C4.4 请求网格后下一步出现格子且不盖住已有元素", bool(g2) and not overlap, f"{len(g2)} 格，重叠 {len(overlap)}")


async def c5(page, out):
    await page.navigate(dc.BASE + "/f20_desktop/actions.html")
    st = await build_page_state(page)
    ex = ActionExecutor(page)
    ctx = find(st, "右键区域")
    await ex.right_click(ctx)
    st = await build_page_state(page)
    mi = next((e for e in st.elements if e.role == "menuitem" and "分享" in e.name), None)
    if mi:
        await ex.click(mi)
    check("C5.2 右键菜单出现并选中一项", mi is not None and "菜单选择：分享这一条" in await text_of(page), mi and mi.name)
    st = await build_page_state(page)
    d = find(st, "双击区域")
    box = await page.locate_box(d)
    await page.mouse_click(*box.center(), count=2)
    await asyncio.sleep(0.4)
    check("C5.1 双击生效", "双击次数：1" in await text_of(page))
    st = await build_page_state(page)
    t = find(st, "输入框")
    s = "中文 Mixed 输入 123"
    r = await ex.type(t, s)
    check("C5.5 中英文混合输入逐字一致", r.ok and f"当前值：{s}" in await text_of(page), r.error)
    await page.chord("Ctrl", "Shift", "K")
    await asyncio.sleep(0.3)
    check("C5.4 组合键生效", "组合键：已收到" in await text_of(page))
    l0 = (await page.layout())["scroll_y"]
    r = await ex.scroll("down", 600)
    l1 = (await page.layout())["scroll_y"]
    r2 = await ex.scroll(to_bottom=True)
    st = await build_page_state(page)
    bottom = find(st, "第四屏（底部）")
    check("C5.3 滚轮滚动、滚到底能停下", l1 > l0 and r2.ok and "第四屏" in st.body_text, f"{l0:.0f}→{l1:.0f}")
    await ex.scroll(direction="up", pixels=5000)
    st = await build_page_state(page)
    link = find(st, "可新标签页打开的链接", "link")
    r = await ex.open_tab(link, switch=True)
    u = await page.url()
    check("C5.7 Ctrl+点击新标签页打开并跟随", r.ok and "actions_done.html?tab=1" in u, u)
    w = await page.back()                     # 新标签页里没有历史：关掉它回到原页
    st = await build_page_state(page)
    plain = find(st, "普通链接", "link")
    await ex.click(plain)
    u1 = await page.url()
    wb = await page.back()
    u2 = await page.url()
    wr = await page.reload()
    check("C5.6 导航、后退、刷新", u1.endswith("actions_done.html") and u2.endswith("actions.html") and wr.status == "ok",
          f"{u1} → {u2} / reload {wr.status}")


async def c6(page, out):
    w = await page.navigate(dc.BASE + "/api/slowpage?ms=3000")
    body = await text_of(page)
    check("C6.1 慢加载页等到内容出现", w.status == "ok" and "慢页面已加载" in body and w.elapsed_ms >= 2500,
          f"{w.status} {w.elapsed_ms}ms")
    await page.navigate(dc.BASE + "/f20_desktop/unstable.html")
    w = await page.wait_settled(timeout=3)
    check("C6.2 一直在变的页面超时并写明原因", w.status == "timeout" and "无网络请求列表" in " ".join(w.pending_urls),
          w.pending_urls)
    await page.navigate(dc.BASE + "/f20_desktop/index.html")
    st = await build_page_state(page)
    ex = ActionExecutor(page)
    from hands.dom import Element
    from hands.geometry import Box
    from hands.desktop.grid import GridRef
    blank = Element("g", "grid_cell", "格 x", "", "", None, None, Box(740, 280, 40, 40), True, True, True,
                    _ref=GridRef(0, Box(740, 280, 40, 40)), source="grid")
    r0 = await ex.click(blank)
    r1 = await ex.click(find(st, "坐标一", "button"))
    check("C6.3 无效点击判无变化、有效点击判有变化", (not r0.effective) and r1.effective, f"{r0.changes} / {r1.changes}")


async def c9(page, out, browser, cfg):
    asked, answers = [], []

    async def fn(req):
        asked.append(req)
        return answers.pop(0) if answers else False
    pol = ConfirmPolicy.from_config({}, True)
    ex = ActionExecutor(page, confirm=pol, confirm_fn=fn)
    await page.navigate(dc.BASE + "/f21_confirm/shop.html")
    st = await build_page_state(page)
    cart = find(st, "加入购物车", "button")
    answers[:] = [False]
    r = await ex.click(cart)
    t = await text_of(page)
    check("C9.2 加入购物车拒绝 → 没有点击、结果写用户拒绝", (not r.ok) and "用户拒绝" in (r.error or "") and "购物车 0" in t, r.error)
    answers[:] = [True]
    r = await ex.click(cart)
    await asyncio.sleep(0.3)
    n1 = len(asked)
    answers[:] = [False]
    await ex.click(cart)
    t = await text_of(page)
    check("C9.3 允许一次只点一次，再点再问", r.ok and "购物车 1" in t and len(asked) == n1 + 1, t.splitlines()[-1:])
    q = find(st, "搜索商品")
    n2 = len(asked)
    await ex.type(q, "香锅")
    r = await ex.press("Enter")
    check("C9.4a 搜索框回车不需要确认", r.ok and len(asked) == n2 and "搜索结果：香锅" in await text_of(page))
    await page.navigate(dc.BASE + "/f21_confirm/form.html")
    st = await build_page_state(page)
    answers[:] = [False]
    r = await ex.click(find(st, "提交", "button"))
    check("C9.4b 表单提交需确认", asked[-1].kind == "form_submit" and not r.ok and "已提交" not in await text_of(page))
    answers[:] = [False]
    await ex.type(find(st, "收货地址"), "某街 1 号")
    r = await ex.press("Enter")
    check("C9.4c 非搜索框回车需确认", asked[-1].kind == "form_submit" and asked[-1].tool == "press" and not r.ok)
    await page.navigate(dc.BASE + "/f21_confirm/checkout.html")
    st = await build_page_state(page)
    n3 = len(asked)
    r = await ex.click(find(st, "提交订单 / Place order", "button"))
    check("C9.5 提交订单硬拦截、不出确认", (r.error or "").startswith("guard_blocked") and len(asked) == n3
          and "已下单" not in await text_of(page), r.error)
    pol2 = ConfirmPolicy.from_config({"add_words": {"account": ["查看详情"]}}, True)
    ex2 = ActionExecutor(page, confirm=pol2, confirm_fn=fn)
    await page.navigate(dc.BASE + "/f21_confirm/shop.html")
    st = await build_page_state(page)
    answers[:] = [False]
    await ex2.click(find(st, "查看详情", "link"))
    check("C9.7 配置里加一个确认词即生效", asked[-1].hit == "查看详情")
    # C9.1 登录页：转人工，记录里没有对密码框的输入
    handoffs = []

    async def handoff(b, url, sdir, input_fn, log):
        handoffs.append(url)
        return b.page

    def reply(system, user, images, schema):
        if "regions" in json.dumps(schema or {}, ensure_ascii=False) and "【任务】" not in user:
            return dc.DESC_OK
        no = dc.element_no(user, "密码")
        if "最近几步" in user and "type" in user.split("最近几步")[1]:
            return dc.say("complete", summary="完成")
        return dc.say("type", element=no, text="secret") if no else dc.say("complete", summary="无密码框")
    rec = RunRecorder(cfg.runs_dir, "d09-login")
    runner = CaseRunner(browser, cfg, rec, decider_factory=VlmDecider.factory(cfg, FuncClient(reply)),
                        handoff_fn=handoff, log=lambda *a: None)
    case = CaseSpec("login", dc.BASE + "/f21_confirm/login.html", "在登录页输入密码", limits={"max_depth": 1, "max_steps": 4},
                    timeout_s=120)
    res = await runner.run_case(case)
    texts = []
    for p in sorted((rec.root / "login").glob("step_*/desktop.json")):
        texts += [e for e in json.loads(p.read_text(encoding="utf-8"))["injected"] if e["kind"] == "text"]
    alltext = "".join(p.read_text(encoding="utf-8") for p in rec.root.rglob("*.json"))
    check("C9.1 登录页转人工、没有向密码框输入", handoffs and not any("secret" in e.get("text", "") for e in texts)
          and "secret" not in alltext.replace('"text": "secret"', ""), f"转人工 {len(handoffs)} 次")
    return rec


async def c10(page, out, recs):
    bad = []
    for root in recs:
        for p in Path(root).rglob("desktop.json"):
            d = json.loads(p.read_text(encoding="utf-8"))
            fr = d.get("frame") or {}
            for e in d.get("injected", []):
                if e["kind"] in ("click", "move") and fr:
                    if not (fr["content_left"] <= e["x"] < fr["content_left"] + fr["content_w"]
                            and fr["content_top"] <= e["y"] < fr["content_top"] + fr["content_h"]):
                        bad.append((str(p), e))
    check("C10.1 记录中注入的点击都在内容区内", not bad, f"越界 {len(bad)}")
    try:
        await page.mouse_click(-10, -10)
        ok = False
    except OutsideWindow:
        ok = True
    check("C10.2 内容区外的落点拒绝执行", ok)
    await page.navigate(dc.BASE + "/f20_desktop/dialog.html")
    st = await build_page_state(page)
    ex = ActionExecutor(page)
    await ex.click(find(st, "打印这一页", "button"))
    await asyncio.sleep(2.0)
    dl = await page.dialogs()
    check("C10.3 打印对话框被识别为对话框（转人工依据）", bool(dl), dl)
    from hands.desktop import win32
    win32.key(win32.VK["Escape"], True)
    win32.key(win32.VK["Escape"], False)
    await asyncio.sleep(1.0)


async def main(only: set[str] | None) -> int:
    srv = dc.start_fixtures()
    dc.no_touch()
    b, cfg = await dc.browser_for("desktop")
    out = cfg.runs_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-d-desktop"
    out.mkdir(parents=True, exist_ok=True)
    page = b.page
    recs = []
    try:
        for name, fn in [("C3", c3), ("C4", c4), ("C5", c5), ("C6", c6)]:
            if only is None or name in only:
                try:
                    await fn(page, out)
                except Exception as e:
                    check(f"{name} 运行出错", False, f"{type(e).__name__}: {e}")
        if only is None or "C9" in only:
            try:
                recs.append((await c9(page, out, b, cfg)).root)
            except Exception as e:
                check("C9 运行出错", False, f"{type(e).__name__}: {e}")
        if only is None or "C10" in only:
            try:
                await c10(page, out, recs)
            except Exception as e:
                check("C10 运行出错", False, f"{type(e).__name__}: {e}")
    finally:
        await dc.close_agent_window(b)
        srv.stop()
    (out / "summary.json").write_text(json.dumps(RESULTS, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"通过 {sum(r[1] for r in RESULTS)} / {len(RESULTS)}；记录：{out}")
    return 0 if all(r[1] for r in RESULTS) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    a = ap.parse_args()
    sys.exit(asyncio.run(main(set(a.only.split(",")) if a.only else None)))
