"""M4 状态监控：订阅所有会话的网络、console、生命周期事件，维护 MonitorState。"""
from __future__ import annotations

import asyncio
import copy
import time
from dataclasses import asdict, dataclass, field


@dataclass
class RequestInfo:
    url: str
    started_at: float
    frame_id: str | None = None
    status: int | None = None
    error_text: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ConsoleEntry:
    source: str
    level: str
    text: str
    url: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MonitorState:
    lifecycle: str = "init"
    pending: list[RequestInfo] = field(default_factory=list)
    failed: list[RequestInfo] = field(default_factory=list)
    console_errors: list[ConsoleEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "lifecycle": self.lifecycle,
            "pending": [r.to_dict() for r in self.pending],
            "failed": [r.to_dict() for r in self.failed],
            "console_errors": [c.to_dict() for c in self.console_errors],
        }


# Ping = sendBeacon / <a ping>：发出后不等回应，常收不到结束事件
IGNORED_TYPES = {"WebSocket", "EventSource", "Ping"}
# load 之后才发出、挂了这么久仍未结束的请求视为后台长连接（长轮询、埋点），不再阻塞"网络安静"
LONG_POLL_S = 3.0


class Monitor:
    def __init__(self):
        self._pending: dict[str, RequestInfo] = {}
        self._owner: dict[str, tuple] = {}   # requestId -> (session_id, resource type)
        self._late: dict[str, float] = {}    # load 之后发出的请求 -> 发出时刻（monotonic）
        self._failed: list[RequestInfo] = []
        self._failed_keys: set[str] = set()
        self._console: list[ConsoleEntry] = []
        self.lifecycle = "init"
        self._last_change = time.monotonic()
        self._changed = asyncio.Event()
        self._subs: list[tuple] = []  # (session, method, cb)
        self.main_frame_id: str | None = None

    # ---- 订阅 ----
    def attach_session(self, session, main_frame_id: str | None = None):
        if main_frame_id:
            self.main_frame_id = main_frame_id
        sid = session.session_id
        handlers = {
            "Network.requestWillBeSent": lambda p: self._on_request(sid, p),
            "Network.responseReceived": lambda p: self._on_response(sid, p),
            "Network.loadingFinished": lambda p: self._on_finished(sid, p),
            "Network.loadingFailed": lambda p: self._on_failed(sid, p),
            "Runtime.consoleAPICalled": self._on_console,
            "Runtime.exceptionThrown": self._on_exception,
            "Log.entryAdded": self._on_log,
            "Page.lifecycleEvent": self._on_lifecycle,
        }
        for method, cb in handlers.items():
            session.on(method, cb)
            self._subs.append((session, method, cb))

    def frame_committed(self, frame_id: str):
        """跨源子 frame 已接入：其文档请求已提交给子进程，父会话里不会再收到结束事件。"""
        for key, info in list(self._pending.items()):
            if info.frame_id == frame_id and self._owner.get(key, (None, None))[1] == "Document":
                self._drop(key)
        self._touch()

    def detach_session(self, session_id: str | None):
        for key in [k for k, (sid, _) in self._owner.items() if sid == session_id]:
            self._drop(key)
        keep = []
        for s, m, cb in self._subs:
            if s.session_id == session_id:
                s.off(m, cb)
            else:
                keep.append((s, m, cb))
        self._subs = keep
        self._touch()

    def detach_all(self):
        for s, m, cb in self._subs:
            s.off(m, cb)
        self._subs = []

    # ---- 事件处理 ----
    def _touch(self):
        self._last_change = time.monotonic()
        self._changed.set()

    # ---- 记账层（与驱动无关：CDP 和 Playwright 都把事件转换后调这里） ----
    # 请求只按 key 记账（CDP 为 requestId）：跨源 iframe 的文档请求在父会话发出、在子会话结束，requestId 相同
    def req_started(self, key, url: str, rtype: str | None, frame_id: str | None = None, owner=None):
        if rtype in IGNORED_TYPES:
            return
        if url.startswith(("data:", "blob:")):      # 页面内存里的数据，不走网络
            return
        if key in self._pending:  # redirect
            self._pending[key].url = url
        else:
            self._pending[key] = RequestInfo(url=url, started_at=time.time(), frame_id=frame_id)
            self._owner[key] = (owner, rtype)
            if rtype != "Document" and self.PHASES.index(self.lifecycle) >= self.PHASES.index("load"):
                self._late[key] = time.monotonic()
            elif rtype in (None, "Other", "Script"):
                # worker 脚本这类：结束事件常发到 worker 自己的会话，页面会话里收不到；挂满 LONG_POLL_S 就不再阻塞。
                # Script 也算（Google 的 recaptcha/enterprise/webworker.js）：同文档改网址后生命周期不再到 load，
                # 不能等 load 再计时；真正拖住页面的脚本会推迟 load，等待逻辑先等 load，不受影响
                # 页面自己的 fetch / XHR 不在此列，照旧阻塞到超时（T07）
                self._late[key] = time.monotonic()
        self._touch()

    def req_response(self, key, status: int | None, url: str = "", frame_id: str | None = None):
        if status is not None and status >= 400 and key not in self._failed_keys:
            self._failed.append(RequestInfo(url=url, started_at=time.time(), frame_id=frame_id, status=status))
            self._failed_keys.add(key)
        if key in self._pending:
            self._pending[key].status = status
            # 已收到响应头的非文档请求：从这时起计时，挂满 LONG_POLL_S 仍没结束（流式响应、交给 worker 的脚本）就不再阻塞
            if self._owner.get(key, (None, None))[1] != "Document" and key not in self._late:
                self._late[key] = time.monotonic()

    def req_finished(self, key):
        if self._drop(key) is not None:
            self._touch()

    def req_failed(self, key, error_text: str | None):
        info = self._drop(key)
        if key not in self._failed_keys and info is not None:
            info.error_text = error_text
            self._failed.append(info)
            self._failed_keys.add(key)
        self._touch()

    def console_error(self, entry: ConsoleEntry):
        self._console.append(entry)

    def set_lifecycle(self, name: str):
        if name == "init":
            self.lifecycle = "init"
        elif name in self.PHASES and self.PHASES.index(name) > self.PHASES.index(self.lifecycle):
            self.lifecycle = name

    def _drop(self, key) -> RequestInfo | None:
        self._owner.pop(key, None)
        self._late.pop(key, None)
        return self._pending.pop(key, None)

    # ---- CDP 事件层：解析参数后调记账层 ----
    def _on_request(self, sid, p):
        self.req_started(p["requestId"], p.get("request", {}).get("url", ""), p.get("type"), p.get("frameId"), sid)

    def _on_response(self, sid, p):
        r = p.get("response", {})
        self.req_response(p["requestId"], r.get("status"), r.get("url", ""), p.get("frameId"))

    def _on_finished(self, sid, p):
        self.req_finished(p["requestId"])

    def _on_failed(self, sid, p):
        self.req_failed(p["requestId"], p.get("errorText"))

    def _on_console(self, p):
        if p.get("type") not in ("error", "assert"):
            return
        parts = []
        for a in p.get("args", []):
            parts.append(str(a.get("value", a.get("description", ""))))
        self.console_error(ConsoleEntry("console", "error", " ".join(parts)))

    def _on_exception(self, p):
        d = p.get("exceptionDetails", {})
        text = d.get("exception", {}).get("description") or d.get("text", "")
        self.console_error(ConsoleEntry("exception", "error", text, d.get("url")))

    def _on_log(self, p):
        e = p.get("entry", {})
        if e.get("level") == "error":
            self.console_error(ConsoleEntry(e.get("source", "log"), "error", e.get("text", ""), e.get("url")))

    PHASES = ["init", "DOMContentLoaded", "load", "networkAlmostIdle", "networkIdle"]

    def _on_lifecycle(self, p):
        if self.main_frame_id is not None and p.get("frameId") != self.main_frame_id:
            return
        self.set_lifecycle(p.get("name"))

    # ---- 查询 ----
    def pending_count(self) -> int:
        return len(self._pending)

    def blocking_count(self) -> int:
        """仍在阻塞"网络安静"的请求数：不含已挂满 LONG_POLL_S 的后台长连接。"""
        now = time.monotonic()
        return len(self._pending) - sum(1 for t in self._late.values() if now - t >= LONG_POLL_S)

    def next_release_in(self) -> float | None:
        """距离下一个 load 后请求被视为后台长连接还有多久；没有则为 None。"""
        now = time.monotonic()
        left = [LONG_POLL_S - (now - t) for t in self._late.values() if now - t < LONG_POLL_S]
        return min(left) if left else None

    def pending_urls(self) -> list[str]:
        return [r.url for r in self._pending.values()]

    def quiet_since(self) -> float:
        return self._last_change

    async def wait_change(self, timeout: float):
        self._changed.clear()
        try:
            await asyncio.wait_for(self._changed.wait(), timeout)
        except asyncio.TimeoutError:
            pass

    def snapshot(self) -> MonitorState:
        return MonitorState(
            lifecycle=self.lifecycle,
            pending=copy.deepcopy(list(self._pending.values())),
            failed=copy.deepcopy(self._failed),
            console_errors=copy.deepcopy(self._console),
        )

    def reset_for_navigation(self):
        """主页面即将导航：旧文档的请求会被中止且不一定上报结束事件，一并清空。"""
        self._pending.clear()
        self._owner.clear()
        self._late.clear()
        self._touch()
        self._failed = []
        self._failed_keys = set()
        self._console = []
        self.lifecycle = "init"
