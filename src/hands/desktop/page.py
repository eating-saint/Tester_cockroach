"""DesktopPage：在代理窗口里实现 PageLike（spec F3–F7、F10）。

坐标：对上层一律是视口 CSS 像素；换算在 Frame 里做。
没有加载信号（probe.md 第 2 条）：导航 / 动作后先看网址或 Document 是否换了（新页面已提交），再等截图稳定。
所有注入前：窗口还在（否则 AgentWindowClosed）、暂停门放行（否则 Interrupted）、窗口在前台、落点在内容区（否则 OutsideWindow）。
"""
from __future__ import annotations

import asyncio
import time

from ..cdp import CDPError
from ..driver import AgentWindowClosed
from ..geometry import Box
from ..monitor import Monitor
from ..page import WaitResult
from ..status import STATUS
from . import screen, win32
from .coords import Frame
from .grid import GridRef, make_grid
from .uia import UiaRef

TITLE_SUFFIX = " - Google Chrome"


class Unsupported(CDPError):
    def __init__(self, what: str):
        super().__init__("desktop", -1, f"桌面驱动不支持 {what}")


class OutsideWindow(ValueError):
    pass


class DesktopPage:
    driver = "desktop"

    def __init__(self, browser, hwnd: int, reader, gate, settings: dict, nav_timeout_s: float = 30):
        self.browser = browser
        self.hwnd = hwnd
        self.reader = reader
        self.gate = gate
        self.s = settings
        self.nav_timeout_s = nav_timeout_s
        self.monitor = Monitor()
        self.monitor.set_lifecycle("load")
        self.epoch = 0
        self.last_elements: list = []
        self._grid_next = False
        self._frame: Frame | None = None
        self._frame_at = 0.0
        self._key = ("", ())              # (网址, Document 的 RuntimeId)：判断"新页面已提交"
        self._tabs_known: set = set()
        self._spawned: list = []
        self._openers: list = []          # 跟随过去的新标签页的"打开者"，back 无历史时回到它
        self.injected: list[dict] = []
        self.last_read_ms = 0

    # ---------------- 基础 ----------------
    def _alive(self):
        if not win32.alive(self.hwnd):
            raise AgentWindowClosed("代理窗口已关闭")

    def frame(self, refresh: bool = False) -> Frame:
        now = time.monotonic()
        if refresh or self._frame is None or now - self._frame_at > 0.5:
            self._alive()
            rect = self.reader.document_rect(self.reader.document(refresh=refresh or self._frame is None))
            self._frame = Frame.from_rect(rect, win32.dpi_of(self.hwnd) / 96)
            self._frame_at = now
        return self._frame

    def _page_key(self) -> tuple:
        try:
            doc = self.reader.document()
            return self.reader.doc_url(doc), tuple(doc.GetRuntimeId())
        except Exception:
            return self._key

    def _log(self, kind: str, **kw):
        self.injected.append({"t": round(time.time(), 3), "kind": kind, **kw})
        if len(self.injected) > 2000:
            del self.injected[:1000]

    def drain_injected(self) -> list[dict]:
        out, self.injected = self.injected, []
        return out

    def _ensure(self):
        self._alive()
        self.gate.check()
        if win32.foreground() != self.hwnd:
            win32.focus(self.hwnd)

    # ---------------- 读取 ----------------
    async def read_dom(self):
        self._alive()
        t0 = time.monotonic()
        self.epoch += 1
        doc = self.reader.document()
        self._frame = Frame.from_rect(self.reader.document_rect(doc), win32.dpi_of(self.hwnd) / 96)
        self._frame_at = time.monotonic()
        els, body = self.reader.read(self._frame, self.epoch, doc)
        self._key = (self.reader.doc_url(doc), tuple(doc.GetRuntimeId()))
        g = self.s["grid"]
        usable = [e for e in els if e.visible and e.in_viewport]
        if self._grid_next or len(usable) < int(g["min_elements"]):
            vw, vh = self._frame.css_size
            els = els + make_grid(vw, vh, int(g["cols"]), int(g["rows"]), [e.box for e in usable],
                                  start_id=len(els) + 1)
            self._grid_next = False
        self.last_elements = els
        if not self._tabs_known:
            self._tabs_known = {t["rid"] for t in self.reader.tabs()}
        self.last_read_ms = int((time.monotonic() - t0) * 1000)
        return els, body

    def request_grid(self):
        self._grid_next = True

    async def layout(self) -> dict:
        vw, vh = self.frame().css_size
        sc = self.reader.scroll()
        content_h, scroll_y = vh, 0.0
        if sc and sc[0] >= 0 and sc[1] > 0:
            content_h = vh * 100 / sc[1]
            scroll_y = sc[0] / 100 * max(content_h - vh, 0)
        return {"vw": vw, "vh": vh, "scroll_x": 0.0, "scroll_y": scroll_y, "layout_y": scroll_y,
                "content_w": vw, "content_h": content_h}

    async def url(self) -> str:
        self._alive()
        return self.reader.doc_url() or "about:blank"

    async def title(self) -> str:
        t = win32.window_text(self.hwnd)
        return t[:-len(TITLE_SUFFIX)] if t.endswith(TITLE_SUFFIX) else t

    async def device_pixel_ratio(self) -> float:
        return self.frame().scale

    def _image(self):
        self._alive()
        return screen.capture_image(self.hwnd, self.frame())

    async def screenshot(self, full_page: bool = False) -> bytes:
        return screen.to_png(self._image())

    async def visual_hash(self) -> str:
        return screen.visual_hash(self._image())

    async def dialogs(self) -> list[dict]:
        """系统对话框（文件保存等）+ Chrome 自己的打印预览（窗口里出现 chrome://print 的 Document）。"""
        pid = win32.window_pid(self.hwnd)
        out = [{"hwnd": h, "title": win32.window_text(h)} for h in win32.owned_dialogs(pid)]
        if self.reader.doc_url().startswith("chrome://print"):
            out.append({"hwnd": self.hwnd, "title": "打印预览"})
        return out

    def _find(self, el):
        """在最近一次读取里按 RuntimeId 找同一个元素（换了纪元也能找）。"""
        rid = getattr(el._ref, "runtime_id", None)
        for e in self.last_elements:
            if isinstance(e._ref, UiaRef) and rid and e._ref.runtime_id == rid:
                return e
        return None

    async def locate_box(self, el) -> Box:
        if isinstance(el._ref, GridRef):
            return el._ref.box
        if not isinstance(el._ref, UiaRef):
            raise LookupError(f"element {el.id} has no node reference")
        f = self.frame()
        vw, vh = f.css_size
        for attempt in range(7):
            rect = self.reader.refresh_rect(el._ref)
            if rect is None:
                cur = self._find(el)
                rect = self.reader.refresh_rect(cur._ref) if cur is not None else None
            if rect is None or rect[2] <= rect[0] or rect[3] <= rect[1]:
                raise LookupError(f"element {el.id} is gone or has no layout")
            box = f.box_to_css(rect)
            cy = box.y + box.height / 2
            if 0 <= cy <= vh and box.x + box.width > 0 and box.x < vw:
                return box
            if attempt == 6:
                break
            dy = cy - vh / 2                       # 朝元素方向滚：一次最多大半屏
            step = max(min(dy, vh * 0.8), -vh * 0.8)
            await self.mouse_wheel(vw / 2, vh / 2, step)
            await asyncio.sleep(0.25)
        raise LookupError(f"element {el.id} could not be scrolled into view")

    async def node_value(self, el) -> str | None:
        if getattr(el, "is_password", False) or not isinstance(el._ref, UiaRef):
            return None
        return self.reader.value(el._ref)

    async def form_action(self, el) -> str | None:
        return None                                 # 无障碍树里没有表单提交地址；靠名称判断

    def _el_from_hit(self, hit):
        from ..dom import Element
        for e in self.last_elements:                # 优先返回清单里的元素（命中元素本身或它的祖先）
            if isinstance(e._ref, UiaRef) and e._ref.runtime_id and (
                    e._ref.runtime_id == hit.runtime_id or e._ref.runtime_id in hit.ancestors):
                return e
        f = self.frame()
        return Element("hit", hit.role or "generic", hit.name, hit.name, "", None, None, f.box_to_css(hit.rect),
                       True, True, True, _ref=UiaRef(hit.runtime_id, None, self.epoch), source="uia")

    async def element_at(self, x: float, y: float):
        sx, sy = self.frame().to_screen(x, y)
        hit = self.reader.element_at(sx, sy)
        if hit is None or not hit.in_document or not hit.runtime_id:
            return None
        return self._el_from_hit(hit)

    async def occluded(self, elements) -> set[str]:
        f = self.frame()
        out = set()
        for e in elements:
            if not isinstance(e._ref, UiaRef) or not e._ref.runtime_id:
                continue
            cx, cy = e.box.center()
            hit = self.reader.element_at(*f.to_screen(cx, cy))
            if hit is None:
                continue
            if hit.runtime_id != e._ref.runtime_id and e._ref.runtime_id not in hit.ancestors:
                out.add(e.id)
        return out

    # ---------------- 输入 ----------------
    def _to_screen(self, x, y) -> tuple[int, int]:
        f = self.frame()
        sx, sy = f.to_screen(x, y)
        if not f.contains_screen(sx, sy):
            raise OutsideWindow(f"落点 ({x:.0f}, {y:.0f}) 不在代理窗口的网页内容区内，拒绝执行")
        return sx, sy

    def cursor_css(self) -> tuple[float, float]:
        """真实光标当前位置（CSS 坐标，夹到内容区内）：轨迹从这里出发，不会先跳一下。"""
        f = self.frame()
        cx, cy = f.to_css(*win32.cursor_pos())
        vw, vh = f.css_size
        return min(max(cx, 1), vw - 2), min(max(cy, 1), vh - 2)

    async def mouse_move(self, x: float, y: float):
        self._ensure()
        sx, sy = self._to_screen(x, y)
        win32.move_abs(sx, sy)
        self._log("move", x=sx, y=sy)

    async def mouse_click(self, x: float, y: float, ctrl: bool = False, button: str = "left", count: int = 1):
        await self.mouse_move(x, y)
        sx, sy = self._to_screen(x, y)
        await asyncio.sleep(float(self.s.get("click_pause_ms", 0)) / 1000)   # 到位后停一下，让人看清点在哪
        self.gate.check()
        if ctrl:
            win32.key(win32.VK["Ctrl"], True)
        try:
            for i in range(count):
                self.gate.check()
                win32.button(button, True)
                win32.button(button, False)
                if i + 1 < count:
                    await asyncio.sleep(0.06)
        finally:
            if ctrl:
                win32.key(win32.VK["Ctrl"], False)
        self._log("click", x=sx, y=sy, button=button, count=count, ctrl=ctrl)

    async def mouse_wheel(self, x: float, y: float, dy: float):
        await self.mouse_move(x, y)
        notches = max(1, int(round(abs(dy) / float(self.s["wheel_px_per_notch"]))))
        for _ in range(notches):
            self.gate.check()
            win32.wheel(-120 if dy > 0 else 120)
            await asyncio.sleep(0.03)
        self._log("wheel", dy=dy, notches=notches)

    async def key_press(self, name: str):
        self._ensure()
        vk = win32.VK.get(name)
        if vk is None:
            raise ValueError(f"unsupported key: {name}")
        win32.key(vk, True)
        win32.key(vk, False)
        self._log("key", key=name)
        await asyncio.sleep(float(self.s.get("key_pause_ms", 0)) / 1000)

    async def chord(self, *names: str):
        self._ensure()
        win32.chord(*names)
        self._log("chord", keys=list(names))
        await asyncio.sleep(float(self.s.get("key_pause_ms", 0)) / 1000)

    async def select_all(self):
        await self.chord("Ctrl", "A")

    async def insert_text(self, text: str):
        self._ensure()
        gap = float(self.s.get("type_char_ms", 0)) / 1000
        for i, ch in enumerate(text):
            self.gate.check()
            win32.text(ch)
            if gap and i + 1 < len(text):
                await asyncio.sleep(gap)
        self._log("text", text=text)

    async def bring_to_front(self):
        self._alive()
        win32.focus(self.hwnd)

    # ---------------- 等待与导航 ----------------
    async def wait_for(self, getter, predicate, timeout: float, interval: float = 0.05):
        deadline = time.monotonic() + timeout
        while True:
            value = await getter()
            if predicate(value) or time.monotonic() >= deadline:
                return value
            await asyncio.sleep(interval)

    async def _wait_commit(self, before: tuple, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self._alive()
            k = self._page_key()
            if k != before and k[0]:
                self._key = k
                self._frame = None
                STATUS.page_changed()
                return True
            await asyncio.sleep(0.1)
        return False

    async def _stable(self, timeout: float | None) -> WaitResult:
        async def grab():
            return await asyncio.to_thread(self._image)

        async def pending():
            return False
        STATUS.set_wait("after_action", timeout)
        try:
            return await screen.wait_stable(grab, pending, int(self.s["stable_ms"]),
                                            float(timeout or self.s["settle_timeout_s"]))
        finally:
            STATUS.clear_wait()

    async def wait_settled(self, after_navigation: bool = False, timeout: float | None = None) -> WaitResult:
        """动作后：commit_wait_s 内网址 / Document 换了就按导航处理，然后等截图稳定。"""
        t0 = time.monotonic()
        await self._wait_commit(self._key, float(self.s["commit_wait_s"]))
        r = await self._stable(timeout)
        return WaitResult(r.status, int((time.monotonic() - t0) * 1000), r.pending_urls)

    async def _nav(self, do, timeout: float | None, no_change_status: str | None = None) -> WaitResult:
        t0 = time.monotonic()
        before = self._page_key()
        await do()
        committed = await self._wait_commit(before, float(timeout or self.nav_timeout_s)
                                            if no_change_status is None else 3.0)
        if not committed and no_change_status:
            return WaitResult(no_change_status, int((time.monotonic() - t0) * 1000))
        if not committed:
            return WaitResult("timeout", int((time.monotonic() - t0) * 1000), ["（新页面没有提交）"])
        r = await self._stable(timeout)
        return WaitResult(r.status, int((time.monotonic() - t0) * 1000), r.pending_urls)

    async def navigate(self, url: str, timeout: float | None = None) -> WaitResult:
        async def do():
            await self.chord("Ctrl", "L")
            await asyncio.sleep(0.15)
            await self.insert_text(url)
            await self.key_press("Enter")
        return await self._nav(do, timeout)

    async def reload(self, timeout: float | None = None) -> WaitResult:
        async def do():
            await self.key_press("F5")
        return await self._nav(do, timeout)

    async def back(self, timeout: float | None = None) -> WaitResult:
        async def do():
            await self.chord("Alt", "ArrowLeft")
        r = await self._nav(do, timeout, no_change_status="no_history")
        if r.status == "no_history" and self._openers:
            # 和 CDP 驱动一致：新标签页里没有历史时，关掉它、回到打开它的标签页
            t0 = time.monotonic()
            opener = self._openers.pop()
            await self.chord("Ctrl", "W")
            await asyncio.sleep(0.3)
            await self.select_tab(opener)
            self._key = self._page_key()
            STATUS.page_changed()
            s = await self._stable(timeout)
            return WaitResult(s.status, int((time.monotonic() - t0) * 1000), s.pending_urls)
        return r

    # ---------------- 标签页 ----------------
    def _tabs(self) -> list[dict]:
        return self.reader.tabs()

    def _poll_new_tabs(self):
        tabs = self._tabs()
        for t in tabs:
            if t["rid"] and t["rid"] not in self._tabs_known:
                self._tabs_known.add(t["rid"])
                self._spawned.append(t["rid"])

    def take_new_tab(self):
        self._poll_new_tabs()
        return self._spawned.pop(0) if self._spawned else None

    def clear_spawned_tabs(self):
        self._poll_new_tabs()
        self._spawned.clear()

    def take_spawned_tab(self):
        return self.take_new_tab()

    async def select_tab(self, rid) -> bool:
        for _ in range(max(len(self._tabs()), 1) + 1):
            tabs = self._tabs()
            if any(t["rid"] == rid and t["selected"] for t in tabs):
                self._frame = None
                return True
            if not any(t["rid"] == rid for t in tabs):
                return False
            await self.chord("Ctrl", "PageDown")
            await asyncio.sleep(0.25)
        return False

    async def adopt_new_tab(self, rid) -> WaitResult:
        t0 = time.monotonic()
        cur = next((t["rid"] for t in self._tabs() if t["selected"]), None)
        if cur and cur != rid:
            self._openers.append(cur)
        await self.select_tab(rid)
        self._key = self._page_key()
        STATUS.page_changed()
        r = await self._stable(None)
        return WaitResult(r.status, int((time.monotonic() - t0) * 1000), r.pending_urls)

    async def new_tab(self, url: str):
        before = {t["rid"] for t in self._tabs()}
        await self.chord("Ctrl", "T")
        await asyncio.sleep(0.4)
        new = [t["rid"] for t in self._tabs() if t["rid"] not in before]
        self._tabs_known |= set(new)
        try:
            await self.navigate(url)
        except Exception:
            if url != "about:blank" or not new:
                raise
            self._frame = None             # 空白页读不到网页内容区（Document 控件）：标签页已开，下一步导航会重读
        return new[0] if new else None

    async def close_tab(self, rid) -> None:
        tabs = self._tabs()
        if len(tabs) <= 1:
            await self.navigate("about:blank")
            return
        if await self.select_tab(rid):
            await self.chord("Ctrl", "W")
            await asyncio.sleep(0.3)
            self._frame = None
            self._key = self._page_key()

    # ---------------- 兼容接口 ----------------
    async def evaluate(self, expression: str, session=None):
        raise Unsupported("执行页面脚本")

    async def evaluate_in_frame(self, el, expression: str):
        raise Unsupported("执行页面脚本")

    async def frame_info(self, el):
        return (0.0, 0.0), False

    async def refresh_offsets(self):
        return None

    async def panel_session(self):
        return None

    async def fresh_start(self):
        return None
