"""自写原生 CDP 客户端：一条浏览器级 websocket，flat session 模式。"""
from __future__ import annotations

import asyncio
import inspect
import itertools
import json
from typing import Any, Callable

import websockets


class CDPError(Exception):
    def __init__(self, method: str, code: int, message: str):
        super().__init__(f"{method}: [{code}] {message}")
        self.method, self.code, self.message = method, code, message


class BrowserCrashed(Exception):
    pass


ALL = "*"


class CDPConnection:
    def __init__(self, default_timeout: float = 30):
        self.ws = None
        self.default_timeout = default_timeout
        self._ids = itertools.count(1)
        self._pending: dict[int, tuple[str, asyncio.Future]] = {}
        self._subs: dict[str, list[tuple[str | None, Callable]]] = {}
        self._waiters: list[tuple[str, str | None, Callable | None, asyncio.Future]] = []
        self._reader: asyncio.Task | None = None
        self._closing = False
        self._failure: BaseException | None = None
        self._bg: set[asyncio.Task] = set()
        self.on_lost: Callable | None = None   # 连接意外断开时回调（浏览器管理器用来记录崩溃）

    async def connect(self, ws_url: str):
        self.ws = await websockets.connect(ws_url, max_size=None, ping_interval=None)
        self._reader = asyncio.create_task(self._read_loop())

    @property
    def closed(self) -> bool:
        return self._failure is not None or self.ws is None

    async def close(self):
        self._closing = True
        if self._reader:
            self._reader.cancel()
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass
        self.fail_all(BrowserCrashed("connection closed by client"))

    async def _read_loop(self):
        try:
            async for msg in self.ws:
                self._dispatch(json.loads(msg))
        except asyncio.CancelledError:
            return
        except Exception:
            pass
        if not self._closing:
            if self.on_lost is not None:
                self.on_lost()
            self.fail_all(BrowserCrashed("websocket connection lost"))

    def _dispatch(self, m: dict):
        if "id" in m:
            item = self._pending.pop(m["id"], None)
            if item is None:
                return
            method, fut = item
            if fut.done():
                return
            if "error" in m:
                fut.set_exception(CDPError(method, m["error"].get("code", 0), m["error"].get("message", "")))
            else:
                fut.set_result(m.get("result", {}))
            return
        method = m.get("method")
        if not method:
            return
        sid = m.get("sessionId")
        params = m.get("params", {})
        for want_sid, cb in list(self._subs.get(method, [])):
            if want_sid == ALL or want_sid == sid:
                try:
                    r = cb(params) if _arity(cb) == 1 else cb(params, sid)
                    if inspect.isawaitable(r):
                        t = asyncio.ensure_future(r)
                        self._bg.add(t)
                        t.add_done_callback(self._bg.discard)
                except Exception as e:  # 回调异常不能拖垮读循环
                    print(f"[cdp] handler error for {method}: {e!r}")
        for w in list(self._waiters):
            wm, wsid, pred, fut = w
            if wm == method and (wsid == ALL or wsid == sid) and not fut.done():
                try:
                    ok = pred is None or pred(params)
                except Exception:
                    ok = False
                if ok:
                    fut.set_result(params)
                    self._waiters.remove(w)

    async def send(self, method: str, params: dict | None = None, session_id: str | None = None,
                   timeout: float | None = None) -> dict:
        if self._failure is not None:
            raise self._failure
        mid = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[mid] = (method, fut)
        msg: dict[str, Any] = {"id": mid, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        try:
            await self.ws.send(json.dumps(msg))
        except Exception:
            self._pending.pop(mid, None)
            if self._failure is not None:
                raise self._failure
            raise BrowserCrashed("send failed: connection lost")
        try:
            return await asyncio.wait_for(fut, timeout or self.default_timeout)
        finally:
            self._pending.pop(mid, None)

    def on(self, method: str, cb: Callable, session_id: str | None = None):
        self._subs.setdefault(method, []).append((session_id, cb))

    def off(self, method: str, cb: Callable, session_id: str | None = None):
        lst = self._subs.get(method, [])
        for i, (s, c) in enumerate(lst):
            if c is cb and (session_id is None or s == session_id):
                del lst[i]
                return

    def off_session(self, session_id: str):
        for method, lst in self._subs.items():
            self._subs[method] = [(s, c) for s, c in lst if s != session_id]

    async def wait_event(self, method: str, predicate: Callable | None = None,
                         session_id: str | None = ALL, timeout: float | None = None) -> dict:
        if self._failure is not None:
            raise self._failure
        fut = asyncio.get_running_loop().create_future()
        w = (method, session_id, predicate, fut)
        self._waiters.append(w)
        try:
            return await asyncio.wait_for(fut, timeout or self.default_timeout)
        finally:
            if w in self._waiters:
                self._waiters.remove(w)

    def fail_all(self, exc: BaseException):
        if self._failure is None:
            self._failure = exc
        for _, fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()
        for *_, fut in list(self._waiters):
            if not fut.done():
                fut.set_exception(exc)
        self._waiters.clear()


def _arity(cb) -> int:
    try:
        return len(inspect.signature(cb).parameters)
    except (TypeError, ValueError):
        return 1


class CDPSession:
    def __init__(self, conn: CDPConnection, session_id: str | None, target_id: str | None = None):
        self.conn = conn
        self.session_id = session_id
        self.target_id = target_id

    async def send(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        return await self.conn.send(method, params, self.session_id, timeout)

    def on(self, method: str, cb: Callable):
        self.conn.on(method, cb, self.session_id)

    def off(self, method: str, cb: Callable):
        self.conn.off(method, cb, self.session_id)

    async def wait_event(self, method: str, predicate: Callable | None = None, timeout: float | None = None):
        return await self.conn.wait_event(method, predicate, self.session_id, timeout)


async def attach_page(conn: CDPConnection, target_id: str) -> CDPSession:
    r = await conn.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
    return CDPSession(conn, r["sessionId"], target_id)
