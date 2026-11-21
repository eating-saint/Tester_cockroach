"""面板验收：平板远程（R 系列）。每项用独立的数据目录、随机端口、假 cloudflared，不碰真实隧道、不写项目里的设备文件。"""
from __future__ import annotations

import asyncio
import json
import sys
import time

import aiohttp

from hands.config import PROJECT_ROOT

FAKE = [sys.executable, str(PROJECT_ROOT / "tests" / "panel" / "fake_cloudflared.py")]
FAKE_HOST = "fake-test.trycloudflare.com"


async def fresh(ctx, name, **settings):
    """换一个干净的远程访问对象（旧的先关掉）。"""
    from panel.remote import RemoteAccess
    old = getattr(ctx.svc, "remote", None)
    if old is not None:
        await old.shutdown()
    d = ctx.run_root / f"remote_{name}"
    ra = RemoteAccess(ctx.svc, {"port": 0, "idle_close_s": 7200, "cloudflared": FAKE, "tunnel_timeout_s": 10, **settings},
                      d, lan_ip_fn=lambda: "127.0.0.1", check_interval=3600)
    ctx.svc.remote = ra
    return ra, d


def tablet():
    return aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True),
                                 headers={"User-Agent": "Mozilla/5.0 (iPad; CPU OS 17_0) Safari/604.1"})


async def pair(s, ra, kind="lan"):
    origin = ra.lan_origin if kind == "lan" else ra.public_url
    code = (await ra_state(ra))["codes"][kind]["link"].split("#c=")[1]
    h = {"Origin": origin}
    if kind == "public":
        h["Host"] = FAKE_HOST
    async with s.post(ra.lan_origin + "/api/pair", json={"code": code, "ua": "iPad Safari"}, headers=h) as r:
        return r.status, await r.json(), code


async def ra_state(ra):
    return ra.state()


async def r01(ctx):
    ra, _ = await fresh(ctx, "r01")
    st0 = (await ctx.get("/api/remote"))["data"]
    st = (await ctx.post("/api/remote/mode", {"mode": "lan"}))["data"]
    async with aiohttp.ClientSession() as s:
        async with s.get(ra.lan_origin + "/m", allow_redirects=False) as r:
            redirect = r.status, r.headers.get("Location")
    ok = st0["mode"] == "off" and st["mode"] == "lan" and st["lan"] and redirect[0] == 302 and redirect[1].startswith("/pair") \
        and ra.tunnel is None
    return ok, f"默认 {st0['mode']}；仅局域网 → {st['lan']}，未配对访问 /m → {redirect}；没有启动隧道"


async def r02(ctx):
    ra, _ = await fresh(ctx, "r02")
    await ra.set_mode("lan")
    async with tablet() as s:
        st, body, _ = await pair(s, ra)
        async with s.get(ra.lan_origin + "/api/state") as r:
            a = r.status
        async with s.get(ra.lan_origin + "/api/settings") as r:
            b = r.status
        async with s.get(ra.lan_origin + "/api/remote") as r:
            c = r.status
    ok = st == 200 and a == 200 and b == 403 and c == 403
    return ok, f"配对 {st} {body.get('data')}；/api/state {a}；/api/settings {b}；/api/remote {c}"


async def r03(ctx):
    ra, _ = await fresh(ctx, "r03")
    await ra.set_mode("lan")
    async with tablet() as s:
        st1, _, code = await pair(s, ra)
    async with tablet() as s2:
        async with s2.post(ra.lan_origin + "/api/pair", json={"code": code}, headers={"Origin": ra.lan_origin}) as r:
            again = r.status, (await r.json()).get("error")
    pc = ra.devices.issue(ra.lan_origin, "lan")
    pc.expires = time.time() - 1                                  # 拨过有效期
    async with tablet() as s3:
        async with s3.post(ra.lan_origin + "/api/pair", json={"code": pc.code}, headers={"Origin": ra.lan_origin}) as r:
            expired = r.status, (await r.json()).get("error")
    ok = st1 == 200 and again[0] == 403 and expired[0] == 403 and "过期" in expired[1]
    return ok, f"重用 → {again}；过期 → {expired}"


async def r04(ctx):
    ra, _ = await fresh(ctx, "r04")
    await ra.set_mode("lan")
    await ctx.post("/api/nav", {"url": ctx.fixture("f01_static/index.html")})
    calls = []
    orig = ctx.svc.input.handle

    async def spy(sess, m):
        calls.append(m)
        return await orig(sess, m)
    ctx.svc.input.handle = spy
    frames, widths = [], []
    try:
        async with tablet() as s:
            await pair(s, ra)
            ws_url = ra.lan_origin.replace("http", "ws") + "/ws"
            async with s.ws_connect(ws_url, headers={"Origin": ra.lan_origin}) as ws:
                await ws.send_str(json.dumps({"t": "mouse", "kind": "down", "x": 10, "y": 10, "button": 0}))
                t0 = time.monotonic()

                async def scroll():
                    for i in range(12):
                        await ctx.post("/api/nav", {"url": ctx.fixture(f"f01_static/index.html?x={i}")})
                        await asyncio.sleep(0.4)
                task = asyncio.create_task(scroll())
                while time.monotonic() - t0 < 6:
                    try:
                        m = await ws.receive(timeout=1)
                    except asyncio.TimeoutError:
                        continue
                    if m.type == aiohttp.WSMsgType.BINARY:
                        frames.append(time.monotonic() - t0)
                        from io import BytesIO
                        from PIL import Image
                        n = int.from_bytes(m.data[:4], "big")
                        widths.append(Image.open(BytesIO(m.data[4 + n:])).width)
                    elif m.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
                task.cancel()
    finally:
        ctx.svc.input.handle = orig
    gaps = [b - a for a, b in zip(frames, frames[1:])]
    ok = bool(frames) and len(frames) <= 7 and all(w <= 640 for w in widths) and all(g >= 0.9 for g in gaps) and not calls
    return ok, f"6 秒内 {len(frames)} 张缩略图，宽 {sorted(set(widths))}，最小间隔 {min(gaps) if gaps else '-'}；输入转发 {len(calls)} 次"


async def r05(ctx):
    ra, _ = await fresh(ctx, "r05")
    await ra.set_mode("lan")
    async with tablet() as s:
        await pair(s, ra)
        ws = await s.ws_connect(ra.lan_origin.replace("http", "ws") + "/ws", headers={"Origin": ra.lan_origin})
        await ws.receive(timeout=3)
        did = ra.devices.list()[0]["id"]
        await ctx.post("/api/remote/devices/revoke", {"id": did})
        t0 = time.monotonic()
        closed = None
        while time.monotonic() - t0 < 3:
            m = await ws.receive(timeout=3)
            if m.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING):
                closed = time.monotonic() - t0
                break
        async with s.get(ra.lan_origin + "/api/state") as r:
            after = r.status
        await ws.close()
    ok = closed is not None and closed < 1.0 and after == 401
    return ok, f"吊销后 {closed and round(closed, 2)} 秒断开；之后请求 → {after}"


async def r06(ctx):
    """两端同时改树 + 修改来源（R06、R11）。"""
    from books_check import stub_client
    ra, _ = await fresh(ctx, "r06")
    await ra.set_mode("lan")
    ctx.svc.jobs.book_client = stub_client()
    await ctx.post("/api/books/run", {"name": "fixture_transformers", "budget": 2})
    try:
        await ctx.wait_state(lambda s: (s.get("job") or {}).get("progress", {}).get("state") == "waiting", 120)
        async with tablet() as s:
            await pair(s, ra)
            name = ra.devices.list()[0]["name"]
            rev = (await ctx.get("/api/expand/tree"))["data"]["rev"]
            pc = await ctx.req("POST", "/api/expand/edit", {"op": "add_group", "key": "cybertron", "values": ["iacon"], "rev": rev},
                               expect_ok=False)
            async with s.post(ra.lan_origin + "/api/expand/edit", json={"op": "grant", "n": 1, "rev": rev},
                              headers={"Origin": ra.lan_origin}) as r:
                tab = r.status, (await r.json()).get("error")
            rev2 = (await ctx.get("/api/expand/tree"))["data"]["rev"]
            async with s.post(ra.lan_origin + "/api/expand/edit", json={"op": "finish", "rev": rev2},
                              headers={"Origin": ra.lan_origin}) as r:
                tab2 = r.status
        job = await ctx.wait_job(60)
        edits = [json.loads(x) for x in (ctx.svc.config.runs_dir / job["result"]["run"] / "book" / "edits.jsonl")
                 .read_text(encoding="utf-8").splitlines()]
        srcs = {(e["op"], e["source"]) for e in edits}
        ok = pc[0] == 200 and tab[0] == 409 and "另一端" in (tab[1] or "") and tab2 == 200 \
            and ("add_group", "电脑") in srcs and ("finish", name) in srcs
        return ok, f"电脑先改 → {pc[0]}；平板用旧版本 → {tab}；平板用新版本结束 → {tab2}；来源 {sorted(srcs)}"
    finally:
        if (await ctx.state())["busy"]:
            await ctx.post("/api/jobs/stop")
            await ctx.wait_job(60)


async def r07(ctx):
    ra, _ = await fresh(ctx, "r07")
    st = (await ctx.post("/api/remote/mode", {"mode": "lan+public"}))["data"]
    async with tablet() as s:
        code, body, _ = await pair(s, ra, "public")
    kinds = [d["kind"] for d in ra.devices.list()]
    st2 = (await ctx.post("/api/remote/mode", {"mode": "lan"}))["data"]
    ok = st["public"] == f"https://{FAKE_HOST}" and code == 200 and "public" in kinds and \
        not [d for d in st2["devices"] if d["kind"] == "public"] and ra.tunnel is None
    return ok, f"公网 {st['public']}；公网配对 {code}；关掉公网后公网设备清除、隧道进程结束"


async def r08(ctx):
    ra, _ = await fresh(ctx, "r08", cloudflared=FAKE + ["--exit-after", "1"])
    await ra.set_mode("lan+public")
    for _ in range(50):
        await asyncio.sleep(0.1)
        if ra.tunnel_down:
            break
    st = (await ctx.get("/api/remote"))["data"]
    return st["tunnel_down"] and st["mode"] == "lan", f"隧道意外退出 → tunnel_down={st['tunnel_down']}，档位 {st['mode']}"


async def r09(ctx):
    ra, _ = await fresh(ctx, "r09", idle_close_s=2)
    await ra.set_mode("lan+public")
    first = await ra.check_idle()
    await asyncio.sleep(2.5)
    closed = await ra.check_idle()
    return (not first) and closed and ra.mode == "lan" and ra.public_url is None, f"2 秒无访问后自动关闭公网：{closed}"


async def r10(ctx):
    ra, d = await fresh(ctx, "r10")
    await ra.set_mode("lan")
    async with tablet() as s:
        _, _, code = await pair(s, ra)
        secret = s.cookie_jar.filter_cookies(aiohttp.client.URL(ra.lan_origin))["dev"].value.split(".", 1)[1]
    await ra.revoke(ra.devices.list()[0]["id"])
    await ra.set_mode("lan+public")
    await ra.set_mode("off")
    log = (d / "logs" / "remote.jsonl").read_text(encoding="utf-8")
    events = [json.loads(x)["event"] for x in log.splitlines()]
    devfile = (d / "panel_devices.json").read_text(encoding="utf-8") if (d / "panel_devices.json").exists() else ""
    ok = {"lan_on", "pair", "revoke", "public_on", "public_off", "lan_off"} <= set(events) and code not in log \
        and secret not in log and secret not in devfile
    return ok, f"日志事件 {events}；日志与设备文件里没有配对码和凭证明文"


REMOTE_CHECKS = [("R01", r01), ("R02", r02), ("R03", r03), ("R04", r04), ("R05", r05), ("R06", r06), ("R07", r07),
                 ("R08", r08), ("R09", r09), ("R10", r10)]
