"""M2 页面导航与等待；当前标签页会话 + 跨源 iframe 子会话树；新标签页跟随。"""
from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass, field

from .cdp import ALL, CDPConnection, CDPError, CDPSession, attach_page
from .config import Config
from .geometry import Box, quad_to_box
from .monitor import Monitor
from .status import STATUS


@dataclass
class WaitResult:
    status: str                # ok / timeout / no_history
    elapsed_ms: int
    pending_urls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"status": self.status, "elapsed_ms": self.elapsed_ms, "pending_urls": self.pending_urls}


@dataclass
class FrameSession:
    session: CDPSession
    frame_id: str
    parent: "FrameSession | None" = None
    owner_offset: tuple[float, float] = (0.0, 0.0)

    @property
    def depth(self) -> int:
        return 0 if self.parent is None else self.parent.depth + 1


async def wait_quiet(monitor: Monitor, loaded: asyncio.Event, config: Config, timeout: float,
                     setup_tasks=()) -> WaitResult:
    """等待策略（两种驱动共用）：等 load，再等"网络安静"满 quiet_ms；超时返回未完成的请求地址。"""
    start = time.monotonic()
    deadline = start + timeout
    quiet = config.quiet_ms / 1000

    def result(status):
        return WaitResult(status, int((time.monotonic() - start) * 1000),
                          monitor.pending_urls() if status != "ok" else [])

    if not loaded.is_set():
        STATUS.set_wait("load", timeout)
        try:
            await asyncio.wait_for(loaded.wait(), max(0.0, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            return result("timeout")
    if setup_tasks:
        await asyncio.wait(list(setup_tasks), timeout=max(0.0, deadline - time.monotonic()))
    STATUS.set_wait("network_quiet", timeout)
    STATUS.wait.since = start
    while True:
        now = time.monotonic()
        pending = monitor.blocking_count()
        # 安静时间至少从本次等待开始算：按键/点击触发的跳转要稍后才发出请求
        quiet_for = now - max(monitor.quiet_since(), start)
        if pending == 0 and quiet_for >= quiet and loaded.is_set():
            return result("ok")
        remaining = deadline - now
        if remaining <= 0:
            return result("timeout")
        if not loaded.is_set():
            try:
                await asyncio.wait_for(loaded.wait(), remaining)
            except asyncio.TimeoutError:
                return result("timeout")
            continue
        limit = remaining if pending else min(quiet - quiet_for, remaining)
        release = monitor.next_release_in()   # 到点时后台长连接不再阻塞，需要醒来重算
        if release is not None:
            limit = min(limit, release + 0.01)
        await monitor.wait_change(limit)


# 支持的按键：名字 → (key/code, windowsVirtualKeyCode, 产生的文字)；名字同时也是 Playwright 的键名
KEYS = {
    "Enter": ("Enter", 13, "\r"), "Tab": ("Tab", 9, None), "Escape": ("Escape", 27, None),
    "Backspace": ("Backspace", 8, None), "ArrowDown": ("ArrowDown", 40, None),
    "ArrowUp": ("ArrowUp", 38, None), "PageDown": ("PageDown", 34, None), "End": ("End", 35, None),
}


AUTO_ATTACH = {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True,
               "filter": [{"type": "iframe"}, {"type": "page", "exclude": True}]}


class Page:
    driver = "cdp"

    def __init__(self, conn: CDPConnection, config: Config):
        self.conn = conn
        self.config = config
        self.session: CDPSession | None = None
        self.target_id: str | None = None
        self.monitor = Monitor()
        self.main_frame_id: str | None = None
        self.frames: dict[str, FrameSession] = {}   # session_id -> FrameSession（含主会话）
        self._loaded = asyncio.Event()
        self._setup_tasks: set[asyncio.Task] = set()
        self._subs: list[tuple] = []
        self._openers: list[str] = []
        self._new_tabs: asyncio.Queue[str] = asyncio.Queue()
        self._spawned: asyncio.Queue[str] = asyncio.Queue()   # 没有打开者关系的新标签页（open_tab 用）
        self._discover_on = False

    # ---------------- 会话管理 ----------------
    def _sub(self, method, cb, session_id):
        self.conn.on(method, cb, session_id)
        self._subs.append((method, cb, session_id))

    async def attach(self, target_id: str):
        self.target_id = target_id
        self.monitor = Monitor()
        self._loaded = asyncio.Event()
        s = await attach_page(self.conn, target_id)
        self.session = s
        main = FrameSession(s, target_id)
        self.frames = {s.session_id: main}
        self._sub("Page.loadEventFired", lambda p: self._loaded.set(), s.session_id)
        self._sub("Page.frameStartedLoading", self._on_frame_started, s.session_id)
        self._sub("Target.attachedToTarget", self._on_attached, ALL)
        self._sub("Target.detachedFromTarget", self._on_detached, ALL)
        for d in ("Page", "Network", "Runtime", "Log", "DOM"):
            await s.send(f"{d}.enable")
        await s.send("Page.setLifecycleEventsEnabled", {"enabled": True})
        # 让页面始终按"有焦点"处理：否则在部分无头环境（以及窗口被遮挡时）滚轮等输入事件会一直等不到确认
        await s.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
        tree = await s.send("Page.getFrameTree")
        self.main_frame_id = tree["frameTree"]["frame"]["id"]
        main.frame_id = self.main_frame_id
        self.monitor.attach_session(s, self.main_frame_id)
        await self._auto_attach(s)
        if not self._discover_on:
            self._sub("Target.targetCreated", self._on_target_created, None)
            await self.conn.send("Target.setDiscoverTargets", {"discover": True})
            self._discover_on = True
        try:
            r = await s.send("Runtime.evaluate", {"expression": "document.readyState", "returnByValue": True})
            if r.get("result", {}).get("value") == "complete":
                self._loaded.set()
        except CDPError:
            pass
        STATUS.page_changed()

    async def _auto_attach(self, s: CDPSession):
        try:
            await s.send("Target.setAutoAttach", AUTO_ATTACH)
        except CDPError:
            await s.send("Target.setAutoAttach", {k: v for k, v in AUTO_ATTACH.items() if k != "filter"})

    def _on_frame_started(self, p):
        if p.get("frameId") == self.main_frame_id:
            self._loaded.clear()

    def _on_attached(self, p, parent_sid):
        if parent_sid not in self.frames:
            return
        info = p["targetInfo"]
        child = CDPSession(self.conn, p["sessionId"], info["targetId"])
        if info.get("type") != "iframe":
            t = asyncio.ensure_future(child.send("Runtime.runIfWaitingForDebugger"))
            t.add_done_callback(lambda f: f.exception())
            return
        fs = FrameSession(child, info["targetId"], parent=self.frames[parent_sid])
        self.frames[child.session_id] = fs
        self.monitor.attach_session(child)          # 先订阅，再放行，保证不漏早期请求
        self.monitor.frame_committed(info["targetId"])
        task = asyncio.ensure_future(self._setup_child(child))
        self._setup_tasks.add(task)
        task.add_done_callback(self._setup_tasks.discard)

    async def _setup_child(self, child: CDPSession):
        try:
            for d in ("Network", "Runtime", "Log", "DOM", "Page"):
                await child.send(f"{d}.enable")
            await self._auto_attach(child)
            await child.send("Runtime.runIfWaitingForDebugger")
        except CDPError:
            pass  # 子 frame 已被移除

    def _on_detached(self, p, parent_sid):
        sid = p.get("sessionId")
        if sid in self.frames and sid != self.session.session_id:
            del self.frames[sid]
            self.monitor.detach_session(sid)
            self.conn.off_session(sid)

    def _on_target_created(self, p):
        info = p["targetInfo"]
        if info.get("type") != "page":
            return
        if info.get("openerId") == self.target_id:
            self._new_tabs.put_nowait(info["targetId"])
        else:                                   # Ctrl+点击打开的标签页没有打开者关系，单独记下供 open_tab 使用
            self._spawned.put_nowait(info["targetId"])

    def detach(self):
        self.monitor.detach_all()
        for method, cb, sid in self._subs:
            self.conn.off(method, cb, sid)
        self._subs = []
        self._discover_on = False
        for sid in list(self.frames):
            self.conn.off_session(sid)

    async def release(self):
        """切换目标前调用：取消订阅并真正断开 CDP 会话。旧会话若仍挂着，
        浏览器会把之后的 iframe 自动接入交给旧会话，新会话就收不到。"""
        sid = self.session.session_id if self.session else None
        self.detach()
        if sid:
            try:
                await self.conn.send("Target.detachFromTarget", {"sessionId": sid}, timeout=5)
            except CDPError:
                pass

    def sessions(self) -> list[FrameSession]:
        return sorted(self.frames.values(), key=lambda f: f.depth)

    def frame_of(self, session_id: str | None) -> FrameSession:
        return self.frames.get(session_id) or self.frames[self.session.session_id]

    async def refresh_offsets(self):
        """跨源 iframe：在父会话找到 <iframe> 元素，取其内容框左上角，逐层累加为主页面坐标偏移。"""
        for fs in self.sessions():
            if fs.parent is None:
                continue
            try:
                owner = await fs.parent.session.send("DOM.getFrameOwner", {"frameId": fs.frame_id})
                model = await fs.parent.session.send("DOM.getBoxModel", {"backendNodeId": owner["backendNodeId"]})
                c = model["model"]["content"]
                px, py = fs.parent.owner_offset
                fs.owner_offset = (px + c[0], py + c[1])
            except CDPError:
                pass

    # ---------------- 导航与等待 ----------------
    def take_new_tab(self) -> str | None:
        try:
            return self._new_tabs.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def clear_spawned_tabs(self):
        while not self._spawned.empty():
            self._spawned.get_nowait()

    def take_spawned_tab(self) -> str | None:
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
        try:
            return await self._wait_settled(timeout)
        finally:
            STATUS.clear_wait()

    async def _wait_settled(self, timeout: float) -> WaitResult:
        return await wait_quiet(self.monitor, self._loaded, self.config, timeout, self._setup_tasks)

    async def navigate(self, url: str, timeout: float | None = None) -> WaitResult:
        self.monitor.reset_for_navigation()
        self._loaded.clear()
        r = await self.session.send("Page.navigate", {"url": url})
        if not r.get("loaderId"):     # 同文档导航（锚点）不会触发 load
            self._loaded.set()
        return await self.wait_settled(True, timeout)

    async def reload(self, timeout: float | None = None) -> WaitResult:
        self.monitor.reset_for_navigation()
        self._loaded.clear()
        await self.session.send("Page.reload")
        return await self.wait_settled(True, timeout)

    async def adopt_new_tab(self, target_id: str) -> WaitResult:
        self._openers.append(self.target_id)
        await self.release()
        await self.attach(target_id)
        return await self.wait_settled(True)

    async def back(self, timeout: float | None = None) -> WaitResult:
        start = time.monotonic()
        h = await self.session.send("Page.getNavigationHistory")
        idx = h["currentIndex"]
        if idx > 0:
            self.monitor.reset_for_navigation()
            self._loaded.clear()
            await self.session.send("Page.navigateToHistoryEntry", {"entryId": h["entries"][idx - 1]["id"]})
            return await self.wait_settled(True, timeout)
        if self._openers:
            opener = self._openers.pop()
            current = self.target_id
            await self.release()
            await self.conn.send("Target.closeTarget", {"targetId": current})
            await self.attach(opener)
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

    async def evaluate(self, expression: str, session: CDPSession | None = None):
        """只读取值（不改页面、不常驻脚本）。"""
        r = await (session or self.session).send("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        if "exceptionDetails" in r:
            raise CDPError("Runtime.evaluate", -1, str(r["exceptionDetails"].get("text")))
        return r.get("result", {}).get("value")

    async def url(self) -> str:
        h = await self.session.send("Page.getNavigationHistory")
        return h["entries"][h["currentIndex"]]["url"]

    async def title(self) -> str:
        return await self.evaluate("document.title") or ""

    # ---------------- 底层原语（动作、截图、页面状态通过这些方法访问浏览器） ----------------
    async def read_dom(self):
        from .dom import DomReader
        return await DomReader().read(self)

    async def layout(self) -> dict:
        m = await self.session.send("Page.getLayoutMetrics")
        lv = m.get("cssLayoutViewport", {})
        cs = m.get("cssContentSize") or m.get("contentSize") or {}
        return {"vw": float(lv.get("clientWidth", 800)), "vh": float(lv.get("clientHeight", 600)),
                "scroll_x": lv.get("pageX", 0), "scroll_y": m.get("cssVisualViewport", {}).get("pageY", 0),
                "layout_y": lv.get("pageY", 0),
                "content_w": cs.get("width", 0), "content_h": cs.get("height", 0)}

    async def locate_box(self, el) -> Box:
        if el._ref is None:
            raise LookupError(f"element {el.id} has no node reference")
        fs = self.frame_of(el._ref.session_id)
        chain = []
        f = fs
        while f.parent is not None:
            chain.append(f)
            f = f.parent
        for child in reversed(chain):          # 先把各级 <iframe> 滚到可见
            owner = await child.parent.session.send("DOM.getFrameOwner", {"frameId": child.frame_id})
            await child.parent.session.send("DOM.scrollIntoViewIfNeeded", {"backendNodeId": owner["backendNodeId"]})
        await fs.session.send("DOM.scrollIntoViewIfNeeded", {"backendNodeId": el._ref.backend_node_id})
        await self.refresh_offsets()
        q = await fs.session.send("DOM.getContentQuads", {"backendNodeId": el._ref.backend_node_id})
        if not q.get("quads"):
            raise LookupError(f"element {el.id} has no layout")
        return quad_to_box(q["quads"][0]).offset(*fs.owner_offset)

    async def node_value(self, el) -> str | None:
        """读节点当前的 value（input / textarea）或可编辑元素的文字；读不到返回 None。只读，不改页面。"""
        if el._ref is None:
            return None
        try:
            fs = self.frame_of(el._ref.session_id)
            obj = await fs.session.send("DOM.resolveNode", {"backendNodeId": el._ref.backend_node_id})
            r = await fs.session.send("Runtime.callFunctionOn", {
                "objectId": obj["object"]["objectId"], "returnByValue": True,
                "functionDeclaration": "function(){return 'value' in this ? this.value : "
                                       "(this.isContentEditable ? this.innerText : null)}"})
            v = r.get("result", {}).get("value")
            return None if v is None else str(v)
        except Exception:
            return None

    async def mouse_move(self, x: float, y: float):
        await self.session.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})

    async def mouse_click(self, x: float, y: float, ctrl: bool = False):
        s = self.session
        mod = {"modifiers": 2} if ctrl else {}            # 2 = Ctrl：链接在新标签页打开
        await s.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left",
                                                  "clickCount": 1, **mod})
        await s.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left",
                                                  "clickCount": 1, **mod})

    async def mouse_wheel(self, x: float, y: float, dy: float):
        s = self.session
        await s.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        await s.send("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": x, "y": y, "deltaX": 0, "deltaY": dy})

    async def key_press(self, name: str):
        key, vk, text = KEYS[name]
        s = self.session
        down = {"type": "keyDown" if text else "rawKeyDown", "key": key, "code": key, "windowsVirtualKeyCode": vk}
        if text:
            down["text"] = text
        await s.send("Input.dispatchKeyEvent", down)
        await s.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": key, "code": key, "windowsVirtualKeyCode": vk})

    async def select_all(self):
        s = self.session
        await s.send("Input.dispatchKeyEvent", {"type": "rawKeyDown", "key": "a", "code": "KeyA",
                                                "windowsVirtualKeyCode": 65, "modifiers": 2, "commands": ["selectAll"]})
        await s.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "code": "KeyA",
                                                "windowsVirtualKeyCode": 65, "modifiers": 2})

    async def insert_text(self, text: str):
        await self.session.send("Input.insertText", {"text": text})

    async def screenshot(self, full_page: bool = False) -> bytes:
        params: dict = {"format": "png"}
        if full_page:
            m = await self.session.send("Page.getLayoutMetrics")
            cs = m.get("cssContentSize") or m.get("contentSize")
            params.update({"captureBeyondViewport": True,
                           "clip": {"x": 0, "y": 0, "width": cs["width"], "height": cs["height"], "scale": 1}})
        r = await self.session.send("Page.captureScreenshot", params)
        return base64.b64decode(r["data"])

    async def bring_to_front(self):
        await self.session.send("Page.bringToFront")

    async def frame_info(self, el) -> tuple[tuple[float, float], bool]:
        """元素所在 frame 在主页面中的偏移，以及该 frame 是否运行在独立会话（跨源 iframe）中。"""
        fs = self.frame_of(el._ref.session_id if el._ref else None)
        return fs.owner_offset, fs.parent is not None

    async def evaluate_in_frame(self, el, expression: str):
        fs = self.frame_of(el._ref.session_id if el._ref else None)
        return await self.evaluate(expression, fs.session)

    async def panel_session(self):
        """面板专用（推流、预览手动输入）：返回当前标签页的 CDP 会话。"""
        return self.session

    async def fresh_start(self):
        """用例开始时调用：CDP 驱动没有需要回收的东西。"""
        return None
