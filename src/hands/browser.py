"""M1 浏览器管理：启动/连接/关闭 Chrome，看门狗检测崩溃，重启，切换有界面。"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

from .cdp import BrowserCrashed, CDPConnection
from .config import Config

IS_WIN = sys.platform == "win32"
CREATE_NEW_PROCESS_GROUP = 0x00000200 if IS_WIN else 0
CREATE_NO_WINDOW = 0x08000000 if IS_WIN else 0


def _posix_profile_pids(profile_dir: Path) -> list[int]:
    out = subprocess.run(["pgrep", "-f", "--", f"--user-data-dir={profile_dir}"], capture_output=True, text=True).stdout
    return [int(x) for x in out.split() if x.strip().isdigit() and int(x) != os.getpid()]


def profile_chrome_pids(profile_dir: Path) -> list[int]:
    """按命令行中的 user-data-dir 找出属于该 profile 的 chrome.exe 进程。"""
    if not IS_WIN:
        return _posix_profile_pids(profile_dir)
    needle = str(Path(profile_dir)).lower().replace("'", "''")
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
          f"Where-Object {{ $_.CommandLine -and $_.CommandLine.ToLower().Contains('{needle}') }} | "
          "Select-Object ProcessId, WorkingSetSize | ConvertTo-Json -Compress")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                         creationflags=CREATE_NO_WINDOW).stdout.strip()
    if not out:
        return []
    data = json.loads(out)
    if isinstance(data, dict):
        data = [data]
    return [int(d["ProcessId"]) for d in data]


def profile_chrome_ws_mb(profile_dir: Path) -> float:
    if not IS_WIN:
        total = 0
        for pid in _posix_profile_pids(profile_dir):
            try:
                for line in open(f"/proc/{pid}/status"):
                    if line.startswith("VmRSS:"):
                        total += int(line.split()[1])
            except OSError:
                pass
        return total / 1024
    needle = str(Path(profile_dir)).lower().replace("'", "''")
    ps = ("(Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
          f"Where-Object {{ $_.CommandLine -and $_.CommandLine.ToLower().Contains('{needle}') }} | "
          "Measure-Object -Property WorkingSetSize -Sum).Sum")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                         creationflags=CREATE_NO_WINDOW).stdout.strip()
    return float(out or 0) / 1024 / 1024


def kill_tree(pid: int):
    if not IS_WIN:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                   creationflags=CREATE_NO_WINDOW)


class BrowserManager:
    def __init__(self, config: Config):
        self.config = config
        self.headless = config.headless
        self.proc: subprocess.Popen | None = None
        self.conn: CDPConnection | None = None
        self.port: int | None = None
        self.version_info: dict = {}
        self.crashed = asyncio.Event()
        self.crash_detected_at: float | None = None
        self.crash_reason: str | None = None
        self.crash_history: list[tuple[float, str]] = []   # 跨重启保留
        self._watchdog_task: asyncio.Task | None = None
        self._stopping = False
        self.page = None  # 当前 Page

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    def _args(self) -> list[str]:
        w, h = self.config.window
        args = [self.config.chrome_path, "--remote-debugging-port=0",
                f"--user-data-dir={self.config.profile_dir}",
                "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
                "--disable-session-crashed-bubble", "--hide-crash-restore-bubble",
                # 后退时始终触发 load，等待策略才确定；关闭窗口遮挡检测，有界面窗口被挡住时照常绘制
                "--disable-features=BackForwardCache,CalculateNativeWinOcclusion",
                "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
                "--disable-background-timer-throttling",
                f"--window-size={w},{h}"]
        if self.headless:
            args.append("--headless")
        if not IS_WIN and hasattr(os, "geteuid") and os.geteuid() == 0:
            args.append("--no-sandbox")          # 仅 Linux 以 root 运行时需要（例如容器里）
        args.append("about:blank")
        return args

    async def start(self):
        cfg = self.config
        Path(cfg.profile_dir).mkdir(parents=True, exist_ok=True)
        for pid in profile_chrome_pids(cfg.profile_dir):  # 同一 profile 的残留实例会吞掉新启动
            kill_tree(pid)
        port_file = Path(cfg.profile_dir) / "DevToolsActivePort"
        port_file.unlink(missing_ok=True)
        extra = {"creationflags": CREATE_NEW_PROCESS_GROUP} if IS_WIN else {"start_new_session": True}
        self.proc = subprocess.Popen(self._args(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **extra)
        # 条件轮询：等端口文件出现（进程级事件，没有可订阅的通知）
        deadline = time.monotonic() + 15
        while True:
            if port_file.exists():
                lines = port_file.read_text().split()
                if len(lines) >= 2:
                    self.port = int(lines[0])
                    break
            if self.proc.poll() is not None:
                raise RuntimeError(f"chrome exited early with code {self.proc.returncode}")
            if time.monotonic() > deadline:
                kill_tree(self.proc.pid)
                raise TimeoutError("DevToolsActivePort not created")
            await asyncio.sleep(0.1)
        async with httpx.AsyncClient() as client:
            deadline = time.monotonic() + 10
            while True:
                try:
                    r = await client.get(f"http://127.0.0.1:{self.port}/json/version", timeout=2)
                    self.version_info = r.json()
                    break
                except (httpx.HTTPError, ValueError):
                    if time.monotonic() > deadline:
                        raise
                    await asyncio.sleep(0.1)
        self.conn = CDPConnection(cfg.cmd_timeout_s)
        await self.conn.connect(self.version_info["webSocketDebuggerUrl"])
        self.crashed = asyncio.Event()
        self.crash_detected_at = None
        self.crash_reason = None
        self._stopping = False
        self.conn.on("Inspector.targetCrashed", lambda p: self._mark_crashed("renderer crashed"), "*")
        self.conn.on_lost = lambda: self._mark_crashed("cdp connection lost")
        self._watchdog_task = asyncio.create_task(self._watchdog())

    def _mark_crashed(self, reason: str):
        if self.crashed.is_set() or self._stopping:
            return
        self.crash_detected_at = time.time()
        self.crash_reason = reason
        self.crash_history.append((self.crash_detected_at, reason))
        self.crashed.set()
        if self.conn:
            self.conn.fail_all(BrowserCrashed(reason))

    async def _watchdog(self):
        while not self._stopping:
            await asyncio.sleep(self.config.watchdog_interval_s)
            if self._stopping:
                return
            if self.proc is not None and self.proc.poll() is not None:
                self._mark_crashed(f"chrome process exited ({self.proc.returncode})")
                return
            if self.conn is not None and self.conn.closed:
                self._mark_crashed("cdp connection lost")
                return

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None and self.conn is not None and not self.conn.closed

    async def stop(self):
        self._stopping = True
        if self._watchdog_task:
            self._watchdog_task.cancel()
        if self.page is not None:
            self.page.detach()
            self.page = None
        if self.conn is not None and not self.conn.closed:
            try:
                await self.conn.send("Browser.close", timeout=3)
            except Exception:
                pass
        if self.proc is not None:
            try:
                await asyncio.wait_for(asyncio.to_thread(self.proc.wait), 5)
            except asyncio.TimeoutError:
                kill_tree(self.proc.pid)
                await asyncio.to_thread(self.proc.wait)
        if self.conn is not None:
            await self.conn.close()
        leftovers = await asyncio.to_thread(profile_chrome_pids, self.config.profile_dir)
        for pid in leftovers:
            kill_tree(pid)
        self.proc = None
        self.conn = None

    async def restart(self):
        await self.stop()
        await self.start()

    async def relaunch(self, headless: bool):
        await self.stop()
        self.headless = headless
        await self.start()

    async def new_page(self):
        from .page import Page

        targets = (await self.conn.send("Target.getTargets"))["targetInfos"]
        pages = [t for t in targets if t["type"] == "page"]
        if pages:
            target_id = pages[0]["targetId"]
        else:
            target_id = (await self.conn.send("Target.createTarget", {"url": "about:blank"}))["targetId"]
        if self.page is not None:
            await self.page.release()
        page = Page(self.conn, self.config)
        await page.attach(target_id)
        self.page = page
        return page
