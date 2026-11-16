"""PanelService：唯一持有 BrowserManager；汇总面板状态；崩溃看护；夹具服务；状态推送。"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

from hands.browser import profile_chrome_pids
from hands.config import DATA_ROOT, DRIVERS, PROJECT_ROOT, find_chrome, load_config
from hands.dom import DomReader
from hands.driver import create_browser
from hands.status import STATUS

from . import history, profile
from .jobs import JobManager
from .preview import Screencaster
from .samples import SampleStore
from .settings import ABOUT, LiveSettings, spec_list

FIXTURE_PORT = 8765


def _load_config_safe():
    """找不到 Chrome 时不抛错：返回 (config, need_chrome_path)。"""
    try:
        return load_config(DATA_ROOT / "config.yaml"), False
    except FileNotFoundError:
        cfg = load_config(DATA_ROOT / "config.yaml", chrome_path="__missing__")
        cfg.chrome_path = ""
        return cfg, True


class PanelService:
    def __init__(self, config=None):
        if config is None:
            config, need = _load_config_safe()
        else:
            need = not config.chrome_path
        self.config = config
        self.need_chrome_path = need
        self.browser = None                  # BrowserManager 或 PwBrowserManager（按 config.driver）
        self.driver_error = ""
        self.status = "idle"                 # idle | running | crash_restarting | need_human | stopped
        self.status_text = ""
        self.settings = LiveSettings(config)
        self.samples = SampleStore(self._library_path())
        self.preview = Screencaster(self)
        self.jobs = JobManager(self)
        self.page_info: dict = {"url": "", "title": ""}
        self.fixtures = None
        self.events: list[dict] = []
        self.logs: list[str] = []
        self.clients: set = set()            # WS 客户端（server 维护）
        self._push_evt = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._busy_browser = asyncio.Lock()
        self._unsub = STATUS.subscribe(self._on_status)
        self._started = time.time()

    def _library_path(self) -> Path:
        from hands.humanize import HumanizeConfig
        p = Path(HumanizeConfig.from_dict(self.config.humanize).mouse.library_path)
        return p if p.is_absolute() else DATA_ROOT / p

    # ---------------- 生命周期 ----------------
    async def start(self):
        self._tasks.append(asyncio.create_task(self._guard_loop()))
        self._tasks.append(asyncio.create_task(self._page_info_loop()))

    async def shutdown(self):
        for t in self._tasks:
            t.cancel()
        if self.jobs.busy:
            self.jobs.stop()
            try:
                await asyncio.wait_for(asyncio.shield(self.jobs._task), 10)
            except Exception:
                pass
        await self.preview.stop()
        self.preview.close()
        if self.browser is not None:
            try:
                await self.browser.stop()
            except Exception:
                pass
        if self.fixtures is not None:
            self.fixtures.stop()
            self.fixtures = None
        self._unsub()

    def log(self, *a):
        line = " ".join(str(x) for x in a)
        self.logs.append(time.strftime("%H:%M:%S ") + line)
        self.logs = self.logs[-200:]
        print(line, flush=True)

    # ---------------- 浏览器 ----------------
    def browser_alive(self) -> bool:
        return self.browser is not None and self.browser.is_alive()

    async def ensure_browser(self):
        if not self.browser_alive():
            await self.browser_start()
        if self.browser.page is None:
            await self.browser.new_page()

    async def browser_start(self, headless: bool | None = None):
        if self.need_chrome_path:
            raise RuntimeError("找不到 Chrome，请先设置 Chrome 路径")
        async with self._busy_browser:
            if self.browser is None:
                self.browser = self._new_browser()
            if headless is not None:
                self.browser.headless = headless
            if not self.browser.is_alive():
                await self.browser.start()
            if self.browser.page is None:
                await self.browser.new_page()
            self.status = "running"
        await self._auto_preview()
        self.push_soon()

    async def browser_stop(self):
        if self.jobs.busy:
            raise PermissionError("有任务在运行")
        async with self._busy_browser:
            await self.preview.stop()
            if self.browser is not None:
                await self.browser.stop()
            self.status = "stopped"
        self.push_soon()

    async def browser_restart(self):
        if self.jobs.busy:
            raise PermissionError("有任务在运行")
        async with self._busy_browser:
            await self.preview.stop()
            if self.browser is None:
                self.browser = self._new_browser()
                await self.browser.start()
            else:
                await self.browser.restart()
            await self.browser.new_page()
            self.status = "running"
        await self._auto_preview()
        self.push_soon()

    async def set_mode(self, headless: bool):
        if self.jobs.busy:
            raise PermissionError("有任务在运行")
        async with self._busy_browser:
            if self.browser is None:
                self.browser = self._new_browser()
                self.browser.headless = headless
                await self.browser.start()
            else:
                await self.preview.stop()
                await self.browser.relaunch(headless)
            await self.browser.new_page()
            self.status = "running"
        self.preview.want_on = headless          # 无界面默认推流；有界面默认不推
        await self.preview.sync()
        self.push_soon()

    def _new_browser(self):
        """按 config.driver 创建浏览器；选了 Playwright 但未安装时抛出带安装提示的错误。"""
        try:
            b = create_browser(self.config)
        except Exception as e:
            self.driver_error = str(e)
            self.push_soon()
            raise RuntimeError(str(e)) from e
        self.driver_error = ""
        return b

    async def set_driver(self, name: str):
        """切换驱动：有任务时拒绝；否则停掉当前浏览器，按新驱动重新启动（保持原来的显示方式）。"""
        if name not in DRIVERS:
            raise ValueError(f"未知驱动：{name}")
        if self.jobs.busy:
            raise PermissionError("有任务在运行")
        async with self._busy_browser:
            headless = self.browser.headless if self.browser is not None else self.config.headless
            await self.preview.stop()
            if self.browser is not None:
                await self.browser.stop()
                self.browser = None
            old = self.config.driver
            self.config.driver = name
            try:
                self.browser = self._new_browser()
            except RuntimeError:
                self.config.driver = old
                self.browser = None
                self.status = "stopped"
                raise
            self.browser.headless = headless
            await self.browser.start()
            await self.browser.new_page()
            self.status = "running"
        self.log(f"[panel] 已切换驱动：{name}")
        await self._auto_preview()
        self.push_soon()

    async def _auto_preview(self):
        from hands.driver import caps_of
        if self.browser is not None and (self.browser.headless or not caps_of(self.browser).screencast):
            self.preview.want_on = True               # 桌面驱动：默认开缩略预览
        await self.preview.sync()

    async def front(self):
        b = self.browser
        if not self.browser_alive() or b.page is None:
            raise RuntimeError("浏览器未运行")
        await b.page.bring_to_front()
        if not b.headless and b.driver == "cdp":       # 窗口摆放只在 CDP 驱动下做（Playwright 方案不做）
            try:
                w = await b.conn.send("Browser.getWindowForTarget", {"targetId": b.page.target_id})
                await b.conn.send("Browser.setWindowBounds", {"windowId": w["windowId"],
                                                              "bounds": {"windowState": "normal"}})
                await b.conn.send("Browser.setWindowBounds", {"windowId": w["windowId"], "bounds": {
                    "left": 960, "top": 0, "width": self.config.window[0], "height": self.config.window[1]}})
            except Exception:
                pass

    async def _guard_loop(self):
        """崩溃看护：没有任务时检测到崩溃就自动重启；有用例任务时交给 CaseRunner 自己重跑。"""
        while True:
            await asyncio.sleep(0.5)  # condition-poll
            b = self.browser
            if b is None or self.status in ("idle", "stopped"):
                continue
            if b.crashed.is_set() and not self.jobs.busy and not self._busy_browser.locked():
                self.status = "crash_restarting"
                self.push_soon()
                try:
                    async with self._busy_browser:
                        await self.preview.stop()
                        await b.restart()
                        await b.new_page()
                    self.status = "running"
                    await self._auto_preview()
                except Exception as e:
                    self.log(f"[panel] 崩溃重启失败: {e}")
                    self.status = "stopped"
                self.push_soon()
            elif self.jobs.busy and b.crashed.is_set():
                self.status = "crash_restarting"
            elif self.status == "crash_restarting" and b.is_alive():
                self.status = "running"

    async def _page_info_loop(self):
        while True:
            await asyncio.sleep(0.5)  # condition-poll
            await self.refresh_page_info()

    # ---------------- 导航 ----------------
    def _check_free(self):
        if self.jobs.busy and not self.jobs.accepts_input() and self.jobs.current.kind != "sample_real":
            raise PermissionError("有任务在运行")

    async def navigate(self, url: str):
        self._check_free()
        await self.ensure_browser()
        if "://" not in url and not url.startswith("about:"):
            url = "http://" + url
        if url.startswith(f"http://127.0.0.1:{FIXTURE_PORT}") or url.startswith(f"http://localhost:{FIXTURE_PORT}"):
            self.ensure_fixtures()
        r = await self.browser.page.navigate(url)
        return r.to_dict()

    async def back(self):
        self._check_free()
        await self.ensure_browser()
        return (await self.browser.page.back()).to_dict()

    async def reload(self):
        self._check_free()
        await self.ensure_browser()
        return (await self.browser.page.reload()).to_dict()

    async def elements(self) -> dict:
        if not self.browser_alive() or self.browser.page is None:
            return {"elements": [], "viewport": None}
        page = self.browser.page
        els, _ = await DomReader().read(page)
        lay = await page.layout()
        return {"elements": [{"id": e.id, "role": e.role, "name": e.name, "tag": e.tag, "box": e.box.to_dict(),
                              "visible": e.visible, "in_viewport": e.in_viewport, "frame_depth": len(e.frame_path)}
                             for e in els],
                "viewport": {"w": lay["vw"], "h": lay["vh"]}}

    # ---------------- 夹具 ----------------
    def ensure_fixtures(self):
        if self.fixtures is None:
            root = DATA_ROOT / "fixtures" if (DATA_ROOT / "fixtures" / "server.py").exists() else PROJECT_ROOT / "fixtures"
            sys.path.insert(0, str(root))
            import server as fixture_server  # noqa
            try:
                self.fixtures = fixture_server.start(FIXTURE_PORT)
            except OSError as e:
                self.log(f"[panel] 夹具服务端口 {FIXTURE_PORT} 已被占用（可能已有夹具服务在运行）：{e}")
                self.fixtures = _External()

    def fixture_url(self, path: str) -> str:
        return f"http://127.0.0.1:{FIXTURE_PORT}/{path.lstrip('/')}"

    def fixture_vars(self) -> dict:
        return {"BASE": f"http://127.0.0.1:{FIXTURE_PORT}", "XBASE": f"http://localhost:{FIXTURE_PORT}"}

    # ---------------- 设置 / Chrome 路径 ----------------
    def set_chrome_path(self, path: str) -> tuple[bool, str]:
        p = Path(path.strip().strip('"'))
        if not p.is_file():
            return False, "文件不存在"
        from .settings import save_yaml_values
        save_yaml_values(DATA_ROOT / "config.yaml", {"chrome_path": str(p)})
        self.config.chrome_path = str(p)
        self.need_chrome_path = False
        if self.browser is not None:
            self.browser.config = self.config
        return True, "已保存"

    # ---------------- 用户目录 ----------------
    def profile_info(self) -> dict:
        d = profile.info(self.config.profile_dir)
        d["browser_running"] = self.browser_alive()
        return d

    async def profile_clear(self, confirm: str) -> tuple[bool, str]:
        running = self.browser_alive()
        if not running:
            leftovers = await asyncio.to_thread(profile_chrome_pids, self.config.profile_dir)
            running = bool(leftovers)
        return profile.clear(self.config.profile_dir, running, confirm)

    # ---------------- 状态 ----------------
    async def refresh_page_info(self):
        b = self.browser
        if b is None or not b.is_alive() or b.page is None or getattr(b.page, "session", True) is None:
            return
        try:
            self.page_info = {"url": await b.page.url(), "title": await b.page.title()}
        except Exception:
            pass

    def _on_status(self, name, payload):
        if name == "page_changed":
            try:
                asyncio.get_running_loop().create_task(self.refresh_page_info())
            except RuntimeError:
                pass
        if name == "action_done":
            self.event("action_done", {k: payload.get(k) for k in
                                       ("tool", "ok", "effective", "changes", "error", "elapsed_ms", "trajectory")})
        self.push_soon()

    def event(self, name: str, payload: dict):
        msg = {"t": "event", "name": name, "at": time.time(), **payload}
        self.events.append(msg)
        self.events = self.events[-100:]
        for c in list(self.clients):
            c.queue_json(msg)

    def push_soon(self):
        self._push_evt.set()

    def on_job_end(self, job):
        if self.browser_alive():
            self.status = "running"

    def browser_state(self) -> dict:
        from hands.driver import caps_of
        b = self.browser
        status = self.status
        if self.jobs.current is not None and self.jobs.current.state == "need_human":
            status = "need_human"
        caps = caps_of(b).to_dict() if b is not None else None
        if b is not None and status == "running" and not b.is_alive():
            status = "crash_restarting" if b.crashed.is_set() else "stopped"
        return {"status": status, "headless": (b.headless if b else self.config.headless),
                "crashes": len(b.crash_history) if b else 0, "pid": b.pid if b and b.is_alive() else None,
                "need_chrome_path": self.need_chrome_path,
                "driver": b.driver if b is not None else self.config.driver, "driver_error": self.driver_error,
                "capabilities": caps}

    def state(self) -> dict:
        b = self.browser
        mon = None
        if b is not None and b.is_alive() and b.page is not None:
            try:
                mon = b.page.monitor.snapshot().to_dict()
            except Exception:
                mon = None
        job = self.jobs.state()
        return {
            "t": "state",
            "now": time.time(),
            "browser": self.browser_state(),
            "page": self.page_info,
            "job": job,
            "busy": self.jobs.busy,
            "wait": STATUS.wait.to_dict() if STATUS.wait else None,
            "phase": STATUS.phase,
            "monitor": mon,
            "tree": self.jobs.tree,
            "steps": self.jobs.steps[-200:],
            "demo": self.jobs.demo_rounds,
            "target": self.jobs.last_target,
            "preview": self.preview.state(),
            "settings": {"current": self.settings.current, "pending": self.settings.pending,
                         "version": self.settings.version},
            "settings_pending": bool(self.settings.pending),
            "seed": (job or {}).get("progress", {}).get("seed") if job else None,
            "pause": self.jobs.gate.paused.to_dict() if self.jobs.gate.paused else None,
            "confirm": (self.jobs.current.confirm_pending if self.jobs.current else None),
            "expand": ({"state": self.jobs.expand.state, "counts": self.jobs.expand.tree.counts()}
                       if self.jobs.expand is not None else None),
        }

    def static_info(self) -> dict:
        return {"specs": spec_list(), "about": ABOUT, "data_root": str(DATA_ROOT),
                "runs_dir": str(self.config.runs_dir)}

    def runs(self) -> list[dict]:
        return history.list_runs(self.config.runs_dir)

    def cases(self) -> list[str]:
        d = DATA_ROOT / "cases"
        return sorted(p.name for p in d.glob("*.yaml")) if d.exists() else []


class _External:
    def stop(self):
        pass


_ = find_chrome
