"""验收公共设施：夹具服务、共享浏览器、标准答案、进程与内存工具。"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import sys
import time
from pathlib import Path

from hands.browser import BrowserManager, profile_chrome_pids, profile_chrome_ws_mb
from hands.config import PROJECT_ROOT, load_config
from hands.dom import DomReader
from hands.recorder import RunRecorder
from hands.runner import CaseRunner, load_case

sys.path.insert(0, str(PROJECT_ROOT / "fixtures"))
import server as fixture_server  # noqa: E402

PORT = 8765
FIXTURES = PROJECT_ROOT / "fixtures"


class Ctx:
    def __init__(self, headed: bool = False, rounds: int = 50, humanize: bool = False, seed: int | None = None):
        from hands.humanize import apply_cli

        self.config = load_config(headless=False if headed else None)
        self.config.humanize = apply_cli(self.config.humanize, humanize, seed)
        self.rounds = rounds
        self.base = f"http://127.0.0.1:{PORT}"
        self.xbase = f"http://localhost:{PORT}"
        self.server = fixture_server.start(PORT)
        self.run_root = self.config.runs_dir / (time.strftime("%Y%m%d-%H%M%S") + "-acceptance")
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.browser: BrowserManager | None = None

    def url(self, path: str) -> str:
        return f"{self.base}/{path.lstrip('/')}"

    def artifact(self, name: str) -> Path:
        p = self.run_root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    async def get_browser(self) -> BrowserManager:
        if self.browser is None or not self.browser.is_alive():
            if self.browser is not None:
                await self.browser.restart()
            else:
                self.browser = BrowserManager(self.config)
                await self.browser.start()
        if self.browser.page is None:
            await self.browser.new_page()
        return self.browser

    async def page(self):
        return (await self.get_browser()).page

    async def close_browser(self):
        if self.browser is not None:
            await self.browser.stop()
            self.browser = None

    async def run_case(self, case_file: str, on_step=None, input_fn=input):
        b = await self.get_browser()
        case = load_case(PROJECT_ROOT / "cases" / case_file, {"BASE": self.base, "XBASE": self.xbase})
        rec = RunRecorder(self.run_root / "cases", label=case.name)
        runner = CaseRunner(b, self.config, rec, on_step=on_step, input_fn=input_fn, log=lambda *a: None)
        result = await runner.run_case(case)
        report = rec.write_report([result])
        return result, rec, report

    async def close(self):
        await self.close_browser()
        self.server.stop()


def load_expected(fixture: str) -> dict:
    return json.loads((FIXTURES / fixture / "expected.json").read_text(encoding="utf-8"))


async def read_elements(page):
    return await DomReader().read(page)


def chrome_procs(profile) -> list[int]:
    return profile_chrome_pids(profile)


def chrome_ws_mb(profile) -> float:
    return profile_chrome_ws_mb(profile)


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [("cb", ctypes.wintypes.DWORD), ("PageFaultCount", ctypes.wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


if sys.platform == "win32":
    _k32 = ctypes.WinDLL("kernel32")
    _psapi = ctypes.WinDLL("psapi")
    _k32.GetCurrentProcess.restype = ctypes.wintypes.HANDLE
    _psapi.GetProcessMemoryInfo.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
                                            ctypes.wintypes.DWORD]
    _psapi.GetProcessMemoryInfo.restype = ctypes.wintypes.BOOL


def python_rss_mb() -> float:
    if sys.platform != "win32":
        for line in open("/proc/self/status"):
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
        return 0.0
    c = PROCESS_MEMORY_COUNTERS()
    c.cb = ctypes.sizeof(c)
    if not _psapi.GetProcessMemoryInfo(_k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
        raise OSError(ctypes.get_last_error())
    return c.WorkingSetSize / 1024 / 1024


RECTS_JS = """(()=>[...document.querySelectorAll('[id]')].map(x=>{const r=x.getBoundingClientRect();
return {id:x.id,label:(x.innerText||x.textContent||x.getAttribute('aria-label')||x.placeholder||'').trim(),
rect:[r.x,r.y,r.width,r.height]}}))()"""


def box_diff(box, rect) -> float:
    return max(abs(a - b) for a, b in zip([box.x, box.y, box.width, box.height], rect))
