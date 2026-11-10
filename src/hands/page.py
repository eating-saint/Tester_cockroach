"""M2 页面导航与等待；当前标签页会话 + 跨源 iframe 子会话树；新标签页跟随。"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from .cdp import ALL, CDPConnection, CDPError, CDPSession, attach_page
from .config import Config
from .monitor import Monitor


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


AUTO_ATTACH = {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True,
               "filter": [{"type": "iframe"}, {"type": "page", "exclude": True}]}


class Page:
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
        if info.get("type") == "page" and info.get("openerId") == self.target_id:
            self._new_tabs.put_nowait(info["targetId"])

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

    async def wait_settled(self, after_navigation: bool = False, timeout: float | None = None) -> WaitResult:
        timeout = self.config.nav_timeout_s if timeout is None else timeout
        start = time.monotonic()
        deadline = start + timeout
        quiet = self.config.quiet_ms / 1000

        def result(status):
            return WaitResult(status, int((time.monotonic() - start) * 1000),
                              self.monitor.pending_urls() if status != "ok" else [])

        if not self._loaded.is_set():
            try:
                await asyncio.wait_for(self._loaded.wait(), max(0.0, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                return result("timeout")
        if self._setup_tasks:
            await asyncio.wait(list(self._setup_tasks), timeout=max(0.0, deadline - time.monotonic()))
        while True:
            now = time.monotonic()
            pending = self.monitor.blocking_count()
            # 安静时间至少从本次等待开始算：按键/点击触发的跳转要稍后才发出请求
            quiet_for = now - max(self.monitor.quiet_since(), start)
            if pending == 0 and quiet_for >= quiet and self._loaded.is_set():
                return result("ok")
            remaining = deadline - now
            if remaining <= 0:
                return result("timeout")
            if not self._loaded.is_set():
                try:
                    await asyncio.wait_for(self._loaded.wait(), remaining)
                except asyncio.TimeoutError:
                    return result("timeout")
                continue
            limit = remaining if pending else min(quiet - quiet_for, remaining)
            release = self.monitor.next_release_in()   # 到点时后台长连接不再阻塞，需要醒来重算
            if release is not None:
                limit = min(limit, release + 0.01)
            await self.monitor.wait_change(limit)

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
