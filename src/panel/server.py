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


async def _async(fn, *args):
    return fn(*args)


class Client:
    """一个 WS 客户端：JSON 消息排队发送；画面帧只发最新的一帧（发不过来就丢旧帧）。
    mode=thumb（远程平板）：不转发推流帧，最多每秒发一张宽 640 的缩略图；收到的输入一律忽略。"""

    THUMB_INTERVAL_S = 1.0

    def __init__(self, ws: web.WebSocketResponse, svc, mode: str = "full", device_id: str | None = None):
        self.ws = ws
        self.svc = svc
        self.mode = mode
        self.device_id = device_id
        self.q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self.frame_ready = asyncio.Event()
        self.sent_seq = 0
        self._last_thumb = 0.0

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
            if self.mode == "thumb":
                wait = self.THUMB_INTERVAL_S - (time.monotonic() - self._last_thumb)
                if wait > 0:
                    await asyncio.sleep(wait)
                data, seq = self.svc.preview.thumb()
                if data is None or seq == self.sent_seq:
                    continue
                self.sent_seq = seq
                self._last_thumb = time.monotonic()
                await self.ws.send_bytes(data)
                continue
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
    register_routes(app, svc, token_for_page=token, page_cookie=(COOKIE, token), source_of=lambda req: "电脑")
    return app


def register_routes(app: web.Application, svc, *, token_for_page: str, page_cookie=None, source_of=None,
                    remote: bool = False) -> web.Application:
    """注册全部页面与接口。本机入口与远程入口共用（鉴权由各自的中间件做）。
    source_of(request) -> 操作来源（"电脑" / 设备名），写进运行中的修改记录。"""
    r = app.router
    source_of = source_of or (lambda req: "电脑")

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
            html = (STATIC / name).read_text(encoding="utf-8").replace("{{TOKEN}}", token_for_page)
            resp = web.Response(text=html, content_type="text/html", charset="utf-8")
            if page_cookie:
                resp.set_cookie(page_cookie[0], page_cookie[1], httponly=True, samesite="Strict", path="/")
            return resp
        return h

    r.add_get("/", page("index.html"))
    r.add_get("/compare", page("compare.html"))
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

    async def b_driver(request):
        d = await body(request)
        return await guarded(svc.set_driver(str(d.get("driver", ""))))
    r.add_post("/api/browser/driver", b_driver)
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
        return ok(svc.jobs.stop(source_of(request)))

    async def jobs_continue(request):
        return ok(svc.jobs.continue_human())
    r.add_post("/api/jobs", jobs_start)
    r.add_post("/api/jobs/stop", jobs_stop)
    r.add_post("/api/jobs/continue", jobs_continue)

    # ---------- 暂停 / 恢复 / 需确认（桌面驱动 spec F8、F9；平板复用） ----------
    async def jobs_pause(request):
        return ok(svc.jobs.pause(source_of(request)))

    async def jobs_resume(request):
        return ok(svc.jobs.resume(source_of(request)))

    async def jobs_confirm(request):
        d = await body(request)
        return ok(svc.jobs.confirm(bool(d.get("allow")), source_of(request)))
    r.add_post("/api/jobs/pause", jobs_pause)
    r.add_post("/api/jobs/resume", jobs_resume)
    r.add_post("/api/jobs/confirm", jobs_confirm)

    # ---------- 任务书（搭建、导入、保存、预览、试跑、运行） ----------
    async def start_book(request, d: dict):
        params = {"name": d.get("name") or d.get("type"), "text": d.get("text"),
                  "values": d.get("values") or d.get("params") or {}, "budget": d.get("budget"),
                  "trial": bool(d.get("trial")), "_source": source_of(request)}
        try:
            job = await svc.jobs.start("book", params)
        except JobBusy as e:
            return err(409, f"正在运行：{e.title}", running=e.title)
        except (RuntimeError, ValueError, FileNotFoundError, KeyError) as e:
            return err(400, str(e))
        return ok(job.to_dict())

    async def ex_start(request):              # 上一章的入口：同 /api/books/run
        return await start_book(request, await body(request))

    async def ex_tree(request):
        return ok(svc.jobs.expand_snapshot)

    async def ex_edit(request):
        d = await body(request)

        async def go():
            return svc.jobs.expand_edit(str(d.get("op", "")), d.get("node"), d.get("key"), d.get("values"),
                                        d.get("n"), rev=d.get("rev"), source=source_of(request))
        try:
            return ok(await go())
        except PermissionError as e:
            return err(409, str(e), conflict="另一端" in str(e))
        except (RuntimeError, ValueError, KeyError) as e:
            return err(400, str(e))
    r.add_post("/api/expand/start", ex_start)
    r.add_get("/api/expand/tree", ex_tree)
    r.add_post("/api/expand/edit", ex_edit)
    r.add_get("/tree", page("tree.html"))
    r.add_get("/build", page("build.html"))
    r.add_get("/m", page("m.html"))

    # ---------- 远程访问管理（只在本机入口；远程入口的白名单里没有这些） ----------
    if not remote:
        def access():
            a = getattr(svc, "remote", None)
            if a is None:
                raise RuntimeError("远程访问不可用")
            return a

        async def rm_state(request):
            return await guarded(_async(lambda: access().state()))

        async def rm_mode(request):
            d = await body(request)
            try:
                return ok(await access().set_mode(str(d.get("mode") or "")))
            except (RuntimeError, ValueError) as e:
                return err(400, str(e), state=access().state())

        async def rm_code(request):
            d = await body(request)
            return await guarded(_async(access().refresh_code, str(d.get("kind") or "lan")))

        async def rm_qr(request):
            try:
                svg = access().qr_svg(str(request.query.get("kind", "lan")))
            except (RuntimeError, ValueError) as e:
                return err(400, str(e))
            return web.Response(text=svg, content_type="image/svg+xml", charset="utf-8")

        async def rm_rename(request):
            d = await body(request)
            return await guarded(_async(access().rename, str(d.get("id") or ""), str(d.get("name") or "")))

        async def rm_revoke(request):
            d = await body(request)
            return ok(await access().revoke(str(d.get("id") or "")))
        r.add_get("/api/remote", rm_state)
        r.add_post("/api/remote/mode", rm_mode)
        r.add_post("/api/remote/code", rm_code)
        r.add_get("/api/remote/qr.svg", rm_qr)
        r.add_post("/api/remote/devices/rename", rm_rename)
        r.add_post("/api/remote/devices/revoke", rm_revoke)

    def books_svc():
        return svc.jobs.books

    async def bk_list(request):
        return await guarded(_async(books_svc().list))

    async def bk_blocks(request):
        from hands.taskbook.blocks import BUILTIN_LISTS, FORMAT, TEMPLATE_VARS, registry_json
        return ok({"blocks": registry_json(), "builtin_lists": list(BUILTIN_LISTS), "format": FORMAT,
                   "template_vars": list(TEMPLATE_VARS)})

    async def bk_guide(request):
        return await guarded(_async(books_svc().guide))

    async def bk_steps(request):
        run = str(request.query.get("run", ""))
        root = Path(svc.config.runs_dir).resolve()
        p = (root / run).resolve() if run and "/" not in run and "\\" not in run else None
        if p is None or p.parent != root or not p.is_dir():
            return err(404, "没有这次运行")
        from .books import BookService
        return ok(BookService.steps(p))

    async def bk_get(request):
        return await guarded(_async(books_svc().get, request.match_info["name"]))

    async def bk_validate(request):
        d = await body(request)
        return await guarded(_async(books_svc().validate, str(d.get("text") or "")))

    async def bk_import(request):
        if request.content_type.startswith("multipart/"):
            text = ""
            async for part in (await request.multipart()):
                if part.name in ("file", "text"):
                    raw = await part.read(decode=False)
                    text = raw.decode("utf-8-sig", errors="replace")
        else:
            text = str((await body(request)).get("text") or "")
        return await guarded(_async(books_svc().import_, text))

    async def bk_save(request):
        d = await body(request)
        from hands.taskbook.book import BookConflict
        try:
            res = books_svc().save(str(d.get("name") or ""), str(d.get("text") or ""), d.get("rev"),
                                   bool(d.get("save_as")))
        except BookConflict as e:
            return err(409, str(e), conflict=True)
        except (ValueError, FileNotFoundError) as e:
            return err(400, str(e))
        return ok(res)

    async def bk_preview(request):
        d = await body(request)
        return await guarded(_async(books_svc().preview, str(d.get("text") or "")))

    async def bk_run(request):
        return await start_book(request, await body(request))
    r.add_get("/api/books", bk_list)
    r.add_get("/api/books/blocks", bk_blocks)
    r.add_get("/api/books/guide", bk_guide)
    r.add_get("/api/books/steps", bk_steps)
    r.add_post("/api/books/validate", bk_validate)
    r.add_post("/api/books/import", bk_import)
    r.add_post("/api/books/save", bk_save)
    r.add_post("/api/books/preview", bk_preview)
    r.add_post("/api/books/run", bk_run)
    r.add_get("/api/books/{name}", bk_get)

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
        if "decision" in d and svc.jobs.busy and d["decision"] != svc.settings.effective().get("decision"):
            return err(409, "任务运行中不能切换决策方式")
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
        from .settings import save_yaml_values
        p = svc.settings.save_default(DATA_ROOT / "config.yaml")
        save_yaml_values(DATA_ROOT / "config.yaml", {"driver": svc.config.driver})   # 当前驱动也作为默认
        return ok(str(p))

    async def settings_restore(request):
        svc.settings.restore()
        if not svc.jobs.busy:
            svc.settings.apply_now(svc.config)
        svc.push_soon()
        return await settings_get(request)
    # ---------- 人眼比对（ver1） ----------
    from . import compare as cmp

    async def cmp_sets(request):
        return ok(await asyncio.to_thread(cmp.list_sets))

    async def cmp_capture(request):
        d = await body(request)
        return await guarded(_start_job("compare_capture", {"set": d.get("set") or "default"}))

    async def cmp_run_model(request):
        d = await body(request)
        return await guarded(_start_job("compare_model", {"set": d.get("set") or "default", "model": d.get("model")}))

    async def _start_job(kind, params):
        return (await svc.jobs.start(kind, params)).to_dict()

    async def cmp_page(request):
        return await guarded(asyncio.to_thread(cmp.page_info, request.query.get("set", ""), request.query.get("page", "")))

    async def cmp_answer(request):
        d = await body(request)
        return await guarded(asyncio.to_thread(cmp.save_answer, d.get("set", ""), d.get("page", ""), d.get("answers")))

    async def cmp_report(request):
        return await guarded(asyncio.to_thread(cmp.latest_report, request.query.get("set", "")))

    async def cmp_img(request):
        try:
            p = cmp.image_path(request.match_info["set"], request.match_info["page"], request.match_info["file"])
        except ValueError as e:
            return err(400, str(e))
        if not p.exists():
            return err(404, "没有这张图")
        return web.FileResponse(p)
    r.add_get("/api/compare/sets", cmp_sets)
    r.add_post("/api/compare/capture", cmp_capture)
    r.add_post("/api/compare/run_model", cmp_run_model)
    r.add_get("/api/compare/page", cmp_page)
    r.add_post("/api/compare/answer", cmp_answer)
    r.add_get("/api/compare/report", cmp_report)
    r.add_get("/api/compare/img/{set}/{page}/{file}", cmp_img)

    async def vlm_status(request):
        from hands.config import vlm_settings
        from hands.vlm.client import ModelUnavailable, OllamaClient
        s = vlm_settings(svc.config)
        try:
            await OllamaClient(s).check()
            return ok({"ok": True, "message": f"模型服务可用：{s['model']}", "model": s["model"]})
        except ModelUnavailable as e:
            return ok({"ok": False, "message": str(e), "model": s["model"]})
    r.add_get("/api/vlm/status", vlm_status)
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
        from .remote import device_of
        dev = device_of(request) if remote else None
        c = Client(ws, svc, mode="thumb" if remote else "full", device_id=dev.id if dev else None)
        svc.clients.add(c)
        if remote and not svc.preview.want_on and svc.browser_alive():
            await svc.preview.set_on(True)          # 平板要看缩略画面：推流没开就打开
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
                if remote:                           # 平板不提供远程点击输入：只读
                    continue
                if t == "shown":
                    svc.preview.report_shown(int(m.get("seq", 0)), float(m.get("at", time.time())))
                elif t in ("mouse", "key", "text"):
                    if svc.jobs.accepts_input() and svc.browser_alive() and svc.browser.page is not None:
                        try:
                            await svc.input.handle(await svc.browser.page.panel_session(), m)
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
    if not remote:                       # 状态推送只需一份（本机入口一直在），远程连接也在 svc.clients 里
        app.cleanup_ctx.append(pusher)
    return app


async def serve(svc, port: int = 0, host: str = "127.0.0.1", token: str | None = None):
    """启动服务，返回 (runner, port, token)。"""
    from hands.config import DATA_ROOT, remote_settings
    from .inputs import InputForwarder
    from .remote import RemoteAccess
    token = token or make_token()
    svc.input = InputForwarder()
    if getattr(svc, "remote", None) is None:
        svc.remote = RemoteAccess(svc, remote_settings(svc.config), DATA_ROOT, log=svc.log)
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
