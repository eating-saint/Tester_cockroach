"""任务书单测用的假网站 / 假页面 / 假执行器 / 替身 4b：不联网、不开浏览器。

FakeSite：网址 → FakeDoc（元素带页面绝对坐标、正文、标题）；路由函数可按网址动态生成页面。
install(monkeypatch)：把 web / pick 模块里的 build_page_state 换成读假页面的版本（会写一张真 PNG）。
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from hands.actions import ActionResult
from hands.dom import Element
from hands.geometry import Box
from hands.page import WaitResult
from hands.pagestate import PageState
from hands.pause import PauseGate
from hands.vlm.client import FuncClient
from hands.vlm.prompts import PromptBook

VW, VH = 1280, 800


def png_bytes() -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (VW, VH), (255, 255, 255)).save(out, format="PNG")
    return out.getvalue()


PNG = png_bytes()


@dataclass
class FakeEl:
    text: str
    y: float                       # 页面绝对坐标
    href: str | None = None
    context: str = ""
    role: str = "link"
    x: float = 20


@dataclass
class FakeDoc:
    title: str
    els: list
    body: str = ""
    height: float = 2400


class FakeSite:
    def __init__(self, router):
        self.router = router              # url -> FakeDoc
        self.visits: list[str] = []

    def doc(self, url) -> FakeDoc:
        return self.router(url)


class FakePage:
    driver = "fake"
    monitor = None

    def __init__(self, site: FakeSite, tabs: bool = False):
        self.site = site
        self.cur = "about:blank"
        self.scroll_y = 0.0
        self.history: list[tuple[str, float]] = []
        self.tabs_on = tabs
        self.closed_tabs = 0

    # 导航
    async def navigate(self, url, timeout=None):
        if self.cur != "about:blank":
            self.history.append((self.cur, self.scroll_y))
        self.cur, self.scroll_y = url, 0.0
        self.site.visits.append(url)
        return WaitResult("ok", 1)

    async def back(self, timeout=None):
        if not self.history:
            return WaitResult("no_history", 1)
        self.cur, self.scroll_y = self.history.pop()
        return WaitResult("ok", 1)

    async def url(self):
        return self.cur

    async def title(self):
        return self.site.doc(self.cur).title if self.cur != "about:blank" else ""

    async def layout(self):
        return {"scroll_x": 0, "scroll_y": self.scroll_y, "vw": VW, "vh": VH}

    def scroll_by(self, dy):
        h = self.site.doc(self.cur).height
        self.scroll_y = max(0.0, min(h - VH, self.scroll_y + dy))

    def state(self) -> PageState:
        if self.cur == "about:blank":
            return PageState(self.cur, "", Box(0, 0, VW, VH), 1.0, [], "")
        d = self.site.doc(self.cur)
        els = []
        for i, f in enumerate(d.els):
            box = Box(f.x, f.y - self.scroll_y, 300, 20)
            inside = -20 < box.y < VH
            els.append(Element(f"e{i}", f.role, f.text, f.text, "a" if f.role == "link" else "div", f.href, None,
                               box, True, inside, True, context=f.context))
        return PageState(self.cur, d.title, Box(0, self.scroll_y, VW, VH), 1.0, els, d.body)


class FakeExecutor:
    def __init__(self, page: FakePage, log: list | None = None):
        self.page = page
        self.log = log if log is not None else []

    async def scroll(self, direction=None, pixels=None, to_bottom=False):
        before = self.page.scroll_y
        self.page.scroll_by(pixels if direction == "down" else -pixels)
        self.log.append(("scroll", direction, round(self.page.scroll_y)))
        return ActionResult(True, self.page.scroll_y != before, data={"scroll_y": self.page.scroll_y})

    async def click(self, el):
        self.log.append(("click", el.name))
        if el.href:
            await self.page.navigate(el.href)
        return ActionResult(True, True, changes=["url"])

    async def open_tab(self, el, switch=False):
        self.log.append(("open_tab", el.name))
        if not self.page.tabs_on or not el.href:
            return ActionResult(False, False, error="没有打开新标签页")
        # 新标签页：没有历史，back 时关掉回到打开者（与三种驱动一致）
        self.page.history.append((self.page.cur, self.page.scroll_y))
        self.page.cur, self.page.scroll_y = el.href, 0.0
        self.page.site.visits.append(el.href)
        return ActionResult(True, True, changes=["new_tab"])


def install(monkeypatch, shots_ok: bool = True):
    async def fake_state(page, save_dir=None, shot_name="screenshot.png"):
        st = page.state()
        if save_dir is not None:
            Path(save_dir).mkdir(parents=True, exist_ok=True)
            (Path(save_dir) / shot_name).write_bytes(PNG)
            st.screenshot = shot_name
        return st
    for mod in ("hands.taskbook.web", "hands.taskbook.pick", "hands.pagestate"):
        monkeypatch.setattr(f"{mod}.build_page_state", fake_state)
    return fake_state


# ---------------- 替身 4b ----------------
LINE = re.compile(r"^(\d+) \| [^|]+ \| ([^|]+?)(?: \| (.*))?$")


def element_lines(user: str) -> list[tuple[int, str, str]]:
    out = []
    for line in user.splitlines():
        m = LINE.match(line.strip())
        if m:
            out.append((int(m.group(1)), m.group(2).strip(), m.group(3) or ""))
    return out


def stub_4b(pick=None, fields=None, junk_first: int = 0):
    """pick(region, [(no, 文字, 其余)]) -> [编号]；fields(names, elements, lines) -> {name: no}。
    junk_first：前几次故意回不合格式的文字（测重问）。"""
    state = {"junk": junk_first}

    def fn(system, user, images, schema):
        if state["junk"] > 0:
            state["junk"] -= 1
            return "我觉得是第 3 个"
        if "numbers" in str(schema):
            region = re.search(r"哪些编号是「(.+?)」里的条目", user).group(1)
            return '{"numbers": %s}' % list(pick(region, element_lines(user)) if pick else [])
        names = re.search(r"字段：(.+)", user).group(1).split("、")
        lines = [l for l in user.splitlines() if re.match(r"^T\d+ ", l)]
        got = fields(names, element_lines(user), lines) if fields else {}
        import json
        return json.dumps({"fields": [{"name": n, "no": got.get(n)} for n in names]}, ensure_ascii=False)
    return FuncClient(fn)


def make_ctx(tmp_path, site, flow, groups=(("optimus", ["megatron", "starscream"]),), start="optimus",
             client=None, tabs=False, values=None, checker=None, budget=100):
    from hands.config import VLM_DEFAULTS
    from hands.expand.tree import ExpandTree
    from hands.taskbook.book import TaskBook
    from hands.taskbook.engine import NodeCtx
    tree = ExpandTree(list(groups), start, budget)
    node = tree.next_node()
    tree.mark_searched(node.id, count=False)
    page = FakePage(site, tabs=tabs)
    book = TaskBook(name="t", groups=list(groups), start=start, flow=flow, values=dict(values or {}))
    log: list = []
    ctx = NodeCtx(browser=None, page=page, config=type("C", (), {"min_interval_s": {}})(), book=book, tree=tree,
                  node=node, node_dir=Path(tmp_path), gate=PauseGate(watch_human=False), checker=checker,
                  client=client, prompts=PromptBook(), vlm=dict(VLM_DEFAULTS),
                  make_executor=lambda p: FakeExecutor(p, log), consume=tree.consume,
                  remaining=lambda: tree.budget - tree.used, can_tab=tabs, log=lambda m: None)
    ctx.actions = log
    return ctx


def query_of(url: str) -> str:
    return (parse_qs(urlparse(url).query).get("q") or [""])[0]
