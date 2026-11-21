"""网页积木：搜索、滚动看完、打开第 N 个、翻页。所有点击 / 滚动都走 ActionExecutor（拦截、需确认、暂停照旧）。"""
from __future__ import annotations

import asyncio
from urllib.parse import quote_plus, urlencode

from ..checks import respect_interval
from ..pagestate import build_page_state
from ..pause import Interrupted
from .book import render
from .calc import norm
from .engine import Item

MAX_SCROLL_SCREENS = 30


async def after_nav(ctx, rec: dict):
    """页面变了之后：截图存进节点目录，做转人工检查（验证码 / 登录 / 系统对话框）。"""
    name = ctx.shot_name()
    state = await build_page_state(ctx.page, ctx.node_dir, shot_name=name)
    rec.setdefault("shots", []).append(name)
    rec["page_url"] = state.url
    if ctx.checker is not None:
        page, recs = await ctx.checker.check_page(ctx.page, ctx.node_dir / f"handoff_{ctx.shots:02d}", state=state)
        ctx.page = page or ctx.page
        if recs:
            rec.setdefault("handoffs", []).extend(recs)
    return state


async def _navigate(ctx, url: str):
    for _ in range(5):                     # 导航被暂停打断：等恢复后重来（桌面驱动）
        try:
            return await ctx.page.navigate(url)
        except Interrupted:
            await ctx.gate.wait()
    return await ctx.page.navigate(url)


async def _act(ctx, make, tries: int = 6):
    """执行一个动作；被人工介入 / 面板暂停打断（interrupted）就等恢复后重做这一步。"""
    r = None
    for _ in range(tries):
        try:
            r = await make()
        except Interrupted:
            await ctx.gate.wait()
            continue
        if not (getattr(r, "data", None) or {}).get("interrupted"):
            return r
        await ctx.gate.wait()
    return r


def _vars(ctx):
    return ctx.node.word, ctx.tree.key_of(ctx.node.id) or "", ctx.book.values


# ---------------- 搜索 ----------------
def build_search_url(p: dict, word: str, key: str, values: dict) -> tuple[str, str]:
    q = render(p.get("query") or "{word}", word, key, values)
    url = render(p["url"], word, key, values, keep=("q",)).replace("{q}", quote_plus(q))
    extra = {str(k): render(v, word, key, values) for k, v in (p.get("extra") or {}).items()}
    if extra:
        url += ("&" if "?" in url else "?") + urlencode(extra)
    return url, q


SEARCH_WORDS = ("search", "搜索", "查找", "find", "query", "关键词")
INPUT_ROLES = ("searchbox", "combobox", "textbox")


def find_search_box(state, want: str | None = None):
    """页面上的搜索框：可见的输入框里，名字像搜索的优先、searchbox 角色其次，同等时取最靠上的。
    want：只要名字里含这段文字的（不分大小写）。"""
    best = None
    for e in state.elements:
        if not e.visible or e.role not in INPUT_ROLES or getattr(e, "is_password", False):
            continue
        if want and want.lower() not in f"{e.name} {e.text}".lower():
            continue
        hay = f"{e.name} {e.text}".lower()
        score = (2 if any(w in hay for w in SEARCH_WORDS) else 0) + (1 if e.role == "searchbox" else 0)
        if e.role == "combobox" and score == 0 and e.tag == "select":
            continue                                   # 下拉选择框（如分类）不是搜索框
        key = (-score, e.box.y)
        if best is None or key < best[0]:
            best = (key, e)
    return best[1] if best else None


def _host(url: str) -> str:
    from urllib.parse import urlparse
    h = (urlparse(url or "").hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


async def _search_from_home(ctx, home: str, q: str, rec: dict, want: str | None = None):
    """像人一样搜索：已在本站且页面上有搜索框就直接用；否则先打开首页再找搜索框。
    want：指定搜索框名字；页面还没加载出来时最多等几秒。"""
    state = None
    if _host(await ctx.page.url()) == _host(home):
        state = await build_page_state(ctx.page)
        if find_search_box(state, want) is None:
            state = None
    if state is None:
        await respect_interval(ctx.last_visit, home, getattr(ctx.config, "min_interval_s", None), ctx.log)
        await _navigate(ctx, home)
        rec["home_opened"] = True
        await after_nav(ctx, rec)                      # 首页也做转人工检查（验证码等）
        state = await build_page_state(ctx.page)
    box = find_search_box(state, want)
    for _ in range(8 if want else 0):                  # 指定的搜索框可能晚一点才出来
        if box is not None:
            break
        await asyncio.sleep(1)
        state = await build_page_state(ctx.page)
        box = find_search_box(state, want)
    if box is None:
        raise RuntimeError(f"页面上找不到搜索框{f'「{want}」' if want else ''}：{await ctx.page.url()}")
    rec["search_box"] = box.name or box.role
    ex = ctx.executor()
    r = await _act(ctx, lambda: ex.type(box, q))
    if not r.ok:
        raise RuntimeError(f"在搜索框里输入失败：{r.error}")
    before = await ctx.page.url()
    r = await _act(ctx, lambda: ex.press("Enter"))
    if not r.ok:
        raise RuntimeError(f"回车提交搜索失败：{r.error}")
    try:
        await ctx.page.wait_settled(after_navigation=True)
    except Interrupted:
        await ctx.gate.wait()
    if await ctx.page.url() == before:
        rec["note"] = "回车后网址没变（可能是站内动态加载结果）"


ERROR_PHRASES = ("something went wrong", "出了点问题", "出错了", "page not found", "页面不存在")


def error_page_hit(state) -> str | None:
    """站点自己的错误页（如 Amazon 的 SORRY something went wrong 狗图页）：返回命中的说法。"""
    hay = " ".join(f"{e.name} {e.text}" for e in state.elements[:200] if e.visible).lower()
    return next((w for w in ERROR_PHRASES if w in hay), None)


async def search(ctx, p, lists, rec, run, step):
    word, key, values = _vars(ctx)
    home = render(p["home"], word, key, values) if p.get("home") else None
    if home:
        q = render(p.get("query") or "{word}", word, key, values)
        rec["query"], rec["home"] = q, home
        await _search_from_home(ctx, home, q, rec, p.get("box"))
    else:
        url, q = build_search_url(p, word, key, values)
        rec["query"], rec["url"] = q, url
        await respect_interval(ctx.last_visit, url, getattr(ctx.config, "min_interval_s", None), ctx.log)
        await _navigate(ctx, url)
    state = await after_nav(ctx, rec)
    hit = error_page_hit(state)
    if hit and home:                                   # 错误页：回首页重搜一次
        rec["error_page"] = hit
        ctx.log(f"[taskbook] 搜索后是错误页（{hit}），回首页重搜一次")
        await _navigate(ctx, home)
        await _search_from_home(ctx, home, q, rec, p.get("box"))
        state = await after_nav(ctx, rec)
        hit = error_page_hit(state)
    if hit:
        raise RuntimeError(f"搜索后是站点错误页（{hit}）：{state.url}")
    return None


# ---------------- 滚动看完 ----------------
async def _scroll_y(page) -> float:
    return float((await page.layout())["scroll_y"])


async def scroll_to_bottom_then_top(ctx, rec):
    ex = ctx.executor()
    lay = await ctx.page.layout()
    vh = float(lay["vh"]) or 800
    last = float(lay["scroll_y"])
    screens = 0
    for _ in range(MAX_SCROLL_SCREENS):
        r = await _act(ctx, lambda: ex.scroll("down", vh * 0.9))
        y = float((r.data or {}).get("scroll_y", await _scroll_y(ctx.page)))
        if not r.ok or y <= last + 1:
            break
        screens += 1
        last = y
    last = await scroll_to_top(ctx, last, vh)
    rec["screens"] = screens
    rec["scroll_y"] = last


async def scroll_to_top(ctx, y: float | None = None, vh: float | None = None) -> float:
    """滚回页面顶部（已在顶部就什么都不做）。返回最后的滚动位置。"""
    if y is None or vh is None:
        lay = await ctx.page.layout()
        y, vh = float(lay["scroll_y"]), float(lay["vh"]) or 800
    if y <= 1:
        return y
    ex = ctx.executor()
    for _ in range(MAX_SCROLL_SCREENS):
        r = await _act(ctx, lambda: ex.scroll("up", vh * 2))
        ny = float((r.data or {}).get("scroll_y", await _scroll_y(ctx.page)))
        if not r.ok or ny >= y - 1:
            break
        y = ny
        if y <= 1:
            break
    return y


def find_text_element(state, text: str):
    """名字 / 文字 / 所在块含 text 的元素里最靠上的一个（页面坐标）。"""
    t = norm(text)
    best = None
    for e in state.elements:
        if not e.visible:
            continue
        hay = norm(f"{e.name} {e.text}")
        ctx_hit = t in norm(e.context)
        if t and (t in hay or ctx_hit):
            y = e.box.y + state.viewport.y
            if best is None or y < best[0]:
                best = (y, e)
    return best[1] if best else None


async def scroll_into_view(ctx, state, el, top_margin: float = 80) -> None:
    """把元素滚到视口上部（元素坐标相对视口）。"""
    vh = state.viewport.height or 800
    dy = el.box.y - top_margin
    if abs(dy) < 4 or (0 <= el.box.y <= vh * 0.5):
        return
    ex = ctx.executor()
    await _act(ctx, lambda: ex.scroll("down" if dy > 0 else "up", abs(dy)))


async def scroll_all(ctx, p, lists, rec, run, step):
    if p.get("mode") == "to_text" and p.get("text"):
        state = await build_page_state(ctx.page)
        el = find_text_element(state, str(p["text"]))
        if el is not None:
            await scroll_into_view(ctx, state, el)
            rec["found"] = el.name or el.text
            return None
        rec["note"] = f"没找到「{p['text']}」，改为滚到底再回顶"
    await scroll_to_bottom_then_top(ctx, rec)
    return None


# ---------------- 打开第 N 个 ----------------
def locate_item(state, item: Item):
    """按清单项的依据元素在当前页面里找元素；id 对不上（页面变化）就按原文找。"""
    t = norm(item.text)
    if item.element and not str(item.element).startswith("T"):
        e = state.element(item.element)
        if e is not None and t and t in norm(f"{e.name} {e.text}"):
            return e
    exact = [e for e in state.elements if e.visible and norm(e.name or e.text) == t]
    if exact:
        return exact[0]
    part = [e for e in state.elements if e.visible and t and t in norm(f"{e.name} {e.text}")]
    return part[0] if part else None


async def go_back_to(ctx, url: str, tries: int = 10) -> bool:
    for _ in range(tries):
        if await ctx.page.url() == url:
            return True
        w = await ctx.page.back()
        if getattr(w, "status", "ok") == "no_history":
            break
    return await ctx.page.url() == url


def _produced_names(flow) -> list[str]:
    from .blocks import REGISTRY
    out = []
    for item in flow or []:
        bid, params = next(iter(item.items()))
        params = params or {}
        for p in REGISTRY[bid].params:
            if p.type == "out" and params.get(p.name):
                out.append(params[p.name])
        if bid == "open":
            out += _produced_names(params.get("flow"))
    return out


async def _open_one(ctx, item, rec: dict, sub: dict, run, flow, step, lists, back: bool) -> int:
    """打开一条、跑子流程、返回。返回消耗次数（打开失败为 0）。"""
    from .engine import run_flow
    state = await build_page_state(ctx.page)
    el = locate_item(state, item)
    if el is None:
        sub["error"] = f"页面上找不到「{item.text}」"
        return 0
    orig = state.url
    ex = ctx.executor()
    if ctx.can_tab:
        r = await _act(ctx, lambda: ex.open_tab(el, switch=True))
        sub["how"] = "新标签页"
        if not r.ok and r.error == "没有打开新标签页" and await ctx.page.url() == orig:
            r = await _act(ctx, lambda: ex.click(el))
            sub["how"] = "同一页（没有打开新标签页）"
    else:
        r = await _act(ctx, lambda: ex.click(el))
        sub["how"] = "同一页"
    if not r.ok:
        sub["error"] = f"打开失败：{r.error}"
        return 0
    ctx.consume(1)
    await after_nav(ctx, sub)
    await run_flow(ctx, flow, f"{step}.open.flow", lists, run)
    if back:
        sub["back"] = await go_back_to(ctx, orig)
    return 1


async def open_nth(ctx, p, lists, rec, run, step):
    src = lists.get_list(p["from"])
    n, count = int(p.get("n", 1)), int(p.get("count", 1) or 1)
    flow = p.get("flow") or []
    names = _produced_names(flow)
    merged: dict[str, list] = {k: [] for k in names}
    rec["_index"] = len(run.blocks)              # 本块记录排在子流程记录之前
    rec["opened"] = []
    cost = 0
    for k in range(n, n + count):
        if k > len(src):
            rec["note"] = f"清单「{p['from']}」只有 {len(src)} 条，没有第 {k} 条"
            break
        if run.stop:
            break
        if ctx.remaining() < 1:
            rec["note"] = "次数用满"
            run.stop = "budget"
            break
        await ctx.gate.wait()
        if ctx.should_stop():
            run.stop = "cancel"
            break
        item = src[k - 1]
        sub = {"n": k, "target": item.text}
        rec["opened"].append(sub)
        for name in names:                       # 每次打开前清掉上一次子流程的产出
            lists.pop(name, None)
        cost += await _open_one(ctx, item, rec, sub, run, flow, step, lists, bool(p.get("back", True)))
        for name in names:
            merged[name].extend(lists.get(name) or [])
    for name in names:
        lists[name] = merged[name]
    rec["cost"] = cost
    rec["_consumed"] = True                      # 已逐个记账，引擎不再重复 consume
    if rec["opened"] and all("error" in s for s in rec["opened"]):
        raise RuntimeError("；".join(s["error"] for s in rec["opened"]))
    return None


# ---------------- 翻页 ----------------
DEFAULT_NEXT = ("下一页", "Next", "›")


def find_next(state, to: int | None, next_text) -> object | None:
    links = [e for e in state.elements if e.visible and e.role in ("link", "button")]
    if to is not None:
        want = str(int(to))
        for e in links:
            if (e.name or e.text or "").strip() == want:
                return e
        return None
    texts = [norm(t) for t in (next_text or DEFAULT_NEXT) if norm(t)]
    for t in texts:
        for e in links:
            if norm(e.name or e.text) == t:
                return e
    for t in texts:
        if len(t) < 2:
            continue
        for e in links:
            if t in norm(e.name or e.text):
                return e
    return None


async def page(ctx, p, lists, rec, run, step):
    state = await build_page_state(ctx.page)
    el = find_next(state, p.get("to"), p.get("next_text"))
    if el is None:
        rec["note"] = "没有下一页" if p.get("to") is None else f"没有第 {p['to']} 页"
        rec["cost"] = 0
        return None
    if not el.in_viewport:
        await scroll_into_view(ctx, state, el, top_margin=200)
        state = await build_page_state(ctx.page)
        el = find_next(state, p.get("to"), p.get("next_text")) or el
    ex = ctx.executor()
    r = await _act(ctx, lambda: ex.click(el))
    if not r.ok:
        raise RuntimeError(f"翻页失败：{r.error}")
    rec["clicked"] = el.name or el.text
    await after_nav(ctx, rec)
    return None
