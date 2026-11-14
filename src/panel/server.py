"""aiohttp 路由：鉴权（Host + Origin + 令牌）、静态页、/runs 只读、REST、WebSocket 集线器。"""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from pathlib import Path

from aiohttp import WSMsgType, web

from . import history
from .jobs import JobBusy

STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "panel_token"


def make_token() -> str:
    return secrets.token_urlsafe(24)


class Client:
    """一个 WS 客户端：JSON 消息排队发送；画面帧只发最新的一帧（发不过来就丢旧帧）。"""

    def __init__(self, ws: web.WebSocketResponse, svc):
        self.ws = ws
        self.svc = svc
        self.q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self.frame_ready = asyncio.Event()
        self.sent_seq = 0

    def queue_json(self, msg: dict):
        try:
            self.q.put_nowait(msg)
        except asyncio.QueueFull:
            pass

    def on_frame(self):
        self.frame_ready.set()

    async def json_loop(self):
        while not self.ws.closed:
            msg = await self.q.get()
            await self.ws.send_str(json.dumps(msg, ensure_ascii=False, default=str))

    async def frame_loop(self):
        while not self.ws.closed:
            await self.frame_ready.wait()
            self.frame_ready.clear()
            data = self.svc.preview.latest
            seq = self.svc.preview.seq
            if data is None or seq == self.sent_seq:
                continue
            self.sent_seq = seq
            await self.ws.send_bytes(data)


def create_app(svc, token: str, port_ref: dict) -> web.Application:
    """port_ref: {"port": 实际端口}（绑定 0 时启动后才知道端口）。"""

    def allowed_hosts():
        p = port_ref["port"]
        return {f"127.0.0.1:{p}", f"localhost:{p}"}

    def allowed_origins():
        p = port_ref["port"]
        return {f"http://127.0.0.1:{p}", f"http://localhost:{p}"}

    @web.middleware
    async def guard(request: web.Request, handler):
        if request.host not in allowed_hosts():
            return web.Response(status=403, text="bad host")
        path = request.path
        protected = path.startswith("/api/") or path == "/ws" or path.startswith("/runs/")
        if protected:
            origin = request.headers.get("Origin")
            if origin is not None and origin not in allowed_origins():
                return web.Response(status=403, text="bad origin")
            got = request.headers.get("X-Panel-Token") or request.query.get("token") or request.cookies.get(COOKIE)
            if not got or not secrets.compare_digest(got, token):
                return web.Response(status=403, text="bad token")
        try:
            resp = await handler(request)
        except web.HTTPException as e:
            resp = e
        p = port_ref["port"]
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; "
            f"connect-src 'self' ws://127.0.0.1:{p} ws://localhost:{p}; frame-src 'self'; "
            "frame-ancestors 'self'; form-action 'self'; base-uri 'self'")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    app = web.Application(middlewares=[guard], client_max_size=50 * 1024 * 1024)
    r = app.router

    def ok(data=None, **kw):
        return web.json_response({"ok": True, **({"data": data} if data is not None else {}), **kw},
                                 dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))

    def err(status, msg, **kw):
        return web.json_response({"ok": False, "error": msg, **kw}, status=status,
                                 dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))

    async def body(request) -> dict:
        try:
            return await request.json()
        except Exception:
            return {}

    async def guarded(coro):
        try:
            return ok(await coro)
        except PermissionError as e:
            return err(409, str(e))
        except JobBusy as e:
            return err(409, f"正在运行：{e.title}", running=e.title)
        except (RuntimeError, ValueError, FileNotFoundError, KeyError) as e:
            return err(400, f"{type(e).__name__}: {e}")

    # ---------- 页面 ----------
    def page(name):
        async def h(request):
            html = (STATIC / name).read_text(encoding="utf-8").replace("{{TOKEN}}", token)
            resp = web.Response(text=html, content_type="text/html", charset="utf-8")
            resp.set_cookie(COOKIE, token, httponly=True, samesite="Strict", path="/")
            return resp
        return h

    r.add_get("/", page("index.html"))
    r.add_get("/collect", page("collect.html") if (STATIC / "collect.html").exists() else page("index.html"))
    r.add_static("/static/", STATIC, show_index=False)

    async def runs_file(request):
        p = history.safe_path(svc.config.runs_dir, request.match_info["rel"])
        if p is None:
            return web.Response(status=404, text="not found")
        return web.FileResponse(p)
    r.add_get("/runs/{rel:.+}", runs_file)

    # ---------- 状态 ----------
    async def get_state(request):
        return web.json_response(svc.state(), dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))
    r.add_get("/api/state", get_state)

    async def get_info(request):
        return ok(svc.static_info())
    r.add_get("/api/info", get_info)

    async def focus(request):
        fn = getattr(svc, "focus_window", None)
        if fn:
            fn()
        return ok()
    r.add_post("/api/focus", focus)

    # ---------- 浏览器 ----------
    async def b_start(request):
        return await guarded(svc.browser_start())

    async def b_stop(request):
        return await guarded(svc.browser_stop())

    async def b_restart(request):
        return await guarded(svc.browser_restart())

    async def b_mode(request):
        d = await body(request)
        return await guarded(svc.set_mode(bool(d.get("headless", True))))

    async def b_front(request):
        return await guarded(svc.front())
    r.add_post("/api/browser/start", b_start)
    r.add_post("/api/browser/stop", b_stop)
    r.add_post("/api/browser/restart", b_restart)
    r.add_post("/api/browser/mode", b_mode)
    r.add_post("/api/browser/front", b_front)

    async def chrome_path(request):
        d = await body(request)
        good, msg = svc.set_chrome_path(str(d.get("path", "")))
        return ok(msg) if good else err(400, msg)
    r.add_post("/api/chrome_path", chrome_path)

    # ---------- 导航 ----------
    async def nav(request):
        d = await body(request)
        return await guarded(svc.navigate(str(d.get("url", ""))))

    async def nav_back(request):
        return await guarded(svc.back())

    async def nav_reload(request):
        return await guarded(svc.reload())
    r.add_post("/api/nav", nav)
    r.add_post("/api/nav/back", nav_back)
    r.add_post("/api/nav/reload", nav_reload)

    async def elements(request):
        return await guarded(svc.elements())
    r.add_get("/api/elements", elements)

    # ---------- 预览 ----------
    async def preview(request):
        d = await body(request)
        if "on" in d:
            await svc.preview.set_on(bool(d["on"]))
        return ok(svc.preview.state())
    r.add_post("/api/preview", preview)

    # ---------- 任务 ----------
    async def jobs_start(request):
        d = await body(request)
        try:
            job = await svc.jobs.start(str(d.get("kind")), d.get("params") or {})
        except JobBusy as e:
            return err(409, f"正在运行：{e.title}", running=e.title)
        except (RuntimeError, ValueError, FileNotFoundError, KeyError) as e:
            return err(400, f"{type(e).__name__}: {e}")
        return ok(job.to_dict())

    async def jobs_stop(request):
        return ok(svc.jobs.stop())

    async def jobs_continue(request):
        return ok(svc.jobs.continue_human())
    r.add_post("/api/jobs", jobs_start)
    r.add_post("/api/jobs/stop", jobs_stop)
    r.add_post("/api/jobs/continue", jobs_continue)

    async def cases(request):
        return ok(svc.cases())

    async def runs(request):
        return ok(svc.runs())
    r.add_get("/api/cases", cases)
    r.add_get("/api/runs", runs)

    # ---------- 设置 ----------
    async def settings_get(request):
        s = svc.settings
        return ok({"current": s.current, "pending": s.pending, "effective": s.effective(), "version": s.version})

    async def settings_put(request):
        d = await body(request)
        errors = svc.settings.update(d)
        if errors:
            return err(400, "取值超出范围", errors=errors)
        if not svc.jobs.busy:
            svc.settings.apply_now(svc.config)       # 空闲时立即生效
        svc.push_soon()
        return await settings_get(request)

    async def settings_save(request):
        if not svc.jobs.busy:
            svc.settings.apply_now(svc.config)
        from hands.config import DATA_ROOT
        p = svc.settings.save_default(DATA_ROOT / "config.yaml")
        return ok(str(p))

    async def settings_restore(request):
        svc.settings.restore()
        if not svc.jobs.busy:
            svc.settings.apply_now(svc.config)
        svc.push_soon()
        return await settings_get(request)
    r.add_get("/api/settings", settings_get)
    r.add_put("/api/settings", settings_put)
    r.add_post("/api/settings/save_default", settings_save)
    r.add_post("/api/settings/restore", settings_restore)

    # ---------- 用户目录 ----------
    async def profile_get(request):
        return ok(await asyncio.to_thread(svc.profile_info))

    async def profile_clear(request):
        d = await body(request)
        good, msg = await svc.profile_clear(str(d.get("confirm", "")))
        return ok(msg) if good else err(409, msg)
    r.add_get("/api/profile", profile_get)
    r.add_post("/api/profile/clear", profile_clear)

    # ---------- 样本 ----------
    async def samples_list(request):
        return ok(svc.samples.list(request.query.get("device"), request.query.get("source")))

    async def samples_add(request):
        d = await body(request)
        return ok(svc.samples.add(d))

    async def samples_get(request):
        s = svc.samples.get(request.match_info["sid"])
        return ok(s) if s else err(404, "not found")

    async def samples_del(request):
        return ok(svc.samples.delete(request.match_info["sid"]))

    async def samples_grid(request):
        return ok(svc.samples.grid())

    async def samples_export(request):
        return web.Response(body=svc.samples.export(), content_type="application/json", charset="utf-8",
                            headers={"Content-Disposition": 'attachment; filename="samples.json"'})

    async def samples_import(request):
        data = await request.read()
        return ok(svc.samples.import_(data, request.query.get("mode", "merge")))
    r.add_get("/api/samples", samples_list)
    r.add_post("/api/samples", samples_add)
    r.add_get("/api/samples/grid", samples_grid)
    r.add_get("/api/samples/export", samples_export)
    r.add_post("/api/samples/import", samples_import)
    r.add_get("/api/samples/{sid}", samples_get)
    r.add_delete("/api/samples/{sid}", samples_del)

    # ---------- WebSocket ----------
    async def ws_handler(request):
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=4 * 1024 * 1024)
        await ws.prepare(request)
        c = Client(ws, svc)
        svc.clients.add(c)
        svc.preview._listeners.append(c.on_frame)
        c.queue_json(svc.state())
        tasks = [asyncio.create_task(c.json_loop()), asyncio.create_task(c.frame_loop())]
        if svc.preview.latest is not None:
            c.on_frame()
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    m = json.loads(msg.data)
                except ValueError:
                    continue
                t = m.get("t")
                if t == "shown":
                    svc.preview.report_shown(int(m.get("seq", 0)), float(m.get("at", time.time())))
                elif t in ("mouse", "key", "text"):
                    if svc.jobs.accepts_input() and svc.browser_alive() and svc.browser.page is not None:
                        try:
                            await svc.input.handle(svc.browser.page.session, m)
                        except Exception as e:
                            svc.log(f"[panel] 输入转发失败: {e}")
        finally:
            svc.clients.discard(c)
            try:
                svc.preview._listeners.remove(c.on_frame)
            except ValueError:
                pass
            for t in tasks:
                t.cancel()
        return ws
    r.add_get("/ws", ws_handler)

    async def pusher(app):
        async def loop():
            while True:
                try:
                    await asyncio.wait_for(svc._push_evt.wait(), 0.5)
                except asyncio.TimeoutError:
                    pass
                svc._push_evt.clear()
                if svc.clients:
                    st = svc.state()
                    for c in list(svc.clients):
                        # 状态消息只保留最新：队列里积压的旧状态无意义
                        c.queue_json(st)
                await asyncio.sleep(0.1)  # 合并 100 ms 内的多次事件
        task = asyncio.create_task(loop())
        yield
        task.cancel()
    app.cleanup_ctx.append(pusher)
    return app


async def serve(svc, port: int = 0, host: str = "127.0.0.1", token: str | None = None):
    """启动服务，返回 (runner, port, token)。"""
    from .inputs import InputForwarder
    token = token or make_token()
    svc.input = InputForwarder()
    port_ref = {"port": port}
    app = create_app(svc, token, port_ref)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    actual = site._server.sockets[0].getsockname()[1]
    port_ref["port"] = actual
    await svc.start()
    return runner, actual, token
