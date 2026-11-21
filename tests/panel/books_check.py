"""面板验收：任务书（B 系列）。替身 4b 只选编号，夹具站点由面板自动起。"""
from __future__ import annotations

import asyncio
import shutil
import sys

from hands.config import DATA_ROOT, PROJECT_ROOT

sys.path.insert(0, str(PROJECT_ROOT / "tests" / "acceptance"))
LEGACY = PROJECT_ROOT / "tests" / "unit" / "data" / "legacy"
TMP_NAMES = ("zz_b04", "zz_b02")


def stub_client():
    from dcommon import BookStub
    from hands.vlm.client import FuncClient
    return FuncClient(BookStub())


def _cleanup():
    d = DATA_ROOT / "tasks"
    for n in TMP_NAMES:
        (d / f"{n}.yaml").unlink(missing_ok=True)
        for p in (d / ".bak").glob(f"{n}.*.yaml") if (d / ".bak").exists() else []:
            p.unlink()


async def b01(ctx):
    rows = (await ctx.get("/api/books"))["data"]
    names = {r["name"]: r for r in rows}
    want = {"dinner", "dinner_yelp", "fixture_dinner", "fixture_transformers", "transformers"}
    g = (await ctx.get("/api/books/transformers"))["data"]
    blocks = (await ctx.get("/api/books/blocks"))["data"]["blocks"]
    ok = want <= set(names) and all(names[n]["status"] == "ok" for n in want) and g["book"]["start"] == "optimus" \
        and len(blocks) == 12
    return ok, f"{len(rows)} 份任务书，积木 {len(blocks)} 个，transformers 状态 {g['status']}"


async def b02(ctx):
    bad = "format: taskbook/1\nname: x\ngroups:\n  - a: [b]\nstart: a\nflow:\n  - pick_list: {max: 99}\n  - fly: {}\n"
    ps = (await ctx.post("/api/books/validate", {"text": bad}))["data"]["problems"]
    got = {(p["step"], p["block"], p["param"]) for p in ps}
    want = {("flow[0]", "pick_list", "region"), ("flow[0]", "pick_list", "max"), ("flow[1]", "fly", None)}
    return want <= got, f"{len(ps)} 条问题：" + "；".join(f"{p['step']}/{p['block']}/{p['param']}：{p['why']}" for p in ps[:4])


async def b03(ctx):
    text = (LEGACY / "dinner.yaml").read_text(encoding="utf-8")
    r = (await ctx.post("/api/books/import", {"text": text}))["data"]
    import aiohttp
    form = aiohttp.FormData()
    form.add_field("file", text.encode("utf-8"), filename="dinner.yaml", content_type="application/x-yaml")
    async with ctx.http.post(ctx.base + "/api/books/import", data=form) as resp:
        r2 = (await resp.json())["data"]
    st, run = await ctx.req("POST", "/api/books/run", {"text": r["text"]}, expect_ok=False)
    ok = r["legacy"] and r["status"] == "needs_rewrite" and r2["status"] == "needs_rewrite" and st == 400 \
        and "改写" in str(run)
    return ok, f"粘贴 / 上传都转换为 needs_rewrite；补全前启动 → {st}"


async def b04(ctx):
    _cleanup()
    try:
        src = (await ctx.get("/api/books/fixture_dinner"))["data"]["text"]
        r1 = (await ctx.post("/api/books/save", {"name": "zz_b04", "text": src, "save_as": True}))["data"]
        t2 = src.replace("今晚吃什么（夹具）", "改过一次")
        r2 = (await ctx.post("/api/books/save", {"name": "zz_b04", "text": t2, "rev": r1["rev"]}))["data"]
        st, d = await ctx.req("POST", "/api/books/save", {"name": "zz_b04", "text": src, "rev": r1["rev"]},
                              expect_ok=False)
        bak = list((DATA_ROOT / "tasks" / ".bak").glob("zz_b04.*.yaml"))
        st2, _ = await ctx.req("POST", "/api/books/save", {"name": "zz_b04", "text": src, "save_as": True},
                               expect_ok=False)
        ok = st == 409 and "另一端" in d.get("error", "") and len(bak) == 1 and st2 == 409 and r2["rev"] != r1["rev"]
        return ok, f"旧 rev 保存 → {st} {d.get('error')}；备份 {len(bak)} 份；另存为同名 → {st2}"
    finally:
        _cleanup()


async def b05(ctx):
    text = (await ctx.get("/api/books/fixture_transformers"))["data"]["text"]
    r = (await ctx.post("/api/books/preview", {"text": text}))["data"]
    labels = [x["label"] for x in r["order"]]
    ok = labels[:3] == ["optimus", "optimus/galvatron", "optimus/megatron"] and "movie/autobot" in labels
    return ok, " → ".join(labels[:8]) + " …"


async def _run_book(ctx, body):
    ctx.svc.jobs.book_client = stub_client()
    await ctx.post("/api/books/run", body)
    return await ctx.wait_state(lambda s: s["busy"], 20)


async def b06(ctx):
    await _run_book(ctx, {"name": "fixture_transformers", "trial": True})
    job = await ctx.wait_job(180)
    run = job["result"]["run"]
    steps = (await ctx.get(f"/api/books/steps?run={run}"))["data"]
    shots_ok = all(any(b.get("shot_urls") for b in s["blocks"]) for s in steps)
    first_shot = next(u for s in steps for b in s["blocks"] for u in b.get("shot_urls", []))
    async with ctx.http.get(ctx.base + first_shot) as r:
        shot_status = r.status
    flt = next(b for b in steps[0]["blocks"] if b["block"] == "filter")
    ok = len(steps) == 5 and shots_ok and shot_status == 200 and job["result"]["trial"] and "trial" in run \
        and any(d["reason"] == "别名归一后是当前词" for d in flt.get("dropped", []))
    return ok, f"试跑 {len(steps)} 次消耗，截图 {shot_status}，运行目录 {run}"


async def b07(ctx):
    await _run_book(ctx, {"name": "fixture_transformers", "budget": 3})
    try:
        await ctx.wait_state(lambda s: (s.get("job") or {}).get("progress", {}).get("state") == "waiting", 120)
        snap = (await ctx.get("/api/expand/tree"))["data"]
        rev = snap["rev"]
        st, d = await ctx.req("POST", "/api/expand/edit", {"op": "grant", "n": 1, "rev": rev - 1}, expect_ok=False)
        st2, _ = await ctx.req("POST", "/api/expand/edit", {"op": "finish", "rev": rev}, expect_ok=False)
        await ctx.wait_job(60)
        ok = st == 409 and "另一端" in d.get("error", "") and st2 == 200
        return ok, f"旧 rev → {st} {d.get('error')}；当前 rev → {st2}"
    finally:
        if (await ctx.state())["busy"]:
            await ctx.post("/api/jobs/stop")
            await ctx.wait_job(60)


BOOK_CHECKS = [("B01", b01), ("B02", b02), ("B03", b03), ("B04", b04), ("B05", b05), ("B06", b06), ("B07", b07)]
