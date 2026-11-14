"""Windows 程序入口：数据目录、单实例锁、后台线程跑 aiohttp、主线程 pywebview 窗口（WebView2，私密模式）。

打包后（PyInstaller，sys.frozen）：数据目录 = <程序目录>/data，首次运行从包内复制 config.yaml、cases、fixtures。
开发时：python -m panel（数据目录 = 项目根）。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import threading
import urllib.request
from pathlib import Path

LOCK_NAME = "panel.lock"


def _setup_data_dir() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    exe_dir = Path(sys.executable).resolve().parent
    data = exe_dir / "data"
    data.mkdir(parents=True, exist_ok=True)
    os.environ["HANDS_DATA_DIR"] = str(data)
    bundle = Path(getattr(sys, "_MEIPASS", exe_dir))
    for name in ("config.yaml", "cases", "fixtures"):
        src, dst = bundle / "seed" / name, data / name
        if src.exists() and not dst.exists():
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copyfile(src, dst)
    return data


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259                                      # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_lock(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def try_focus_existing(lock: dict) -> bool:
    """锁有效（进程活着且面板应答）就请它把窗口调到前台，返回 True；否则 False（接管）。"""
    if not lock or not pid_alive(int(lock.get("pid", 0))):
        return False
    port = lock.get("port")
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/focus", data=b"{}", method="POST",
                                 headers={"X-Panel-Token": lock.get("token", ""), "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def main(port: int = 0):
    _setup_data_dir()
    from hands.config import DATA_ROOT
    lock_path = DATA_ROOT / LOCK_NAME
    if try_focus_existing(read_lock(lock_path)):
        print("面板已在运行，已切到前台")
        return
    import webview

    from .server import serve
    from .service import PanelService

    loop = asyncio.new_event_loop()
    ready = threading.Event()
    box: dict = {}

    def run_loop():
        asyncio.set_event_loop(loop)

        async def boot():
            svc = PanelService()
            runner, actual, token = await serve(svc, port)
            box.update(svc=svc, runner=runner, port=actual, token=token)
            lock_path.write_text(json.dumps({"pid": os.getpid(), "port": actual, "token": token}), encoding="utf-8")
            ready.set()
        loop.run_until_complete(boot())
        loop.run_forever()

    th = threading.Thread(target=run_loop, daemon=True)
    th.start()
    ready.wait(30)
    if "port" not in box:
        print("面板后台启动失败")
        return
    url = f"http://127.0.0.1:{box['port']}/"
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False     # 新窗口请求不交给系统浏览器
    svc = box["svc"]

    def blocked(u: str):                 # 在界面线程里被调用：只投递到后台事件循环，绝不同步等待
        loop.call_soon_threadsafe(svc.event, "blocked", {"url": u})
    _install_navigation_guard(url, blocked)
    window = webview.create_window("Hands 控制面板", url, width=1500, height=950, min_size=(1000, 650))

    def focus():
        try:
            window.restore()
            window.show()
            window.on_top = True
            window.on_top = False
        except Exception:
            pass
    box["svc"].focus_window = focus

    def on_closing():
        fut = asyncio.run_coroutine_threadsafe(_shutdown(box), loop)
        try:
            fut.result(30)
        except Exception:
            pass
        try:
            lock_path.unlink()
        except OSError:
            pass
    window.events.closing += on_closing
    webview.start(private_mode=True)
    if lock_path.exists() and (read_lock(lock_path) or {}).get("pid") == os.getpid():
        on_closing()
    loop.call_soon_threadsafe(loop.stop)


async def _shutdown(box):
    svc = box.get("svc")
    if svc is not None:
        await svc.shutdown()
    r = box.get("runner")
    if r is not None:
        await r.cleanup()


def _install_navigation_guard(base: str, on_blocked) -> bool:
    """外部跳转拦截（N2）主方案：在 pywebview 初始化 WebView2 的回调里（界面线程）挂 NavigationStarting，
    取消非面板地址。注意：不能从其他线程碰 WebView2 原生控件，也不能在这个回调里同步调 evaluate_js，
    两者都会和界面线程互相等待而卡死。拿不到时依靠前端拦截 + CSP 兜底（app.js / server.py）。"""
    try:
        from webview.platforms import edgechromium
    except Exception:
        return False
    cls = edgechromium.EdgeChrome
    if getattr(cls, "_panel_guarded", False):
        return True
    origin = base.rstrip("/")
    allowed = (origin, "about:", "data:", "blob:" + origin)
    original = cls.on_webview_ready

    def nav_starting(sender, args):
        try:
            u = str(args.Uri)
            if not u.startswith(allowed):
                args.Cancel = True
                on_blocked(u)
        except Exception:
            pass

    def on_webview_ready(self, sender, args):
        original(self, sender, args)
        try:
            if args.IsSuccess:
                sender.CoreWebView2.NavigationStarting += nav_starting
                sender.CoreWebView2.FrameNavigationStarting += nav_starting
        except Exception:
            pass
    cls.on_webview_ready = on_webview_ready
    cls._panel_guarded = True
    return True


if __name__ == "__main__":
    main()
