"""进程内实时状态（给控制面板等观察者用）。

hands 在等待、阶段切换、动作结束、换页时更新它；没有订阅者时开销只是几次属性赋值。
hands 不依赖任何观察者：订阅者回调里的异常一律吞掉，不影响主流程。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable


@dataclass
class WaitInfo:
    what: str                 # "load" | "network_quiet" | "after_action" | "think" | "human"
    since: float              # time.monotonic()
    timeout_s: float | None = None

    def to_dict(self) -> dict:
        return {"what": self.what, "elapsed_ms": int((time.monotonic() - self.since) * 1000),
                "timeout_ms": int(self.timeout_s * 1000) if self.timeout_s is not None else None}


class LiveStatus:
    def __init__(self):
        self.phase: str | None = None
        self.wait: WaitInfo | None = None
        self.page_epoch: int = 0
        self.last_action: dict | None = None
        self.pause: dict | None = None
        self._subs: list[Callable] = []

    def subscribe(self, cb: Callable[[str, dict], None]) -> Callable[[], None]:
        """cb(event_name, payload)。返回取消订阅函数。"""
        self._subs.append(cb)

        def cancel():
            try:
                self._subs.remove(cb)
            except ValueError:
                pass
        return cancel

    def _emit(self, name: str, payload: dict | None = None):
        for cb in list(self._subs):
            try:
                cb(name, payload or {})
            except Exception:
                pass

    def set_wait(self, what: str, timeout_s: float | None = None):
        self.wait = WaitInfo(what, time.monotonic(), timeout_s)
        self._emit("wait", {"what": what})

    def clear_wait(self):
        self.wait = None
        self._emit("wait", {"what": None})

    def set_phase(self, phase: str | None):
        self.phase = phase
        self._emit("phase", {"phase": phase})

    def page_changed(self):
        self.page_epoch += 1
        self._emit("page_changed", {"epoch": self.page_epoch})

    def set_pause(self, info: dict | None):
        """暂停 / 恢复（桌面驱动人工输入、面板按钮、改树、次数上限）。"""
        self.pause = info
        self._emit("pause", {"pause": info})

    def action_done(self, info: dict):
        self.last_action = info
        self._emit("action_done", info)


STATUS = LiveStatus()
