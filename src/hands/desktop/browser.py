"""DesktopBrowser：在用户默认 Chrome Profile 里新开一个代理窗口（spec F1、F2）。

- 不加任何特殊启动参数、不另指定用户目录：登录状态和 Cookie 与用户平时一致。
- 只在这个窗口里操作；结束时不关窗口。
- 看门狗：窗口不见了 → 代理窗口已关闭（不重跑）；窗口长时间无响应 → 按崩溃处理（重开一个窗口）。
"""
from __future__ import annotations

import asyncio
import sys
import time

from ..config import Config, desktop_settings
from ..driver import DESKTOP_CAPS, DESKTOP_NOT_CHROME, AgentWindowClosed, DriverUnavailable
from ..pause import PauseGate


class DesktopBrowser:
    driver = "desktop"
    capabilities = DESKTOP_CAPS

    @property
    def headless(self) -> bool:
        return False                     # 桌面驱动只能有界面

    @headless.setter
    def headless(self, value):
        pass                             # 面板 / 命令行要求无界面时忽略（start 里会打印提示）

    def __init__(self, config: Config, gate: PauseGate | None = None, log=print):
        self.config = config
        self.s = desktop_settings(config)
        self.page = None
        self.hwnd: int | None = None
        self.crashed = asyncio.Event()
        self.crash_history: list = []
        self.crash_reason: str | None = None
        self.crash_detected_at: float | None = None
        self.closed = False
        self.gate = gate or PauseGate(self.s["idle_resume_s"])
        self.log = log
        self._watch = None
        self._watchdog: asyncio.Task | None = None
        self._pid: int | None = None

    @property
    def pid(self) -> int | None:
        return self._pid

    def check_crashed(self):
        if self.closed:
            raise AgentWindowClosed("代理窗口已关闭")

    async def start(self):
        if sys.platform != "win32":
            from ..driver import DESKTOP_NOT_WINDOWS
            raise DriverUnavailable(DESKTOP_NOT_WINDOWS)
        from . import win32
        from .inputwatch import InputWatch
        from .page import DesktopPage
        from .uia import UiaReader
        if self.config.headless:
            self.log("[desktop] 桌面驱动只能有界面运行，已按有界面运行")
        progid, exe = win32.default_browser()
        if progid != "ChromeHTML" or not exe:
            raise DriverUnavailable(DESKTOP_NOT_CHROME.format(progid=progid or "未知"))
        win32.set_dpi_aware()
        before = win32.chrome_windows()
        win32.launch_new_window(exe, "about:blank")
        hwnd = await asyncio.to_thread(win32.wait_new_window, before, 15.0)
        if not hwnd:
            raise RuntimeError("没有等到新的 Chrome 窗口（15 秒）")
        self.hwnd = hwnd
        self._pid = win32.window_pid(hwnd)
        win32.place(hwnd, tuple(self.s["window"]))
        win32.focus(hwnd)
        await asyncio.sleep(0.8)
        reader = UiaReader(hwnd, float(self.s["uia_timeout_s"]))
        self.page = DesktopPage(self, hwnd, reader, self.gate, self.s, float(self.config.nav_timeout_s))
        self.gate.arm(float(self.s["resume_grace_s"]))     # 启动时的窗口动画、用户刚松开的回车不算
        await self.page.chord("Ctrl", "0")                   # 浏览器缩放复位到 100%
        self.crashed = asyncio.Event()
        self.closed = False
        self.crash_reason = None
        loop = asyncio.get_running_loop()
        self._watch = InputWatch(loop, self.gate.on_human_input).start()
        self._watchdog = loop.create_task(self._watchdog_loop())
        self.log(f"[desktop] 代理窗口 hwnd={hwnd}（默认 Profile），位置 {self.s['window']}")
        return self

    async def _watchdog_loop(self):
        from . import win32
        hung_since = None
        while True:
            await asyncio.sleep(0.5)
            if self.hwnd is None:
                return
            if not win32.alive(self.hwnd):
                self.closed = True
                self._crash("代理窗口已关闭")
                return
            if win32.hung(self.hwnd):
                hung_since = hung_since or time.monotonic()
                if time.monotonic() - hung_since > 10:
                    self._crash("代理窗口无响应超过 10 秒")
                    return
            else:
                hung_since = None

    def _crash(self, reason: str):
        self.crash_reason = reason
        self.crash_detected_at = time.time()
        self.crash_history.append({"t": self.crash_detected_at, "reason": reason})
        self.crashed.set()

    async def stop(self):
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None
        if self._watch is not None:
            self._watch.stop()
            self._watch = None
        # 不关窗口：任务结束后留给用户看

    async def restart(self):
        await self.stop()
        await self.start()

    async def relaunch(self, headless: bool):
        await self.restart()

    async def new_page(self):
        return self.page

    def is_alive(self) -> bool:
        from . import win32
        return self.hwnd is not None and win32.alive(self.hwnd) and not win32.hung(self.hwnd)
