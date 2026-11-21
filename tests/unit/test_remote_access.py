"""远程入口：Host / Origin / 设备凭证 / 白名单、配对、吊销、档位、空闲自动关闭（假隧道、假时钟、随机端口）。"""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest

from panel.remote import RemoteAccess, allowed_path

FAKE = [sys.executable, str(Path(__file__).resolve().parents[1] / "panel" / "fake_cloudflared.py")]


class Clock:
    def __init__(self):
        self.t = 10000.0

    def __call__(self):
        return self.t


class FakeSvc:
    def __init__(self, runs_dir):
        self.clients = set()
        self.events = []
        self.preview = SimpleNamespace(want_on=True, latest=None, seq=0, _listeners=[],
                                       thumb=lambda: (None, 0), report_shown=lambda *a: None)
        self.config = SimpleNamespace(runs_dir=runs_dir)
        self.jobs = SimpleNamespace()
        self._push_evt = asyncio.Event()

    def push_soon(self):
        pass

    def event(self, name, payload):
        self.events.append(name)

    def browser_alive(self):
        return False

    def state(self):
        return {"t": "state", "busy": False}

    def log(self, *a):
        pass


async def make(tmp_path, **kw):
    svc = FakeSvc(tmp_path / "runs")
    clock = Clock()
    ra = RemoteAccess(svc, {"port": 0, "idle_close_s": 60, "cloudflared": kw.get("cloudflared", FAKE),
                            "tunnel_timeout_s": 10},
                      tmp_path, clock=clock, lan_ip_fn=lambda: "127.0.0.1", check_interval=3600)
    svc.remote = ra
    return svc, ra, clock


def lan(ra):
    return f"http://127.0.0.1:{ra.port}"


async def pair(s: aiohttp.ClientSession, ra, kind="lan", base=None, host=None):
    base = base or lan(ra)
    origin = ra.lan_origin if kind == "lan" else ra.public_url
    code = ra.devices.current_code(origin, kind).code
    h = {"Origin": origin}
    if host:
        h["Host"] = host
    async with s.post(base + "/api/pair", json={"code": code, "ua": "iPad Safari/604.1"}, headers=h) as r:
        body = await r.json()
        body["set_cookie"] = r.headers.get("Set-Cookie", "")
        return r.status, body, code


def test_allowlist():
    assert allowed_path("/api/books") and allowed_path("/api/books/run") and allowed_path("/api/state")
    assert allowed_path("/runs/x/book/report.html") and allowed_path("/m") and allowed_path("/ws")
    for p in ("/api/settings", "/api/remote", "/api/remote/mode", "/api/browser/start", "/api/jobs", "/api/nav",
              "/api/jobs/continue", "/api/profile/clear", "/compare", "/api/booksx"):
        assert not allowed_path(p), p


async def test_lan_pairing_and_guards(tmp_path):
    svc, ra, clock = await make(tmp_path)
    await ra.set_mode("lan")
    try:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as s:
            async with s.get(lan(ra) + "/api/state") as r:                 # 没有凭证
                assert r.status == 401
            async with s.get(lan(ra) + "/m", allow_redirects=False) as r:
                assert r.status == 302 and r.headers["Location"].startswith("/pair")
            async with s.get(lan(ra) + "/pair") as r:
                assert r.status == 200 and "配对" in await r.text()
            async with s.get(lan(ra) + "/api/state", headers={"Host": "evil.example:80"}) as r:
                assert r.status == 403                                     # DNS 重绑定
            st, body, code = await pair(s, ra)
            assert st == 200 and body["data"]["name"] == "平板 · Safari"
            sc = body["set_cookie"]
            assert sc.startswith("dev=") and "HttpOnly" in sc and "SameSite=Strict" in sc and "Max-Age=31536000" in sc
            assert "Secure" not in sc                                      # 局域网是 http
            async with s.get(lan(ra) + "/api/state") as r:
                assert r.status == 200
            async with s.post(lan(ra) + "/api/jobs/pause", headers={"Origin": "http://evil.example"}) as r:
                assert r.status == 403                                     # 跨站
            for p in ("/api/settings", "/api/remote"):
                async with s.get(lan(ra) + p) as r:
                    assert r.status == 403 and "只能在电脑上操作" in (await r.json())["error"]
            async with s.post(lan(ra) + "/api/remote/mode", json={"mode": "off"}, headers={"Origin": lan(ra)}) as r:
                assert r.status == 403
            # 同一码再用
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as s2:
                async with s2.post(lan(ra) + "/api/pair", json={"code": code}, headers={"Origin": lan(ra)}) as r:
                    assert r.status == 403 and "用过" in (await r.json())["error"]
            # 吊销后被拒
            did = ra.devices.list()[0]["id"]
            assert await ra.revoke(did)
            async with s.get(lan(ra) + "/api/state") as r:
                assert r.status == 401
        raw = (tmp_path / "panel_devices.json").read_text(encoding="utf-8")
        assert code not in raw
        log = [json.loads(x) for x in (tmp_path / "logs" / "remote.jsonl").read_text(encoding="utf-8").splitlines()]
        assert [x["event"] for x in log] == ["lan_on", "pair", "revoke"]
        assert code not in (tmp_path / "logs" / "remote.jsonl").read_text(encoding="utf-8")
    finally:
        await ra.set_mode("off")
    assert ra.mode == "off"


async def test_public_mode_idle_close_and_purge(tmp_path):
    svc, ra, clock = await make(tmp_path)
    st = await ra.set_mode("lan+public")
    try:
        assert st["mode"] == "lan+public" and st["public"] == "https://fake-test.trycloudflare.com"
        assert "codes" in st and st["codes"]["public"]["expires_in"] > 0
        host = "fake-test.trycloudflare.com"
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as s:
            stt, body, _ = await pair(s, ra, "public", base=lan(ra), host=host)
            assert stt == 200 and body["data"]["kind"] == "public"
        pub = [d for d in ra.devices.list() if d["kind"] == "public"]
        assert len(pub) == 1
        assert not await ra.check_idle()
        clock.t += 61
        assert await ra.check_idle()
        assert ra.mode == "lan" and ra.public_url is None and not [d for d in ra.devices.list() if d["kind"] == "public"]
        events = [json.loads(x)["event"] for x in (tmp_path / "logs" / "remote.jsonl").read_text(encoding="utf-8").splitlines()]
        assert "public_off" in events and json.loads(
            (tmp_path / "logs" / "remote.jsonl").read_text(encoding="utf-8").splitlines()[-1])["reason"] == "idle"
    finally:
        await ra.set_mode("off")


async def test_public_fails_without_cloudflared(tmp_path):
    svc, ra, _ = await make(tmp_path, cloudflared="Z:/nope/cloudflared.exe")
    with pytest.raises(RuntimeError, match="winget install Cloudflare.cloudflared"):
        await ra.set_mode("lan+public")
    try:
        assert ra.mode == "lan" and ra.state()["cloudflared"] is False
    finally:
        await ra.set_mode("off")


async def test_tunnel_down(tmp_path):
    svc, ra, _ = await make(tmp_path, cloudflared=FAKE + ["--exit-after", "0.8"])
    await ra.set_mode("lan+public")
    try:
        for _ in range(60):
            await asyncio.sleep(0.1)
            if ra.tunnel_down:
                break
        assert ra.tunnel_down and ra.mode == "lan" and "remote_changed" in svc.events
    finally:
        await ra.set_mode("off")
