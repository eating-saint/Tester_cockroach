"""Playwright 驱动的页面：导航、等待（与 CDP 驱动共用 wait_quiet 和 Monitor 记账层）、新标签页、底层原语。"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from playwright.async_api import Error as PwError
from playwright.async_api import TimeoutError as PwTimeout

from ..cdp import BrowserCrashed, CDPError
from ..geometry import Box
from ..monitor import ConsoleEntry, Monitor
from ..page import KEYS, WaitResult, wait_quiet
from ..status import STATUS

# Playwright 的 resource_type 是 CDP 类型名的小写；Monitor 按 CDP 名判断
_RTYPES = {"document": "Document", "eventsource": "EventSource", "websocket": "WebSocket",
           "ping": "Ping", "other": "Other", "xhr": "XHR", "fetch": "Fetch", "script": "Script",
           "stylesheet": "Stylesheet", "image": "Image", "font": "Font", "media": "Media"}

RECYCLE_AFTER = 500          # 见 PwPage.fresh_start

_CLOSED_MARKERS = ("has been closed", "Target closed", "Browser closed", "Connection closed",
                   "browser has disconnected")


@dataclass(frozen=True)
class PwRef:
    frame_key: str          # aria 快照里 ref 的 frame 前缀，主 frame 为 ""
    aria_ref: str           # 例如 "e5"、"f1e2"


class PwPage:
    driver = "playwright"

    def __init__(self, browser, page):
        self.browser = browser
        self.config = browser.config
        self.p = page                         # playwright Page
        self.monitor = Monitor()
        self._loaded = asyncio.Event()
        self._subs: list[tuple] = []
        self._ctx_sub = None
        self._openers: list = []
        self._new_tabs: asyncio.Queue = asyncio.Queue()
        self._spawned: asyncio.Queue = asyncio.Queue()   # 没有打开者关系的新标签页（open_tab 用）
        self._panel_session = None
        self._dom = None
        self.last_elements: list = []
        self._req_seen = 0                    # 本标签页及其打开的标签页累计请求数（换回打开者标签页时不清零）

    # ---------------- 事件接线 ----------------
    def _on(self, emitter, event, cb):
        emitter.on(event, cb)
        self._subs.append((emitter, event, cb))

    async def attach(self):
        self.monitor = Monitor()
        self._loaded = asyncio.Event()
        p = self.p
        self._on(p, "request", self._on_request)
        self._on(p, "response", self._on_response)
        self._on(p, "requestfinished", lambda r: self.monitor.req_finished(r))
        self._on(p, "requestfailed", lambda r: self.monitor.req_failed(r, r.failure))
        self._on(p, "console", self._on_console)
        self._on(p, "pageerror", lambda err: self.monitor.console_error(
            ConsoleEntry("exception", "error", str(getattr(err, "message", err)))))
        self._on(p, "domcontentloaded", lambda _: self.monitor.set_lifecycle("DOMContentLoaded"))
        self._on(p, "load", self._on_load)
        self._on(p, "crash", lambda _: self.browser._mark_crashed("renderer crashed"))
        self._on(p.context, "page", self._on_context_page)
        try:
            if await p.evaluate("document.readyState") == "complete":
                self._loaded.set()
                self.monitor.set_lifecycle("load")
        except PwError:
            pass
        STATUS.page_changed()

    def detach(self):
        for emitter, event, cb in self._subs:
            try:
                emitter.remove_listener(event, cb)
            except Exception:
                pass
        self._subs = []
        if self._panel_session is not None:
            self._panel_session.detach()
            self._panel_session = None

    def _on_request(self, req):
        prev = req.redirected_from
        if prev is not None:
            self.monitor.req_finished(prev)
        try:
            frame = req.frame
            main_nav = req.is_navigation_request() and frame == self.p.main_frame
        except PwError:                    # Service Worker 的请求没有所属 frame
            main_nav = False
        if main_nav:                       # 主 frame 开始加载新文档（等价于 CDP 的 frameStartedLoading）
            self._loaded.clear()
            self.monitor.set_lifecycle("init")
        self._req_seen += 1
        rt = req.resource_type
        self.monitor.req_started(req, req.url, _RTYPES.get(rt, rt.capitalize() if rt else None))

    def _on_response(self, resp):
        self.monitor.req_response(resp.request, resp.status, resp.url)

    def _on_console(self, msg):
        if msg.type in ("error", "assert"):
            self.monitor.console_error(ConsoleEntry("console", "error", msg.text))
        for a in msg.args:                    # 参数句柄不用，立即释放，否则一直登记在 Python 端
            t = asyncio.ensure_future(a.dispose())
            t.add_done_callback(lambda f: f.cancelled() or f.exception())

    def _on_load(self, _):
        self.monitor.set_lifecycle("load")
        self._loaded.set()

    async def _on_context_page(self, newp):
        try:
            opener = await newp.opener()
        except PwError:
            return
        if opener is self.p:
            self._new_tabs.put_nowait(newp)
        else:                                   # Ctrl+点击打开的标签页没有打开者关系，单独记下供 open_tab 使用
            self._spawned.put_nowait(newp)

    # ---------------- 异常转换 ----------------
    def _convert(self, e: Exception) -> Exception:
        msg = str(e)
        if self.browser.crashed.is_set():
            return BrowserCrashed(self.browser.crash_reason or msg)
        if any(m in msg for m in _CLOSED_MARKERS) and not self.browser._stopping:
            self.browser._mark_crashed("browser closed")
            return BrowserCrashed(msg)
        return CDPError("playwright", -1, msg.splitlines()[0] if msg else type(e).__name__)

    async def _call(self, aw):
        self.browser.check_crashed()
        try:
            return await aw
        except PwError as e:
            raise self._convert(e) from e

    # ---------------- 导航与等待 ----------------
    def take_new_tab(self):
        try:
            return self._new_tabs.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def clear_spawned_tabs(self):
        while not self._spawned.empty():
            self._spawned.get_nowait()

    def take_spawned_tab(self):
        """open_tab 用：本页打开的新标签页（有打开者关系的优先），或动作期间新出现的任何标签页。"""
        t = self.take_new_tab()
        if t is not None:
            return t
        try:
            return self._spawned.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def wait_settled(self, after_navigation: bool = False, timeout: float | None = None) -> WaitResult:
        timeout = self.config.nav_timeout_s if timeout is None else timeout
        self.browser.check_crashed()
        wait = asyncio.ensure_future(wait_quiet(self.monitor, self._loaded, self.config, timeout))
        crash = asyncio.ensure_future(self.browser.crashed.wait())
        try:
            done, _ = await asyncio.wait({wait, crash}, return_when=asyncio.FIRST_COMPLETED)
            if wait in done:
                return wait.result()
            raise BrowserCrashed(self.browser.crash_reason or "browser crashed")
        finally:
            for t in (wait, crash):
                if not t.done():
                    t.cancel()
            STATUS.clear_wait()

    async def _goto_like(self, start: float, timeout: float, aw) -> WaitResult | None:
        """执行一次导航调用（等到 commit）；超时时直接给出超时结果，其他错误（如连不上）交给后续等待。"""
        try:
            r = await aw
        except PwTimeout:
            return WaitResult("timeout", int((time.monotonic() - start) * 1000), self.monitor.pending_urls())
        except PwError as e:
            ex = self._convert(e)
            if isinstance(ex, BrowserCrashed):
                raise ex from e
            return None                     # 例如 net::ERR_*：浏览器会显示错误页，照常等它加载完
        if r is None and not self._loaded.is_set():
            self._loaded.set()              # 同文档导航（锚点）不会触发 load
        return None

    async def navigate(self, url: str, timeout: float | None = None) -> WaitResult:
        timeout = self.config.nav_timeout_s if timeout is None else timeout
        start = time.monotonic()
        self.browser.check_crashed()
        self.monitor.reset_for_navigation()
        self._loaded.clear()
        w = await self._goto_like(start, timeout, self.p.goto(url, wait_until="commit", timeout=timeout * 1000))
        if w is not None:
            return w
        return await self.wait_settled(True, max(0.0, timeout - (time.monotonic() - start)))

    async def reload(self, timeout: float | None = None) -> WaitResult:
        timeout = self.config.nav_timeout_s if timeout is None else timeout
        start = time.monotonic()
        self.browser.check_crashed()
        self.monitor.reset_for_navigation()
        self._loaded.clear()
        w = await self._goto_like(start, timeout, self.p.reload(wait_until="commit", timeout=timeout * 1000))
        if w is not None:
            return w
        return await self.wait_settled(True, max(0.0, timeout - (time.monotonic() - start)))

    async def adopt_new_tab(self, newp) -> WaitResult:
        self._openers.append(self.p)
        self.detach()
        self.p = newp
        await self.attach()
        return await self.wait_settled(True)

    async def back(self, timeout: float | None = None) -> WaitResult:
        timeout = self.config.nav_timeout_s if timeout is None else timeout
        start = time.monotonic()
        self.browser.check_crashed()
        can_back = await self._call(self.p.evaluate("history.length")) > 1
        url_before = self.p.url
        if can_back:
            self.monitor.reset_for_navigation()
            self._loaded.clear()
            try:
                r = await self.p.go_back(wait_until="commit", timeout=timeout * 1000)
            except PwTimeout:
                return WaitResult("timeout", int((time.monotonic() - start) * 1000), self.monitor.pending_urls())
            except PwError as e:
                raise self._convert(e) from e
            if r is not None or self.p.url != url_before:
                if r is None:
                    self._loaded.set()      # 同文档后退
                return await self.wait_settled(True, max(0.0, timeout - (time.monotonic() - start)))
            self._loaded.set()              # 实际没有可后退的历史
        if self._openers:
            opener = self._openers.pop()
            current = self.p
            self.detach()
            try:
                await current.close()
            except PwError:
                pass
            self.p = opener
            await self.attach()
            return await self.wait_settled(False, timeout)
        return WaitResult("no_history", int((time.monotonic() - start) * 1000))

    async def wait_for(self, getter, predicate, timeout: float, interval: float = 0.05):
        """条件等待：反复读取直到满足条件或超时，返回最后一次读取的值。没有可订阅事件时使用。"""
        deadline = time.monotonic() + timeout
        while True:
            value = await getter()
            if predicate(value) or time.monotonic() >= deadline:
                return value
            await asyncio.sleep(interval)  # condition-poll

    async def evaluate(self, expression: str, session=None):
        """只读取值。表达式写法与 CDP 驱动相同（如 "document.title"、"(()=>{…})()"）。"""
        return await self._call(self.p.evaluate(expression))

    async def url(self) -> str:
        self.browser.check_crashed()
        return self.p.url

    async def title(self) -> str:
        return await self._call(self.p.title()) or ""

    async def refresh_offsets(self):
        return None                         # 坐标换算在读取元素时完成

    # ---------------- 底层原语 ----------------
    def _reader(self):
        if self._dom is None:
            from .dom import PwDomReader
            self._dom = PwDomReader()
        return self._dom

    async def read_dom(self):
        try:
            els, body = await self._reader().read(self)
        except PwError as e:
            raise self._convert(e) from e
        self.last_elements = els
        return els, body

    async def layout(self) -> dict:
        d = await self.evaluate(
            "(()=>{const e=document.documentElement;return {vw:e.clientWidth,vh:e.clientHeight,"
            "scroll_x:scrollX,scroll_y:scrollY,content_w:e.scrollWidth,content_h:e.scrollHeight}})()")
        d = {k: float(v or 0) for k, v in (d or {}).items()}
        d.setdefault("vw", 800.0)
        d.setdefault("vh", 600.0)
        d["layout_y"] = d.get("scroll_y", 0.0)
        return d

    def _locator(self, el):
        if el._ref is None or not isinstance(el._ref, PwRef):
            raise LookupError(f"element {el.id} has no node reference")
        return self.p.locator(f"aria-ref={el._ref.aria_ref}")

    async def _relocate(self, el):
        """引用失效时（页面重绘后）重读一次元素列表，按角色、名字、frame 找回同一元素。"""
        els, _ = await self.read_dom()
        for e in els:
            if e.role == el.role and e.name == el.name and e._ref.frame_key == el._ref.frame_key:
                return e
        return None

    async def locate_box(self, el) -> Box:
        """滚到可见后取元素在主页面视口中的框：frame 内 getBoundingClientRect + 逐层 frame 偏移（见 PwDomReader.frame_offset）。"""
        target = el
        for attempt in range(2):
            loc = self._locator(target)
            bb = None
            try:
                await loc.scroll_into_view_if_needed(timeout=5000)
                h = await loc.element_handle(timeout=5000)
                # 与 CDP 驱动的 DOM.getContentQuads 第一个矩形一致：换行的行内链接取第一行文字的矩形，
                # 不用 getBoundingClientRect 的外接框（中心可能落在两行之间的空白处，点了没反应）
                r = await h.evaluate("e => { const q = [...e.getClientRects()].find(r => r.width > 0 && r.height > 0)"
                                     " || e.getBoundingClientRect(); return [q.x, q.y, q.width, q.height]; }")
                ox, oy = await self._reader().frame_offset(await h.owner_frame())
                await h.dispose()
                if r[2] > 0 and r[3] > 0:
                    bb = Box(r[0] + ox, r[1] + oy, r[2], r[3])
            except PwError as e:
                ex = self._convert(e)
                if isinstance(ex, BrowserCrashed):
                    raise ex from e
            if bb is not None:
                return bb
            if attempt == 0:
                found = await self._relocate(el)
                if found is None:
                    break
                target = found
        raise LookupError(f"element {el.id} has no layout")

    async def node_value(self, el) -> str | None:
        try:
            v = await self._locator(el).evaluate(
                "e => 'value' in e ? e.value : (e.isContentEditable ? e.innerText : null)", timeout=5000)
        except Exception:
            return None
        return None if v is None else str(v)

    async def mouse_move(self, x: float, y: float):
        await self._call(self.p.mouse.move(x, y))

    async def mouse_click(self, x: float, y: float, ctrl: bool = False):
        await self._call(self.p.mouse.move(x, y))
        if ctrl:                                   # Ctrl+点击：链接在新标签页打开
            await self._call(self.p.keyboard.down("Control"))
        try:
            await self._call(self.p.mouse.down())
            await self._call(self.p.mouse.up())
        finally:
            if ctrl:
                await self._call(self.p.keyboard.up("Control"))

    async def mouse_wheel(self, x: float, y: float, dy: float):
        await self._call(self.p.mouse.move(x, y))
        await self._call(self.p.mouse.wheel(0, dy))

    async def key_press(self, name: str):
        if name not in KEYS:
            raise ValueError(f"unsupported key: {name}")
        await self._call(self.p.keyboard.press(name))

    async def select_all(self):
        await self._call(self.p.keyboard.press("Control+A"))

    async def insert_text(self, text: str):
        await self._call(self.p.keyboard.insert_text(text))

    async def screenshot(self, full_page: bool = False) -> bytes:
        return await self._call(self.p.screenshot(full_page=full_page, type="png"))

    async def bring_to_front(self):
        await self._call(self.p.bring_to_front())

    async def frame_info(self, el) -> tuple[tuple[float, float], bool]:
        key = el._ref.frame_key if isinstance(el._ref, PwRef) else ""
        if not key:
            return (0.0, 0.0), False
        frame = await self._frame_of(el)
        off = await self._reader().frame_offset(frame)
        main = await self.evaluate("location.origin")
        try:
            origin = await frame.evaluate("location.origin")
        except PwError:
            origin = None
        return off, origin != main

    async def _frame_of(self, el):
        h = await self._call(self._locator(el).element_handle(timeout=5000))
        try:
            return await self._call(h.owner_frame())
        finally:
            await h.dispose()

    async def evaluate_in_frame(self, el, expression: str):
        frame = await self._frame_of(el)
        return await self._call(frame.evaluate(expression))

    async def fresh_start(self):
        """用例开始时调用。Playwright 会给每个请求、响应、frame 在 Python 端登记一个对象，标签页关闭前一直不释放
        （每类上限 1 万个），长时间复用同一标签页内存会持续上涨。累计请求超过 RECYCLE_AFTER 时换一个新标签页、
        关掉旧的，登记的对象随之释放。用例开头不需要保留历史记录，所以不影响行为。"""
        if self._req_seen < RECYCLE_AFTER:
            return
        old = self.p
        newp = await self._call(old.context.new_page())
        self.detach()
        self._openers.clear()
        self.p = newp
        self._req_seen = 0
        await self.attach()
        for p in list(old.context.pages):      # 旧标签页（含此前用例留下的打开者标签页）一并关掉
            if p is not newp:
                try:
                    await p.close()
                except PwError:
                    pass

    async def panel_session(self):
        """面板专用（推流、预览手动输入）：经 Playwright 的 CDP 会话复用现有推流代码。"""
        if self._panel_session is None:
            from .session import PwCdpSession
            raw = await self._call(self.p.context.new_cdp_session(self.p))
            self._panel_session = PwCdpSession(raw)
        return self._panel_session
