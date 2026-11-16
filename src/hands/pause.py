"""暂停门：统一管理暂停与恢复（人工输入、面板 / 命令行按钮、改树、次数上限），与驱动无关。

- 运行中 check()：已暂停就抛 Interrupted，动作在下一个轨迹点 / 下一个字之前停下。
- 每步开头 await wait()：暂停时阻塞到恢复，返回暂停秒数（不计入任务时限）。
- on_human_input(t)：输入监听线程投递过来；过了宽限期才会触发暂停。
- idle_resume_s > 0：因人工输入暂停后，人停手满 N 秒自动恢复。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass, field

from .status import STATUS


class Interrupted(Exception):
    """动作中途被暂停打断。"""


@dataclass
class PauseInfo:
    reason: str                  # human_input / user / budget / edit / confirm
    since: float                 # time.time()
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["elapsed_s"] = round(time.time() - self.since, 1)
        return d


class PauseGate:
    def __init__(self, idle_resume_s: float = 0, watch_human: bool = True):
        self.idle_resume_s = float(idle_resume_s or 0)
        self.watch_human = watch_human           # False：人工输入不触发暂停（CDP / Playwright）
        self.paused: PauseInfo | None = None
        self.log: list[dict] = []
        self.last_human: float = 0.0             # time.monotonic()
        self._armed_at = 0.0                     # 此时刻之前的人工输入不触发暂停
        self._event = asyncio.Event()
        self._event.set()
        self._idle_task: asyncio.Task | None = None
        self._t0 = 0.0

    # ---------------- 状态 ----------------
    @property
    def is_paused(self) -> bool:
        return self.paused is not None

    def pause(self, reason: str, detail: dict | None = None) -> bool:
        """已经暂停时不覆盖原因，返回 False。"""
        if self.paused is not None:
            return False
        self.paused = PauseInfo(reason, time.time(), dict(detail or {}))
        self._t0 = time.monotonic()
        self._event.clear()
        STATUS.set_pause(self.paused.to_dict())
        if reason == "human_input" and self.idle_resume_s > 0:
            self._start_idle_watch()
        return True

    def resume(self, how: str) -> bool:
        if self.paused is None:
            return False
        self.log.append({"start": self.paused.since, "end": time.time(), "reason": self.paused.reason,
                         "how": how, "detail": self.paused.detail,
                         "duration_s": round(time.monotonic() - self._t0, 2)})
        self.paused = None
        self._event.set()
        if self._idle_task is not None and not self._idle_task.done() and \
                self._idle_task is not _current_task():
            self._idle_task.cancel()
        self._idle_task = None
        STATUS.set_pause(None)
        return True

    def arm(self, after_s: float) -> None:
        """恢复后宽限期：after_s 秒内的人工输入不触发暂停。"""
        self._armed_at = time.monotonic() + max(after_s, 0)

    # ---------------- 运行中 ----------------
    def check(self) -> None:
        if self.paused is not None:
            raise Interrupted(self.paused.reason)

    async def wait(self) -> float:
        if self.paused is None:
            return 0.0
        t = time.monotonic()
        await self._event.wait()
        return time.monotonic() - t

    def on_human_input(self, t: float | None = None, info: dict | None = None) -> None:
        t = time.monotonic() if t is None else t
        self.last_human = t
        if not self.watch_human or self.paused is not None or t < self._armed_at:
            return
        self.pause("human_input", info)          # info：鼠标 / 键盘、移动像素等，写进暂停记录便于排查

    # ---------------- 自动恢复 ----------------
    def _start_idle_watch(self):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._idle_task = loop.create_task(self._idle_watch())

    async def _idle_watch(self):
        while self.paused is not None and self.paused.reason == "human_input":
            quiet = time.monotonic() - self.last_human
            if quiet >= self.idle_resume_s:
                self.resume("idle")
                return
            await asyncio.sleep(min(self.idle_resume_s - quiet, 0.5) + 0.01)


def _current_task():
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None
