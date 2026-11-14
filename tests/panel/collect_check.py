"""K8–K10：本地采集页自动检查（P17 的自动部分）。

在面板自己的自动化 Chrome 里打开 /collect，用 CDP 鼠标事件模拟采集：
正常 1 条入库；点偏 / 移出采集区 / 超 5 秒 / 点太少 各被作废；删除、导出、替换导入、合并导入、回放。
样本写进临时库，不碰真实的 trajlib/samples.json。
"""
from __future__ import annotations

import asyncio
import json
import math
import tempfile
from pathlib import Path


async def _eval(page, expr: str, await_promise: bool = False):
    r = await page.session.send("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                                     "awaitPromise": await_promise})
    if "exceptionDetails" in r:
        raise AssertionError(f"JS 异常: {r['exceptionDetails']}")
    return r["result"].get("value")


async def _mouse(page, kind, x, y, button="none"):
    p = {"type": kind, "x": x, "y": y, "button": button, "buttons": 1 if kind == "mousePressed" else 0}
    if kind in ("mousePressed", "mouseReleased"):
        p["clickCount"] = 1
    await page.session.send("Input.dispatchMouseEvent", p)


def _c(b):
    return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2


async def _wait_q(page, timeout=5):
    for _ in range(int(timeout / 0.1)):
        q = await _eval(page, "window.__question()")
        if q and not q["recording"]:
            return q
        await asyncio.sleep(0.1)
    raise AssertionError("没有出题")


async def _do_one(page, mode="ok"):
    """mode: ok | miss | leave | slow | few"""
    q = await _wait_q(page)
    sx, sy = _c(q["start"])
    tx, ty = _c(q["target"])
    await _mouse(page, "mouseMoved", sx, sy)
    await _mouse(page, "mousePressed", sx, sy, "left")
    await _mouse(page, "mouseReleased", sx, sy, "left")
    if mode == "leave":
        await _mouse(page, "mouseMoved", sx, sy + 3)
        await _mouse(page, "mouseMoved", 3, 3)          # 采集区外（页面左上角标题栏）
        await asyncio.sleep(0.3)
        return q
    if mode == "slow":
        await _mouse(page, "mouseMoved", sx + 2, sy + 2)
        await asyncio.sleep(5.4)
        return q
    n = 3 if mode == "few" else 25
    ex, ey = tx, ty
    if mode == "miss":   # 起点和目标连线上、目标框外的一点（一定还在采集区内）
        b = q["target"]
        for f in (0.5, 0.4, 0.3, 0.2):
            ex, ey = sx + (tx - sx) * f, sy + (ty - sy) * f
            if not (b["x"] <= ex <= b["x"] + b["width"] and b["y"] <= ey <= b["y"] + b["height"]):
                break
    for i in range(1, n + 1):
        s = i / n
        bend = math.sin(math.pi * s) * 20
        await _mouse(page, "mouseMoved", sx + (ex - sx) * s + bend, sy + (ey - sy) * s)
        await asyncio.sleep(0.016)
    await _mouse(page, "mouseMoved", ex, ey)
    await _mouse(page, "mousePressed", ex, ey, "left")
    await _mouse(page, "mouseReleased", ex, ey, "left")
    await asyncio.sleep(0.4)
    return q


async def _move_click(page, sx, sy, ex, ey, n=25):
    for i in range(1, n + 1):
        s = i / n
        await _mouse(page, "mouseMoved", sx + (ex - sx) * s + math.sin(math.pi * s) * 15, sy + (ey - sy) * s)
        await asyncio.sleep(0.016)
    await _mouse(page, "mouseMoved", ex, ey)
    await _mouse(page, "mousePressed", ex, ey, "left")
    await _mouse(page, "mouseReleased", ex, ey, "left")


async def _wait_progress(ctx, pred, timeout=10):
    for _ in range(int(timeout / 0.2)):
        st = await ctx.state()
        p = (st.get("job") or {}).get("progress") or {}
        if pred(p, st):
            return p, st
        await asyncio.sleep(0.2)
    raise AssertionError(f"进度条件未满足：{p}")


async def p30_real_sample(ctx):
    """K33–K34：真实网站采集的自动部分（测试开关允许无界面）。"""
    from panel.samples import SampleStore

    tmp = Path(tempfile.mkdtemp(prefix="panel_real_")) / "samples.json"
    real_store = ctx.svc.samples
    ctx.svc.samples = SampleStore(tmp)
    notes = []
    try:
        await ctx.post("/api/browser/start")
        await ctx.post("/api/nav", {"url": ctx.fixture("f12_mouse/index.html")})
        page = ctx.svc.browser.page
        keys0 = await _eval(page, "Object.keys(window).sort().join(',')")
        # 无界面时不加测试开关会被拒绝
        st_code, _ = await ctx.start_job("sample_real", {"device": "mouse"}, expect_ok=False)
        await ctx.start_job("sample_real", {"device": "mouse", "allow_headless": True})
        await asyncio.sleep(0.8)
        # 第一下：起点
        sx, sy = 640, 400
        await _mouse(page, "mouseMoved", sx, sy)
        await _mouse(page, "mousePressed", sx, sy, "left")
        await _mouse(page, "mouseReleased", sx, sy, "left")
        saved = 0
        for k in range(3):
            p, _ = await _wait_progress(ctx, lambda p, s: p.get("question"))
            b = p["question"]["box"]
            ex, ey = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
            await _move_click(page, sx, sy, ex, ey)
            p, _ = await _wait_progress(ctx, lambda p, s, n=saved: p.get("saved", 0) + p.get("rejected", 0) > n)
            saved = p["saved"]
            notes.append(f"题{k + 1}:{(p.get('last') or {}).get('reason', 'ok')}")
            sx, sy = ex, ey
        lib = ctx.svc.samples.library.samples
        s0 = lib[0] if lib else None
        fields_ok = bool(s0) and s0.source == "real" and s0.domain == "127.0.0.1" and len(s0.points) >= 8 and \
            not any(isinstance(v, str) and len(v) > 40 for v in s0.to_dict().values())
        notes.append(f"无界面拒绝={st_code == 400}；3 题保存 {saved} 条，来源/域名正确={fields_ok}")

        # 停止后再点，不再入库
        await ctx.post("/api/jobs/stop")
        await ctx.wait_job(15)
        n_before = len(lib)
        await _move_click(page, sx, sy, 200, 200)
        await asyncio.sleep(0.8)
        no_more = len(ctx.svc.samples.library.samples) == n_before
        keys1 = await _eval(page, "Object.keys(window).sort().join(',')")
        notes.append(f"停止后不再采={no_more}；主环境无新增全局变量={keys0 == keys1}")

        # K34：点中跳转链接 → 样本已保存、提示跳转，可后退
        await ctx.post("/api/nav", {"url": ctx.fixture("f11_nav/index.html")})
        await ctx.start_job("sample_real", {"device": "mouse", "allow_headless": True})
        await asyncio.sleep(0.8)
        jump_ok = back_ok = False
        for attempt in range(4):
            page = ctx.svc.browser.page
            # 起点放在“同页跳转”下方约 160 px：新标签页链接离它不到 150 px，不会被选中
            els = (await ctx.get("/api/elements"))["data"]["elements"]
            same = next(e["box"] for e in els if e["name"] == "同页跳转")
            sx, sy = same["x"] + same["width"] / 2, same["y"] + same["height"] / 2 + 160
            await _mouse(page, "mousePressed", sx, sy, "left")
            await _mouse(page, "mouseReleased", sx, sy, "left")
            p, _ = await _wait_progress(ctx, lambda p, s: p.get("question"))
            if p["question"]["name"] != "同页跳转":   # 新标签页链接：跳过这题，换起点重来
                await asyncio.sleep(0.2)
                continue
            b = p["question"]["box"]
            n0 = len(ctx.svc.samples.library.samples)
            await _move_click(page, sx, sy, b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
            p, _ = await _wait_progress(ctx, lambda p, s: "已跳转" in (p.get("msg") or ""))
            jump_ok = "样本已保存" in p["msg"] and len(ctx.svc.samples.library.samples) == n0 + 1
            await ctx.post("/api/nav/back")
            back_ok = (await ctx.svc.browser.page.url()).endswith("f11_nav/index.html")
            break
        await ctx.post("/api/jobs/stop")
        await ctx.wait_job(15)
        notes.append(f"跳转后样本已保存={jump_ok}；后退回原页={back_ok}")
        ok = st_code == 400 and saved >= 2 and fields_ok and no_more and keys0 == keys1 and jump_ok and back_ok
        return ok, "；".join(notes)
    finally:
        if ctx.svc.jobs.busy:
            ctx.svc.jobs.stop()
        ctx.svc.samples = real_store


async def p17_collect(ctx):
    from panel.samples import SampleStore

    tmp = Path(tempfile.mkdtemp(prefix="panel_collect_")) / "samples.json"
    real_store = ctx.svc.samples
    ctx.svc.samples = SampleStore(tmp)
    notes = []
    try:
        await ctx.post("/api/browser/start")
        await ctx.post("/api/nav", {"url": ctx.base + "/collect"})
        page = ctx.svc.browser.page
        await asyncio.sleep(0.5)

        # K8 自检：200 道题，八方向都有，距离 / 尺寸都在范围内
        st = await _eval(page, "window.__selfTest(200)")
        st2 = await _eval(page, "window.__selfTest(200, 760, 480)")
        k8 = all(st["dirs"]) and st["distOk"] and st["sizeOk"] and st["fails"] == 0 and \
            all(st2["dirs"]) and st2["distOk"] and st2["sizeOk"] and st2["fails"] == 0
        notes.append(f"K8 自检 {st['dirs']} 小窗口 {st2['dirs']}")

        # K9：选设备、开始一组、正常 1 条
        await _eval(page, "document.querySelector('input[value=mouse]').click(); document.getElementById('bGroup').click(); 1")
        q = await _do_one(page, "ok")
        n_ok = len(ctx.svc.samples.library.samples)
        msg_ok = await _eval(page, "document.getElementById('msg').textContent")
        grid = ctx.svc.samples.grid()
        k9_ok = n_ok == 1 and "已保存" in msg_ok and sum(c["local"] for c in grid["mouse"].values()) == 1
        notes.append(f"正常 1 条入库={k9_ok}")

        # 作废四种
        rej = {}
        for mode, word in (("miss", "没点中"), ("leave", "移出"), ("slow", "超过 5 秒"), ("few", "点太少")):
            await asyncio.sleep(0.8)
            await _do_one(page, mode)
            m = await _eval(page, "document.getElementById('msg').textContent")
            rej[mode] = word in m and len(ctx.svc.samples.library.samples) == 1
        notes.append("作废 " + " ".join(f"{k}={v}" for k, v in rej.items()))

        # 再采 2 条，共 3 条
        for _ in range(2):
            await asyncio.sleep(0.8)
            await _do_one(page, "ok")
        n3 = len(ctx.svc.samples.library.samples)

        # K10：删 1 条 → 格子减 1；导出 → 替换导入空库 → 合并导入导出文件
        sid = ctx.svc.samples.library.samples[0].id
        await ctx.req("DELETE", f"/api/samples/{sid}")
        n_after_del = len(ctx.svc.samples.library.samples)
        async with ctx.http.get(ctx.base + "/api/samples/export") as r:
            exported = await r.read()
        empty = json.dumps({"version": 1, "samples": []}).encode()
        async with ctx.http.post(ctx.base + "/api/samples/import?mode=replace", data=empty) as r:
            await r.json()
        n_empty = len(ctx.svc.samples.library.samples)
        for _ in range(2):   # 合并两次，条数不变
            async with ctx.http.post(ctx.base + "/api/samples/import?mode=merge", data=exported) as r:
                await r.json()
        n_back = len(ctx.svc.samples.library.samples)
        k10 = n3 == 3 and n_after_del == 2 and n_empty == 0 and n_back == 2
        notes.append(f"自检失败数 {st['fails']}/{st2['fails']}；条数 {n3}→删 {n_after_del}→替换空库 {n_empty}→合并两次 {n_back}")

        # 回放：样本视图里点第一条 → 回放 → 画布非空
        await _eval(page, "document.querySelector('button[data-view=samples]').click(); 1")
        await asyncio.sleep(0.6)
        await _eval(page, "document.querySelector('#list tr[data-id]').click(); 1")
        await asyncio.sleep(0.4)
        await _eval(page, "document.getElementById('bReplay').click(); 1")
        await asyncio.sleep(1.2)
        painted = await _eval(page, """(() => { const c = document.getElementById('draw');
            const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) n++; return n; })()""")
        notes.append(f"回放后画布像素 {painted}")
        shot = await page.session.send("Page.captureScreenshot", {"format": "png"})
        import base64
        ctx.artifact("P17_collect.png").write_bytes(base64.b64decode(shot["data"]))

        ok = k8 and k9_ok and all(rej.values()) and k10 and painted > 50
        return ok, "；".join(notes)
    finally:
        ctx.svc.samples = real_store
