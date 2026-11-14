"""面板专用：把 Playwright 的 CDPSession 包成与 hands.cdp.CDPSession 相同的 send / on / off 接口，
供推流（Page.startScreencast）、预览手动输入、布局度量复用现有面板代码。hands 的动作与读取不使用它。"""
from __future__ import annotations

import asyncio

from ..cdp import CDPError


class PwCdpSession:
    def __init__(self, raw):
        self.raw = raw
        self.session_id = f"pw-{id(raw)}"
        self._wrapped: dict = {}

    async def send(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        try:
            return await asyncio.wait_for(self.raw.send(method, params or {}), timeout or 30)
        except asyncio.TimeoutError as e:
            raise CDPError(method, -1, "timeout") from e
        except Exception as e:
            raise CDPError(method, -1, str(e).splitlines()[0] if str(e) else type(e).__name__) from e

    def on(self, method: str, cb):
        self.raw.on(method, cb)

    def off(self, method: str, cb):
        try:
            self.raw.remove_listener(method, cb)
        except Exception:
            pass

    def detach(self):
        t = asyncio.ensure_future(self.raw.detach())
        t.add_done_callback(lambda f: f.cancelled() or f.exception())
