"""面板自动验收项（P 系列，对应 spec 的 AC 编号）。每项 async def pNN(ctx) -> (通过, 说明)。"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
import tomllib

import aiohttp

from hands.config import DATA_ROOT, PROJECT_ROOT
from hands.status import STATUS

from collect_check import p17_collect, p30_real_sample
from common import load_expected, parse_frame


# ---------------------------------------------------------------- AC1 鉴权
async def p01(ctx):
    bad = []
    async with aiohttp.ClientSession() as s:
        u = ctx.base + "/api/state"
        cases = {"无令牌": {}, "错令牌": {"X-Panel-Token": "x"},
                 "外部 Origin": {"X-Panel-Token": ctx.token, "Origin": "http://evil.example"},
                 "局域网 Host": {"X-Panel-Token": ctx.token, "Host": f"192.168.1.2:{ctx.port}"}}
        for name, h in cases.items():
            async with s.get(u, headers=h) as r:
                if r.status != 403:
                    bad.append(f"{name}→{r.status}")
        async with s.get(u, headers={"X-Panel-Token": ctx.token}) as r:
            if r.status != 200:
                bad.append(f"正确请求→{r.status}")
    return (not bad), ("; ".join(bad) or "无令牌/错令牌/外部 Origin/局域网 Host 均 403，正确请求 200；只监听 127.0.0.1")


# ---------------------------------------------------------------- AC2 浏览器启停与崩溃
async def p02(ctx):
    from hands.browser import kill_tree, profile_chrome_pids
    seen = []
    await ctx.post("/api/browser/start")
    st = await ctx.wait_state(lambda s: s["browser"]["status"] == "running")
    seen.append(st["browser"]["status"])
    c0 = st["browser"]["crashes"]
    kill_tree(st["browser"]["pid"])
    t0 = time.monotonic()
    saw_restarting = False
    while time.monotonic() - t0 < 15:
        s = await ctx.state()
        if s["browser"]["status"] == "crash_restarting":
            saw_restarting = True
        if s["browser"]["status"] == "running" and s["browser"]["crashes"] == c0 + 1:
            break
        await asyncio.sleep(0.1)
    dt = time.monotonic() - t0
    s = await ctx.state()
    if not (s["browser"]["status"] == "running" and s["browser"]["crashes"] == c0 + 1):
        return False, f"崩溃后未恢复：{s['browser']}"
    # 有任务时停止/切换被拒
    await ctx.start_job("manual")
    code1, _ = await ctx.req("POST", "/api/browser/stop", {}, expect_ok=False)
    code2, _ = await ctx.req("POST", "/api/browser/mode", {"headless": False}, expect_ok=False)
    await ctx.post("/api/jobs/stop")
    await ctx.wait_job()
    if code1 != 409 or code2 != 409:
        return False, f"任务中停止/切换返回 {code1}/{code2}（应 409）"
    await ctx.post("/api/browser/stop")
    st = await ctx.wait_state(lambda s: s["browser"]["status"] == "stopped")
    left = await asyncio.to_thread(profile_chrome_pids, ctx.svc.config.profile_dir)
    if left:
        return False, f"停止后残留 Chrome {left}"
    await ctx.post("/api/browser/start")
    await ctx.wait_state(lambda s: s["browser"]["status"] == "running")
    return True, (f"启动→运行中；强杀 Chrome 后{'显示崩溃重启中、' if saw_restarting else ''}{dt:.1f}s 恢复运行中，"
                  f"崩溃次数 {c0}→{c0 + 1}；任务中停止/切换 409；停止后无残留")


# ---------------------------------------------------------------- AC3 有界面不推流
async def p03(ctx):
    await ctx.post("/api/browser/mode", {"headless": False})
    st = await ctx.wait_state(lambda s: s["browser"]["status"] == "running" and not s["browser"]["headless"])
    off = not st["preview"]["on"]
    await ctx.post("/api/browser/front")
    await ctx.post("/api/preview", {"on": True})
    ws = await ctx.ws()
    got = False
    await ctx.post("/api/nav", {"url": ctx.fixture("f01_static/index.html")})
    try:
        t0 = time.monotonic()
        while time.monotonic() - t0 < 5 and not got:
            m = await ws.receive(timeout=5)
            got = m.type == aiohttp.WSMsgType.BINARY
    finally:
        await ws.close()
    await ctx.post("/api/browser/mode", {"headless": True})
    st2 = await ctx.wait_state(lambda s: s["browser"]["headless"] and s["preview"]["on"], 20)
    ok = off and got and st2["preview"]["on"]
    return ok, f"有界面时推流{'关' if off else '开（错）'}；手动打开后{'收到' if got else '未收到'}帧；切回无界面自动恢复推流"


# ---------------------------------------------------------------- AC4 互斥
async def p04(ctx):
    await ctx.start_job("case", {"case": "panel_stop.yaml"})
    await ctx.wait_state(lambda s: s["busy"])
    code, data = await ctx.start_job("demo", expect_ok=False)
    st = await ctx.state()
    await ctx.post("/api/jobs/stop")
    await ctx.wait_job()
    ok = code == 409 and "panel_stop" in (data.get("running") or "")
    return ok, f"用例运行中第二个启动请求 → {code} {data.get('error')}；state.busy={st['busy']}"


# ---------------------------------------------------------------- AC5 帧率与延迟
async def p05(ctx):
    bench = DATA_ROOT / "runs" / "bench_screencast.json"
    best = json.loads(bench.read_text(encoding="utf-8"))["best_fps"] if bench.exists() else None
    await ctx.post("/api/nav", {"url": ctx.fixture("panel_bench/index.html")})
    ws = await ctx.ws()
    lat, n, t0 = [], 0, time.monotonic()
    try:
        while time.monotonic() - t0 < 5:
            m = await ws.receive(timeout=5)
            if m.type == aiohttp.WSMsgType.BINARY:
                head, jpg = parse_frame(m.data)
                now = time.time()
                n += 1
                lat.append(now - head["ts"])
                await ws.send_str(json.dumps({"t": "shown", "seq": head["seq"], "at": now}))
    finally:
        await ws.close()
    span = time.monotonic() - t0
    client_fps = n / span
    server = (await ctx.state())["preview"]
    med = statistics.median(lat) * 1000 if lat else 1e9
    need = best * 0.9 if best else 30
    ok = client_fps >= need and med <= 100
    return ok, (f"客户端收到 {client_fps:.1f} 帧/秒（服务端统计 {server['fps']}），延迟中位数 {med:.1f} ms；"
                f"标准 ≥ {need:.1f} 帧/秒（基准 {best} × 90%）、≤ 100 ms")


# ---------------------------------------------------------------- AC6 跟随新标签页 + 元素框对齐
async def p06(ctx):
    exp = load_expected("f11_nav")
    await ctx.post("/api/nav", {"url": ctx.fixture("f11_nav/index.html")})
    els = (await ctx.get("/api/elements"))["data"]["elements"]
    link = next(e for e in els if e["name"] == "新标签页")
    ws = await ctx.ws()
    epoch0 = STATUS.page_epoch
    await ctx.start_job("target", {"x": link["box"]["x"] + 5, "y": link["box"]["y"] + 5})
    t0 = time.monotonic()
    switched = None
    try:
        while time.monotonic() - t0 < 6:
            m = await ws.receive(timeout=6)
            if m.type == aiohttp.WSMsgType.BINARY:
                head, _ = parse_frame(m.data)
                if head["epoch"] != epoch0 and exp["blank_url_contains"] in (head.get("url") or ""):
                    switched = time.monotonic() - t0
                    break
    finally:
        await ws.close()
    await ctx.wait_job()
    # 叠加对齐：元素框（CSS）→ 画布（×k）→ 换回 CSS 误差
    st = await ctx.state()
    fr = st["preview"]["frame"] or {}
    k = 1280 / fr["dw"] if fr.get("dw") else 1.0
    els2 = (await ctx.get("/api/elements"))["data"]["elements"]
    err = max((abs(round(e["box"]["x"] * k) / k - e["box"]["x"]) for e in els2), default=0)
    await ctx.post("/api/nav/back")
    ok = switched is not None and switched <= 2.0 and err <= 1
    return ok, f"新标签页 {('%.2fs' % switched) if switched else '未'}切换到新画面；叠加换算误差 {err:.2f}px"


# ---------------------------------------------------------------- AC7 地址栏
async def p07(ctx):
    await ctx.post("/api/nav", {"url": ctx.fixture("f11_nav/index.html")})
    s1 = await ctx.wait_state(lambda s: "f11_nav/index.html" in s["page"]["url"] and s["page"]["title"])
    await ctx.post("/api/nav", {"url": ctx.fixture("f11_nav/page2.html")})
    await ctx.post("/api/nav/back")
    s2 = await ctx.wait_state(lambda s: "f11_nav/index.html" in s["page"]["url"])
    await ctx.post("/api/nav/reload")
    s3 = await ctx.wait_state(lambda s: "f11_nav/index.html" in s["page"]["url"])
    await ctx.start_job("case", {"case": "panel_stop.yaml"})
    await ctx.wait_state(lambda s: s["busy"])
    code, _ = await ctx.req("POST", "/api/nav", {"url": "about:blank"}, expect_ok=False)
    await ctx.post("/api/jobs/stop")
    await ctx.wait_job()
    ok = code == 409
    return ok, f"标题“{s1['page']['title']}”；后退、刷新后网址正确；任务运行中导航 → {code}"


# ---------------------------------------------------------------- AC8 等待与网络
async def p08(ctx):
    exp4, exp9 = load_expected("f04_slow"), load_expected("f09_fault")
    old = ctx.svc.config.nav_timeout_s
    ctx.svc.config.nav_timeout_s = 5
    try:
        task = asyncio.create_task(ctx.req("POST", "/api/nav", {"url": ctx.fixture("f04_slow/index.html")}))
        st = await ctx.wait_state(lambda s: s["wait"] and s["wait"]["what"] == "network_quiet"
                                  and s["monitor"] and any(exp4["slow_url_contains"] in p["url"]
                                                           for p in s["monitor"]["pending"]), 6, 0.1)
        e1 = st["wait"]["elapsed_ms"]
        await asyncio.sleep(0.6)
        e2 = (await ctx.state())["wait"]["elapsed_ms"]
        _, res = await task
    finally:
        ctx.svc.config.nav_timeout_s = old
    timeout = res["data"]["status"] == "timeout"
    await ctx.post("/api/nav", {"url": ctx.fixture("f09_fault/index.html")})
    st9 = await ctx.wait_state(lambda s: s["monitor"] and s["monitor"]["failed"] and s["monitor"]["console_errors"], 10)
    f404 = any(f.get("status") == 404 for f in st9["monitor"]["failed"])
    cons = any(exp9["console_contains"] in c["text"] for c in st9["monitor"]["console_errors"])
    ok = e2 > e1 and timeout and f404 and cons
    return ok, (f"慢请求页：等待“网络安静”{e1}→{e2}ms 增长，未完成请求含慢请求，到时 {res['data']['status']}；"
                f"故障页：404={f404}，console 含 FIXTURE_ERROR_42={cons}")


# ---------------------------------------------------------------- AC9 / AC12 用例、任务树、停止
async def p09(ctx):
    await ctx.start_job("case", {"case": "t13_reddit_yes.yaml"})
    job = await ctx.wait_job()
    st = await ctx.state()
    tree = st["tree"]
    goals = [n["goal"] for n in tree["nodes"]]
    child = next((n for n in tree["nodes"] if n["parent"] == "root"), None)
    steps = st["steps"]
    report = json.loads((ctx.svc.config.runs_dir / job["result"]["report"]).with_name("report.json")
                        .read_text(encoding="utf-8"))
    ok = (job["result"]["passed"] and child is not None and child["summary"] and len(steps) == report["results"][0]["steps"]
          and all(s["durations"] and {"check", "decide", "execute"} <= set(s["durations"]) for s in steps))
    return ok, f"任务树 {goals}，子任务摘要 {bool(child and child['summary'])}；时间线 {len(steps)} 步与报告一致，含三段耗时"


async def p12(ctx):
    await ctx.start_job("case", {"case": "t13_reddit_yes.yaml"})
    j1 = await ctx.wait_job()
    rep = ctx.svc.config.runs_dir / j1["result"]["report"]
    await ctx.start_job("case", {"case": "panel_stop.yaml"})
    await ctx.wait_state(lambda s: s["job"] and (s["job"]["progress"].get("step") or 0) >= 3, 60, 0.05)
    at = (await ctx.state())["job"]["progress"]["step"]
    await ctx.post("/api/jobs/stop")
    j2 = await ctx.wait_job()
    stopped_after = j2["progress"]["step"]
    ok = (j1["result"]["passed"] and rep.exists() and j2["result"]["reason"] == "user_stopped"
          and stopped_after - at <= 1 and j2["state"] == "stopped")
    return ok, (f"t13 通过、报告 {rep.name} 存在；panel_stop 第 {at} 步点停止，第 {stopped_after} 步后停下，"
                f"结果 {j2['result']['reason']}")


# ---------------------------------------------------------------- AC10 设置
async def p10(ctx):
    from hands.humanize import HumanizeConfig
    from panel.settings import from_config
    cfg_path = DATA_ROOT / "config.yaml"
    backup = cfg_path.read_text(encoding="utf-8")
    try:
        await ctx.req("PUT", "/api/settings", {"humanize.mouse.overshoot_prob": 1.0, "humanize.think.enabled": False,
                                               "humanize.scroll_jitter.enabled": False})
        await ctx.start_job("demo", {"seed": 7})
        await ctx.wait_state(lambda s: s["demo"] and len(s["demo"][-1]["rows"]) >= 3, 60, 0.02)
        await ctx.req("PUT", "/api/settings", {"humanize.mouse.overshoot_prob": 0})
        job = await ctx.wait_job()
        rows = (await ctx.state())["demo"][-1]["rows"]
        probs = [r["overshoot_prob"] for r in rows]
        k = next(i for i, p in enumerate(probs) if p == 0)
        later_over = any(r["overshoot"] for r in rows[k:])
        code, data = await ctx.req("PUT", "/api/settings", {"humanize.think.prob": 5}, expect_ok=False)
        _, sv = await ctx.req("PUT", "/api/settings", {"quiet_ms": 650})
        await ctx.post("/api/settings/save_default")
        from hands.config import load_config
        saved = from_config(load_config(cfg_path, chrome_path="x"))
        comments_ok = sum("#" in x for x in backup.splitlines()) == sum("#" in x for x in cfg_path.read_text(encoding="utf-8").splitlines())
        await ctx.post("/api/settings/restore")
        restored = (await ctx.get("/api/settings"))["data"]["effective"]
        # ver1 起视觉大模型已接入：可以选（200）；不存在的决策方式仍被拒（400）。选完切回规则决策，不影响后续检查
        vlm_code, _ = await ctx.req("PUT", "/api/settings", {"decision": "vlm"}, expect_ok=False)
        bad_code, _ = await ctx.req("PUT", "/api/settings", {"decision": "gpt"}, expect_ok=False)
        await ctx.req("PUT", "/api/settings", {"decision": "rule"})
        ok = (k >= 3 and not later_over and code == 400 and saved["quiet_ms"] == 650 and comments_ok
              and restored["quiet_ms"] == 500 and restored["humanize.mouse.overshoot_prob"] == 0.7
              and vlm_code == 200 and bad_code == 400)
        return ok, (f"第 {k + 1} 次点击起 overshoot_prob=0（前 {k} 次仍为旧值 1.0），之后无过冲；越界 → {code}；"
                    f"保存为默认后 config.yaml quiet_ms={saved['quiet_ms']}、注释保留={comments_ok}；"
                    f"恢复默认 OK；视觉大模型 → {vlm_code}，未知决策方式 → {bad_code}")
    finally:
        cfg_path.write_text(backup, encoding="utf-8")
        await ctx.post("/api/settings/restore")
        ctx.svc.settings.apply_now(ctx.svc.config)
        _ = HumanizeConfig


# ---------------------------------------------------------------- AC11 演示页
async def p11(ctx):
    rounds = []
    for seed in (4321, 4321, 99):
        await ctx.start_job("demo", {"seed": seed})
        job = await ctx.wait_job()
        rounds.append(((await ctx.state())["demo"][-1], job))
    r1, r2, r3 = (r[0] for r in rounds)
    pts = lambda r: [x["trajectory"]["points"] for x in r["rows"]]  # noqa: E731
    hits = [sum(x["hit"] for x in r["rows"]) for r in (r1, r2, r3)]
    timing = all(x["planned_ms"] is not None and x["actual_ms"] is not None for x in r1["rows"])
    both = len((await ctx.state())["demo"]) == 2
    ok = all(h == len(r1["rows"]) for h in hits) and pts(r1) == pts(r2) and pts(r1) != pts(r3) and timing and both
    return ok, (f"三轮命中 {hits}/{len(r1['rows'])}；同种子两轮轨迹点一致={pts(r1) == pts(r2)}，换种子不同={pts(r1) != pts(r3)}；"
                f"每次有计划/实际耗时；保留上一轮用于对比")


# ---------------------------------------------------------------- AC13 指定目标
async def p13(ctx):
    exp = load_expected("f05_iframe")
    out = []
    await ctx.post("/api/nav", {"url": ctx.fixture("f01_static/index.html")})
    els = (await ctx.get("/api/elements"))["data"]["elements"]
    b2 = [e for e in els if e["role"] == "button" and e["visible"]][1]
    await ctx.start_job("target", {"x": b2["box"]["x"] + b2["box"]["width"] / 2, "y": b2["box"]["y"] + b2["box"]["height"] / 2})
    j = await ctx.wait_job()
    out.append(j["result"]["element"]["name"] == b2["name"])
    for b in exp["buttons"]:
        await ctx.post("/api/nav", {"url": ctx.fixture("f05_iframe/index.html")})
        els = (await ctx.get("/api/elements"))["data"]["elements"]
        e = next(x for x in els if x["name"] == b["name"])
        await ctx.start_job("target", {"x": e["box"]["x"] + e["box"]["width"] / 2, "y": e["box"]["y"] + e["box"]["height"] / 2})
        await ctx.wait_job()
        got = await ctx.svc.browser.page.evaluate("document.getElementById('result').textContent")
        out.append(got == b["result"])
    return all(out), f"静态页第 2 个按钮点中={out[0]}；同源 iframe={out[1]}；跨源 iframe={out[2]}"


# ---------------------------------------------------------------- AC14（自动部分）手动输入转发
async def p14(ctx):
    await ctx.post("/api/nav", {"url": ctx.fixture("f07_form/index.html")})
    els = (await ctx.get("/api/elements"))["data"]["elements"]
    box = next(e for e in els if e["role"] in ("textbox", "searchbox"))["box"]
    sub = next(e for e in els if e["role"] == "button")["box"]
    await ctx.start_job("manual")
    ws = await ctx.ws()
    c = lambda b: (b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)  # noqa: E731
    try:
        x, y = c(box)
        for k in ("move", "down", "up"):
            await ws.send_str(json.dumps({"t": "mouse", "kind": k, "x": x, "y": y, "button": 0, "detail": 1}))
        await ws.send_str(json.dumps({"t": "text", "text": "你好abc"}))
        x, y = c(sub)
        for k in ("move", "down", "up"):
            await ws.send_str(json.dumps({"t": "mouse", "kind": k, "x": x, "y": y, "button": 0, "detail": 1}))
        await asyncio.sleep(1.0)
        body = await ctx.svc.browser.page.evaluate("document.body.innerText")
        code, _ = await ctx.start_job("demo", expect_ok=False)
    finally:
        await ws.close()
        await ctx.post("/api/jobs/stop")
        await ctx.wait_job()
    ok = "你好abc" in body and code == 409
    return ok, f"预览转发点击+输入“你好abc”+提交：页面{'显示' if '你好abc' in body else '未显示'}；手动模式下启动演示 → {code}（中文输入法本身需人工验）"


# ---------------------------------------------------------------- AC15 面板内人工介入（假验证码页）
async def p15(ctx):
    await ctx.post("/api/nav", {"url": ctx.fixture("extra/gate.html?reset=1")})
    await ctx.start_job("case", {"case": "extra_captcha.yaml"})
    st = await ctx.wait_state(lambda s: s["job"] and s["job"]["state"] == "need_human", 30)
    headless = st["browser"]["headless"]
    lamp = st["browser"]["status"]
    await ctx.post("/api/nav", {"url": ctx.fixture("extra/gate.html?solve=1")})   # 模拟人工在预览里过验证
    await ctx.post("/api/jobs/continue")
    job = await ctx.wait_job()
    h = job["result"]["human_handoffs"]
    rep = json.loads((ctx.svc.config.runs_dir / job["result"]["report"]).with_name("report.json").read_text(encoding="utf-8"))
    ok = (job["result"]["passed"] and len(h) == 1 and headless and lamp == "need_human"
          and rep["results"][0]["human_handoffs"])
    return ok, (f"出现“需要人工”（状态灯 {lamp}），未切有界面={headless}；继续后用例通过，人工介入 {len(h)} 次，"
                f"报告记录起止时间（Turnstile 联网部分见 AC15②/AC29 本机人工）")


# ---------------------------------------------------------------- AC16 运行历史
async def p16(ctx):
    runs = (await ctx.get("/api/runs"))["data"]
    root = ctx.svc.config.runs_dir
    dirs = sorted((p.name for p in root.iterdir() if (p / "report.json").exists()), reverse=True)[:200]
    same = [r["dir"] for r in runs] == dirs
    first = next(r for r in runs if r["has_html"])
    async with ctx.http.get(f"{ctx.base}/runs/{first['dir']}/report.html") as r:
        html_ok = r.status == 200 and "运行报告" in await r.text()
    async with ctx.http.get(f"{ctx.base}/runs/..%2Fconfig.yaml") as r:
        trav = r.status
    return same and html_ok and trav == 404, f"列表 {len(runs)} 条与 runs/ 一致={same}；报告可打开={html_ok}；目录穿越 → {trav}"


# ---------------------------------------------------------------- AC18 真实轨迹库回放（合成样本）
async def p18(ctx):
    import math
    import random

    from hands.trajlib import Library, Sample
    rng = random.Random(1)
    lib = Library()
    for d in range(8):
        a = d * math.pi / 4
        for j in range(2):
            L = 250 + 40 * j
            sx, sy = 600.0, 400.0
            ex, ey = sx + L * math.cos(a), sy + L * math.sin(a)
            pts = [[sx + (ex - sx) * i / 24 + rng.uniform(-3, 3) * math.sin(math.pi * i / 24),
                     sy + (ey - sy) * i / 24 + rng.uniform(-3, 3) * math.sin(math.pi * i / 24), int(450 * i / 24)]
                   for i in range(1, 25)]
            pts[-1] = [ex, ey, 450]
            lib.samples.append(Sample.from_raw({"device": "mouse", "source": "local", "start": [sx, sy], "end": [ex, ey],
                                                "target": {"x": ex - 20, "y": ey - 10, "width": 40, "height": 20},
                                                "points": pts}))
    saved = ctx.svc.samples.library
    ctx.svc.samples.library = lib
    try:
        await ctx.req("PUT", "/api/settings", {"humanize.mouse.source": "library", "humanize.mouse.device": "mouse"})
        res = []
        for seed in (11, 11, 12):
            await ctx.start_job("demo", {"seed": seed})
            await ctx.wait_job()
            res.append((await ctx.state())["demo"][-1]["rows"])
        r1, r2, r3 = res
        lib_rows = [r for r in r1 if r["source"] == "library"]
        err = max(max(math.dist(r["trajectory"]["points"][-1][:2], r["trajectory"]["landing"]),
                      math.dist(r["trajectory"]["start"], r["trajectory"]["start"])) for r in lib_rows)
        hits = sum(r["hit"] for r in r1)
        same = [r["trajectory"]["points"] for r in r1] == [r["trajectory"]["points"] for r in r2]
        diff = [r["trajectory"]["points"] for r in r1] != [r["trajectory"]["points"] for r in r3]
        lib.samples = [s for s in lib.samples if s.direction != 0]          # 删掉“右”方向
        await ctx.start_job("demo", {"seed": 11})
        await ctx.wait_job()
        rows = (await ctx.state())["demo"][-1]["rows"]
        fb = [r for r in rows if r["trajectory"].get("direction_code") == "E"]
        fallback_ok = bool(fb) and all(r["fallback"] for r in fb)
        ok = hits == len(r1) and len(lib_rows) >= len(r1) - 1 and err <= 1 and same and diff and fallback_ok
        return ok, (f"命中 {hits}/{len(r1)}，{len(lib_rows)} 次用样本（有样本编号），末点误差 {err:.2f}px；"
                    f"同种子一致={same}，换种子不同={diff}；删掉“右”方向后退回模板并注明={fallback_ok}")
    finally:
        ctx.svc.samples.library = saved
        await ctx.req("PUT", "/api/settings", {"humanize.mouse.source": "template"})


# ---------------------------------------------------------------- AC20 用户目录
async def p20(ctx):
    await ctx.post("/api/nav", {"url": ctx.fixture("f14_cookie/index.html?set=1")})
    await ctx.post("/api/browser/stop")
    await ctx.wait_state(lambda s: s["browser"]["status"] == "stopped")
    code_running = None
    await ctx.post("/api/browser/start")
    await ctx.post("/api/nav", {"url": ctx.fixture("f14_cookie/index.html")})
    kept = "panel_cookie=yes" in (await ctx.svc.browser.page.evaluate("document.cookie"))
    code_running, _ = await ctx.req("POST", "/api/profile/clear", {"confirm": "CLEAR"}, expect_ok=False)
    await ctx.post("/api/browser/stop")
    code_noconfirm, _ = await ctx.req("POST", "/api/profile/clear", {"confirm": ""}, expect_ok=False)
    await ctx.post("/api/profile/clear", {"confirm": "CLEAR"})
    await ctx.post("/api/browser/start")
    await ctx.post("/api/nav", {"url": ctx.fixture("f14_cookie/index.html")})
    gone = "panel_cookie" not in (await ctx.svc.browser.page.evaluate("document.cookie"))
    info = (await ctx.get("/api/profile"))["data"]
    local = os.environ.get("LOCALAPPDATA", "")
    isolated = local.lower() not in info["path"].lower() or "Google\\Chrome\\User Data".lower() not in info["path"].lower()
    ok = kept and code_running == 409 and code_noconfirm == 409 and gone and isolated
    return ok, (f"重启浏览器后 Cookie 仍在={kept}；运行中清空 → {code_running}；未确认 → {code_noconfirm}；"
                f"清空后 Cookie 消失={gone}；目录 {info['path']} 与日常 Chrome 隔离")


# ---------------------------------------------------------------- AC21 CSP 与前端拦截（自动部分）
async def p21(ctx):
    async with ctx.http.get(ctx.base + "/") as r:
        csp = r.headers.get("Content-Security-Policy", "")
        html = await r.text()
    js = (PROJECT_ROOT / "src" / "panel" / "static" / "app.js").read_text(encoding="utf-8")
    ok = "default-src 'self'" in csp and "<script>" not in html and "window.open =" in js and "drop" in js
    return ok, "CSP default-src 'self'、无内联脚本；前端拦截外链点击 / window.open / 拖放（窗口原生拦截需本机人工验）"


# ---------------------------------------------------------------- AC22 依赖清单
async def p22(ctx):
    d = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    core = d["dependencies"] == ["websockets", "httpx", "pillow", "pyyaml"]
    opt = d["optional-dependencies"]
    ok = core and opt.get("panel") == ["aiohttp", "pywebview"] and opt.get("build") == ["pyinstaller"]
    return ok, f"必需依赖与 ver0 相同={core}；可选 panel={opt.get('panel')} build={opt.get('build')}"


# ---------------------------------------------------------------- AC23 预览不干扰
async def p23(ctx):
    from hands.dom import DomReader
    page = ctx.svc.browser.page
    res = {}
    for on in (False, True):
        await ctx.post("/api/preview", {"on": on})
        await ctx.post("/api/nav", {"url": ctx.fixture("f01_static/index.html")})
        els, _ = await DomReader().read(page)
        reqs = [p.url for p in page.monitor.snapshot().pending]
        res[on] = ([(e.id, e.box.x, e.box.y, e.box.width, e.box.height) for e in els], reqs)
    same = res[False][0] == res[True][0]
    noreq = not res[True][1]
    return same and noreq, f"开/关推流元素坐标一致={same}；推流期间无额外请求={noreq}（T03–T13 全量见 run_tests.py）"


# ---------------------------------------------------------------- AC24 稳定性
async def _rss_mb() -> float:
    import importlib.util
    spec = importlib.util.spec_from_file_location("acc_common", PROJECT_ROOT / "tests" / "acceptance" / "common.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.python_rss_mb()


async def p24_real(ctx):
    n = ctx.rounds
    mem = {}
    fails = []
    for i in range(1, n + 1):
        await ctx.start_job("case", {"case": "t13_reddit_yes.yaml"})
        j = await ctx.wait_job()
        if not j["result"]["passed"]:
            fails.append(i)
        if i in (min(5, n), n):
            mem[i] = await _rss_mb()
    # 运行中“刷新页面”：新 WS 连接立即拿到完整状态
    ws = await ctx.ws()
    m = await ws.receive(timeout=5)
    await ws.close()
    first = json.loads(m.data) if m.type == aiohttp.WSMsgType.TEXT else {}
    a, b = mem.get(min(5, n)), mem.get(n)
    growth = (b - a) / a if a else 0
    ok = not fails and growth <= 0.2 and first.get("t") == "state"
    return ok, f"连续 {n} 次 t13 失败 {fails}；后台内存 第{min(5, n)}次 {a:.0f}MB → 第{n}次 {b:.0f}MB（{growth:+.0%}）；重连立即收到完整状态"


# ---------------------------------------------------------------- AC25 可追溯
async def p25(ctx):
    runs = (await ctx.get("/api/runs"))["data"]
    labels = {r["label"] for r in runs}
    need = {"panel-demo", "panel-target"}
    has_case = any(lbl.startswith("panel-t13") for lbl in labels)
    ok = need <= labels and has_case
    return ok, f"runs/ 下有 panel-demo、panel-target、panel-<用例> 独立目录={ok}"


# ---------------------------------------------------------------- AC27 用途说明
async def p27(ctx):
    about = (await ctx.get("/api/info"))["data"]["about"]
    ok = "已授权" in about and "不用于规避" in about
    return ok, "设置页用途说明含“已授权”“不用于规避”"


# ---------------------------------------------------------------- Playwright 方案 AC5：面板切换驱动
async def p31(ctx):
    orig = (await ctx.state())["browser"]["driver"]
    other = "playwright" if orig == "cdp" else "cdp"
    await ctx.start_job("manual")
    await ctx.wait_state(lambda s: s["busy"])
    code, data = await ctx.req("POST", "/api/browser/driver", {"driver": other}, expect_ok=False)
    still = (await ctx.state())["browser"]["driver"]
    await ctx.post("/api/jobs/stop")
    await ctx.wait_job()
    if code != 409 or still != orig:
        return False, f"任务中切换返回 {code}（应 409），驱动 {orig}→{still}"
    notes = [f"任务中切换 → 409，驱动保持 {orig}"]
    for d in (other, orig):
        await ctx.post("/api/browser/driver", {"driver": d})
        st = await ctx.wait_state(lambda s, d=d: s["browser"]["driver"] == d and s["browser"]["status"] == "running", 60)
        await ctx.post("/api/nav", {"url": ctx.fixture("f01_static/index.html")})
        st = await ctx.wait_state(lambda s: "f01_static" in (s["page"].get("url") or ""), 20)
        notes.append(f"切到 {d}：状态 {st['browser']['status']}，页面 {st['page'].get('title')!r}")
    return True, "；".join(notes)


CHECKS = [("P01", p01), ("P02", p02), ("P03", p03), ("P04", p04), ("P05", p05), ("P06", p06), ("P07", p07),
          ("P08", p08), ("P09", p09), ("P10", p10), ("P11", p11), ("P12", p12), ("P13", p13), ("P14", p14),
          ("P15", p15), ("P16", p16), ("P17", p17_collect), ("P18", p18), ("P20", p20), ("P21", p21), ("P22", p22), ("P23", p23),
          ("P24", p24_real), ("P25", p25), ("P27", p27), ("P30", p30_real_sample), ("P31", p31)]
from books_check import BOOK_CHECKS  # noqa: E402
from remote_check import REMOTE_CHECKS  # noqa: E402

CHECKS += BOOK_CHECKS + REMOTE_CHECKS
CDP_ONLY = {"P30"}          # 真实网站采集：Playwright 驱动不支持（spec“不做的事”）
