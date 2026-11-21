"""平板远程访问（spec F9–F12）：第二个 aiohttp 站点（0.0.0.0:<端口>），与本机入口共用同一个服务对象。

档位：off / lan（仅局域网）/ lan+public（另开 cloudflared 临时隧道）。只能在电脑面板上开关。
远程入口的每个请求：Host 检查（防 DNS 重绑定）→ Origin 检查（防跨站）→ 设备凭证（除配对入口）→ 路由白名单。
公网在面板退出、手动关闭、超过 idle_close_s 没有已配对设备访问时自动关闭，公网设备随之作废。
开关、配对、吊销、自动关闭写进 <数据目录>/logs/remote.jsonl（不写凭证与配对码）。
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import time
from pathlib import Path

from aiohttp import web

from .devices import DeviceStore
from .tunnel import INSTALL_HINT, Tunnel, find_exe

MODES = ("off", "lan", "lan+public")
COOKIE = "dev"
try:                                            # 请求里存放的访问地址与设备
    ORIGIN_KEY = web.RequestKey("remote_origin", str)
    DEVICE_KEY = web.RequestKey("remote_device", object)
except AttributeError:                          # 旧版 aiohttp
    ORIGIN_KEY, DEVICE_KEY = "remote_origin", "remote_device"


def device_of(request):
    return request.get(DEVICE_KEY)
# 远程入口只放行这些（其余一律 403 "只能在电脑上操作"）
ALLOW_EXACT = {"/m", "/tree", "/build", "/ws", "/api/state", "/api/runs", "/api/remote/me",
               "/api/jobs/pause", "/api/jobs/resume", "/api/jobs/confirm", "/api/jobs/stop",
               "/api/expand/tree", "/api/expand/edit", "/api/expand/start"}
ALLOW_PREFIX = ("/api/books", "/runs/")
OPEN_EXACT = {"/pair", "/api/pair"}
PAGES = {"/", "/m", "/tree", "/build"}


def lan_ipv4() -> str | None:
    """本机的局域网 IPv4：UDP connect 到保留地址拿出口网卡地址（不发包）；不是私网地址时再从主机名找。"""
    cands = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 80))
            cands.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        cands += socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        pass
    for ip in cands:
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if a.version == 4 and a.is_private and not a.is_loopback and not a.is_link_local:
            return ip
    return None


def allowed_path(path: str) -> bool:
    return path in ALLOW_EXACT or any(path == p or path.startswith(p + "/") or path.startswith(p + "?")
                                      for p in ALLOW_PREFIX) or path.startswith("/runs/")


class RemoteAccess:
    def __init__(self, svc, settings: dict, data_dir, clock=time.time, lan_ip_fn=lan_ipv4, check_interval: float = 60,
                 log=None):
        self.svc = svc
        self.s = dict(settings)
        self.data_dir = Path(data_dir)
        self.clock = clock
        self.lan_ip_fn = lan_ip_fn
        self.check_interval = check_interval
        self.devices = DeviceStore(self.data_dir / "panel_devices.json", clock)
        self.mode = "off"
        self.lan_ip: str | None = None
        self.port: int | None = None
        self.public_url: str | None = None
        self.public_since: float | None = None
        self.tunnel: Tunnel | None = None
        self.tunnel_down = False
        self.last_error = ""
        self.firewall_hint = False
        self._runner: web.AppRunner | None = None
        self._idle: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._log_fn = log or (lambda m: None)

    # ---------------- 地址 ----------------
    @property
    def lan_origin(self) -> str | None:
        return f"http://{self.lan_ip}:{self.port}" if self.lan_ip and self.port else None

    @property
    def public_host(self) -> str | None:
        return self.public_url.split("://", 1)[1] if self.public_url else None

    def origin_of(self, request: web.Request) -> str | None:
        """这个请求是从哪个地址来的（局域网 / 本次公网）；Host 不认识返回 None。"""
        host = (request.host or "").lower()
        if self.lan_ip and self.port and host == f"{self.lan_ip}:{self.port}":
            return self.lan_origin
        if self.public_host and host == self.public_host:
            return self.public_url
        # cloudflared 把 Host 改成了本地地址时：从回环地址来、带 Cloudflare 头的请求算公网
        peer = request.remote or ""
        if self.public_url and peer in ("127.0.0.1", "::1") and (request.headers.get("Cf-Ray") or
                                                                   request.headers.get("Cf-Connecting-Ip")):
            return self.public_url
        return None

    # ---------------- 日志 ----------------
    def log(self, event: str, **kw):
        d = self.data_dir / "logs"
        d.mkdir(parents=True, exist_ok=True)
        row = {"t": self.clock(), "time": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, "mode": self.mode, **kw}
        with (d / "remote.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._log_fn(f"[remote] {event} {json.dumps(kw, ensure_ascii=False)}")

    # ---------------- 档位 ----------------
    async def set_mode(self, mode: str) -> dict:
        if mode not in MODES:
            raise ValueError(f"档位只能是 {' / '.join(MODES)}")
        async with self._lock:
            self.last_error = ""
            if mode == "off":
                await self._stop_public("manual")
                await self._stop_lan()
            elif mode == "lan":
                await self._stop_public("manual")
                await self._start_lan()
            else:
                await self._start_lan()
                await self._start_public()
            return self.state()

    async def _start_lan(self):
        if self._runner is not None:
            return
        ip = self.lan_ip_fn()
        if not ip:
            raise RuntimeError("找不到本机的局域网地址：请确认电脑连着 Wi-Fi 或网线")
        self.lan_ip = ip
        app = create_remote_app(self.svc, self)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", int(self.s.get("port", 8791)))
        try:
            await site.start()
        except OSError as e:
            await runner.cleanup()
            raise RuntimeError(f"远程端口 {self.s.get('port')} 打不开：{e}") from None
        self._runner = runner
        self.port = site._server.sockets[0].getsockname()[1]
        self.mode = "lan"
        self.firewall_hint = True
        self.devices.current_code(self.lan_origin, "lan")
        if self._idle is None or self._idle.done():
            self._idle = asyncio.create_task(self._idle_loop())
        self.log("lan_on", address=self.lan_origin)
        self.svc.push_soon()

    async def _stop_lan(self):
        if self._runner is None:
            self.mode = "off"
            return
        await self._close_ws(lambda c: True)
        await self._runner.cleanup()
        self._runner = None
        if self._idle is not None:
            self._idle.cancel()
            self._idle = None
        self.log("lan_off")
        self.mode = "off"
        self.svc.push_soon()

    async def _start_public(self):
        if self.tunnel is not None and self.tunnel.running:
            self.mode = "lan+public"
            return
        exe = find_exe(self.s.get("cloudflared") or None)
        if exe is None:
            self.last_error = INSTALL_HINT
            self.log("public_failed", why="no_cloudflared")
            raise RuntimeError(INSTALL_HINT)
        self.tunnel = Tunnel(exe, self.port, on_down=self._on_tunnel_down, log=self._log_fn)
        try:
            url = await self.tunnel.start(float(self.s.get("tunnel_timeout_s", 30)))
        except RuntimeError as e:
            self.tunnel = None
            self.last_error = str(e)
            self.log("public_failed", why=str(e)[:200])
            raise
        self.public_url = url
        self.public_since = self.clock()
        self.tunnel_down = False
        self.mode = "lan+public"
        self.devices.current_code(self.public_url, "public")
        self.log("public_on", address=url)
        self.svc.push_soon()

    async def _stop_public(self, reason: str):
        t, self.tunnel = self.tunnel, None
        had = self.public_url is not None
        if t is not None:
            await t.stop()
        gone = self.devices.purge("public")
        ids = {d.id for d in gone}
        await self._close_ws(lambda c: getattr(c, "device_id", None) in ids)
        self.public_url = None
        self.public_since = None
        if self.mode == "lan+public":
            self.mode = "lan"
        if had or t is not None:
            self.log("public_off", reason=reason, devices=[d.name for d in gone])
        self.svc.push_soon()

    async def _on_tunnel_down(self):
        async with self._lock:
            self.tunnel = None
            await self._stop_public("tunnel_down")
            self.tunnel_down = True
            self.svc.event("remote_changed", {"tunnel_down": True})

    async def _idle_loop(self):
        while True:
            await asyncio.sleep(self.check_interval)
            try:
                await self.check_idle()
            except Exception as e:
                self._log_fn(f"[remote] 空闲检查出错：{e}")

    async def check_idle(self) -> bool:
        if self.mode != "lan+public" or self.public_since is None:
            return False
        seen = [d.last_seen for d in self.devices.devices.values() if d.kind == "public"]
        last = max(seen + [self.public_since])
        if self.clock() - last > float(self.s.get("idle_close_s", 7200)):
            async with self._lock:
                await self._stop_public("idle")
            self.svc.event("remote_changed", {"idle_closed": True})
            return True
        return False

    # ---------------- 设备 ----------------
    async def _close_ws(self, pred):
        for c in list(getattr(self.svc, "clients", [])):
            if getattr(c, "mode", "full") == "thumb" and pred(c):
                try:
                    await c.ws.close(code=4001, message=b"revoked")
                except Exception:
                    pass

    async def revoke(self, did: str) -> bool:
        dev = self.devices.revoke(did)
        if dev is None:
            return False
        await self._close_ws(lambda c: getattr(c, "device_id", None) == did)
        self.log("revoke", device=dev.name, kind=dev.kind)
        self.svc.push_soon()
        return True

    def rename(self, did: str, name: str):
        dev = self.devices.rename(did, name)
        self.log("rename", device=dev.name)
        return dev.public()

    def refresh_code(self, kind: str):
        origin = self.lan_origin if kind == "lan" else self.public_url
        if not origin:
            raise ValueError("这一档没有开")
        self.devices.issue(origin, kind)
        return self.state()

    def redeem(self, code: str, origin: str, ua: str):
        dev, secret = self.devices.redeem(code, origin, ua)
        self.log("pair", device=dev.name, kind=dev.kind)
        self.svc.push_soon()
        return dev, secret

    def qr_svg(self, kind: str) -> str:
        import segno
        origin = self.lan_origin if kind == "lan" else self.public_url
        if not origin:
            raise ValueError("这一档没有开")
        import io
        pc = self.devices.current_code(origin, kind)
        buf = io.BytesIO()                       # 独立的 SVG 文件（带 xmlns），<img> 才能显示
        segno.make(f"{origin}/pair#c={pc.code}", error="m").save(buf, kind="svg", scale=5, border=2, xmldecl=False)
        return buf.getvalue().decode("utf-8")

    def pair_url(self, kind: str) -> str | None:
        origin = self.lan_origin if kind == "lan" else self.public_url
        if not origin:
            return None
        return f"{origin}/pair#c={self.devices.current_code(origin, kind).code}"

    # ---------------- 状态 ----------------
    def brief(self) -> dict:
        return {"mode": self.mode, "devices": len(self.devices.devices), "tunnel_down": self.tunnel_down}

    def state(self) -> dict:
        codes = {}
        for kind, origin in (("lan", self.lan_origin), ("public", self.public_url)):
            if origin:
                pc = self.devices.current_code(origin, kind)
                codes[kind] = {"expires_in": max(0, int(pc.expires - self.clock())),
                               "link": f"{origin}/pair#c={pc.code}"}    # 只给本机面板（/api/remote 不对远程开放）
        return {"mode": self.mode, "lan": self.lan_origin, "public": self.public_url, "codes": codes,
                "devices": self.devices.list(), "cloudflared": find_exe(self.s.get("cloudflared") or None) is not None,
                "install_hint": INSTALL_HINT, "firewall_hint": self.firewall_hint, "tunnel_down": self.tunnel_down,
                "error": self.last_error, "idle_close_s": self.s.get("idle_close_s")}

    async def shutdown(self):
        try:
            await self.set_mode("off")
        except Exception:
            pass


# ---------------- 远程入口 ----------------
def create_remote_app(svc, access: RemoteAccess) -> web.Application:
    from .server import STATIC, register_routes

    def json_err(status, msg):
        return web.json_response({"ok": False, "error": msg}, status=status,
                                 dumps=lambda o: json.dumps(o, ensure_ascii=False))

    @web.middleware
    async def guard(request: web.Request, handler):
        origin = access.origin_of(request)
        if origin is None:
            return web.Response(status=403, text="bad host")
        path = request.path
        if path.startswith("/api/") or path == "/ws":
            got = request.headers.get("Origin")
            if got is not None and got != origin:
                return web.Response(status=403, text="bad origin")
        request[ORIGIN_KEY] = origin
        if path == "/":
            raise web.HTTPFound("/m")
        if not (path in OPEN_EXACT or path.startswith("/static/")):
            dev = access.devices.verify(request.cookies.get(COOKIE), origin)
            if dev is None:
                if path in PAGES:
                    raise web.HTTPFound("/pair?need=1")
                return json_err(401, "这台设备还没有配对：请在电脑上扫码配对")
            if not allowed_path(path):
                return json_err(403, "只能在电脑上操作")
            request[DEVICE_KEY] = dev
        try:
            resp = await handler(request)
        except web.HTTPException as e:
            resp = e
        ws_scheme = "wss" if origin.startswith("https") else "ws"
        host = origin.split("://", 1)[1]
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; "
            f"connect-src 'self' {ws_scheme}://{host}; frame-src 'self'; frame-ancestors 'self'; "
            "form-action 'self'; base-uri 'self'")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    app = web.Application(middlewares=[guard], client_max_size=8 * 1024 * 1024)
    register_routes(app, svc, token_for_page="", page_cookie=None,
                    source_of=lambda req: device_of(req).name if device_of(req) else "平板", remote=True)

    async def pair_page(request):
        return web.FileResponse(STATIC / "pair.html", headers={"Content-Type": "text/html; charset=utf-8"})

    async def pair_api(request):
        try:
            d = await request.json()
        except Exception:
            d = {}
        origin = request[ORIGIN_KEY]
        try:
            dev, secret = access.redeem(str(d.get("code") or ""), origin, str(d.get("ua") or request.headers.get("User-Agent", "")))
        except PermissionError as e:
            return json_err(403, str(e))
        resp = web.json_response({"ok": True, "data": {"name": dev.name, "kind": dev.kind}},
                                 dumps=lambda o: json.dumps(o, ensure_ascii=False))
        resp.set_cookie(COOKIE, f"{dev.id}.{secret}", httponly=True, samesite="Strict", path="/",
                        secure=origin.startswith("https"), max_age=31536000 if dev.kind == "lan" else None)
        return resp

    async def me(request):
        dev = device_of(request)
        return web.json_response({"ok": True, "data": {"name": dev.name, "kind": dev.kind, "id": dev.id}},
                                 dumps=lambda o: json.dumps(o, ensure_ascii=False))
    app.router.add_get("/pair", pair_page)
    app.router.add_post("/api/pair", pair_api)
    app.router.add_get("/api/remote/me", me)
    return app
