"""Playwright 驱动的浏览器管理：用本机 Chrome + 同一个专用用户目录启动持久化上下文；崩溃检测、重启、有界面切换。"""
from __future__ import annotations

import asyncio
import ctypes
import json
import subprocess
import time
from pathlib import Path

from playwright.async_api import async_playwright

from ..browser import CREATE_NO_WINDOW, IS_WIN, kill_tree, profile_chrome_pids
from ..cdp import BrowserCrashed
from ..config import Config

# 页面加载前把封闭的 Shadow DOM 改成开放，元素读取才能看到里面的元素（spec F3）
OPEN_SHADOW_JS = """(() => {
  const orig = Element.prototype.attachShadow;
  if (orig.__handsOpen) return;
  const patched = function (init) { return orig.call(this, Object.assign({}, init, { mode: 'open' })); };
  patched.__handsOpen = true;
  Element.prototype.attachShadow = patched;
})();"""


def _main_browser_pid(profile_dir: Path) -> int | None:
    """用户目录对应的 Chrome 主进程（命令行里不带 --type= 的那个）。"""
    if not IS_WIN:
        pids = profile_chrome_pids(profile_dir)
        return min(pids) if pids else None
    needle = str(Path(profile_dir)).lower().replace("'", "''")
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
          f"Where-Object {{ $_.CommandLine -and $_.CommandLine.ToLower().Contains('{needle}') "
          "-and -not $_.CommandLine.Contains('--type=') } | Select-Object ProcessId | ConvertTo-Json -Compress")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                         creationflags=CREATE_NO_WINDOW).stdout.strip()
    if not out:
        return None
    data = json.loads(out)
    if isinstance(data, list):
        data = data[0] if data else {}
    return int(data["ProcessId"]) if data.get("ProcessId") else None


def _pid_alive(pid: int) -> bool:
    if not IS_WIN:
        import os
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    k32 = ctypes.WinDLL("kernel32")
    h = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == 259                      # STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


class PwBrowserManager:
    driver = "playwright"

    def __init__(self, config: Config):
        self.config = config
        self.headless = config.headless
        self.context = None
        self._pw = None
        self._pid: int | None = None
        self.port = None                     # Playwright 用管道连接，没有调试端口
        self.version_info: dict = {}
        self.crashed = asyncio.Event()
        self.crash_detected_at: float | None = None
        self.crash_reason: str | None = None
        self.crash_history: list[tuple[float, str]] = []
        self._watchdog_task: asyncio.Task | None = None
        self._stopping = False
        self.page = None

    @property
    def pid(self) -> int | None:
        return self._pid

    def _args(self) -> list[str]:
        w, h = self.config.window
        return [f"--window-size={w},{h}", "--no-first-run", "--no-default-browser-check",
                "--disable-session-crashed-bubble", "--hide-crash-restore-bubble",
                "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
                "--disable-background-timer-throttling",
                # 后退时始终触发 load；关闭窗口遮挡检测（与 CDP 驱动相同）
                "--disable-features=BackForwardCache,CalculateNativeWinOcclusion"]

    async def start(self):
        cfg = self.config
        Path(cfg.profile_dir).mkdir(parents=True, exist_ok=True)
        for pid in await asyncio.to_thread(profile_chrome_pids, cfg.profile_dir):
            kill_tree(pid)
        self._stopping = False
        self.crashed = asyncio.Event()
        self.crash_detected_at = None
        self.crash_reason = None
        self._pw = await async_playwright().start()
        try:
            self.context = await self._pw.chromium.launch_persistent_context(
                str(cfg.profile_dir), executable_path=cfg.chrome_path, headless=self.headless,
                no_viewport=True, args=self._args(), timeout=30000,
                # Playwright 默认加 --no-sandbox；CDP 驱动启动 Chrome 不关沙箱，这里保持一致
                chromium_sandbox=True)
        except Exception:
            await self._pw.stop()
            self._pw = None
            raise
        await self.context.add_init_script(OPEN_SHADOW_JS)
        self.context.on("close", lambda *_: self._mark_crashed("browser closed"))
        self._pid = await asyncio.to_thread(_main_browser_pid, cfg.profile_dir)
        self.version_info = {"Browser": getattr(self.context.browser, "version", "") if self.context.browser else ""}
        self._watchdog_task = asyncio.create_task(self._watchdog())

    def _mark_crashed(self, reason: str):
        if self.crashed.is_set() or self._stopping:
            return
        self.crash_detected_at = time.time()
        self.crash_reason = reason
        self.crash_history.append((self.crash_detected_at, reason))
        self.crashed.set()

    def check_crashed(self):
        if self.crashed.is_set():
            raise BrowserCrashed(self.crash_reason or "browser crashed")

    async def _watchdog(self):
        while not self._stopping:
            await asyncio.sleep(self.config.watchdog_interval_s)
            if self._stopping:
                return
            if self._pid is not None and not _pid_alive(self._pid):
                self._mark_crashed("chrome process exited")
                return

    def is_alive(self) -> bool:
        return (self.context is not None and not self.crashed.is_set()
                and (self._pid is None or _pid_alive(self._pid)))

    async def stop(self):
        self._stopping = True
        if self._watchdog_task:
            self._watchdog_task.cancel()
            self._watchdog_task = None
        if self.page is not None:
            self.page.detach()
            self.page = None
        if self.context is not None:
            try:
                await asyncio.wait_for(self.context.close(), 5)
            except Exception:
                pass
        if self._pw is not None:
            try:
                await asyncio.wait_for(self._pw.stop(), 5)
            except Exception:
                pass
        leftovers = await asyncio.to_thread(profile_chrome_pids, self.config.profile_dir)
        for pid in leftovers:
            kill_tree(pid)
        self.context = None
        self._pw = None
        self._pid = None

    async def restart(self):
        await self.stop()
        await self.start()

    async def relaunch(self, headless: bool):
        await self.stop()
        self.headless = headless
        await self.start()

    async def new_page(self):
        from .page import PwPage

        self.check_crashed()
        pages = self.context.pages
        p = pages[0] if pages else await self.context.new_page()
        if self.page is not None:
            self.page.detach()
        page = PwPage(self, p)
        await page.attach()
        self.page = page
        return page

