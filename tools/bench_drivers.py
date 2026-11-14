"""两种驱动的性能对比（Playwright 方案 F10 / AC7）：python tools/bench_drivers.py [--rounds 20] [--t17 RUN_DIR_CDP RUN_DIR_PW]

逐个驱动测：启动耗时（start + new_page，10 次中位数）、读取页面耗时（F1、F10 上 build_page_state，中位数）、
一次点击耗时（F1 上 ActionExecutor.click：含等待 / 不含等待）、推流帧率（面板会话 Page.startScreencast，
在 panel_bench 动画页上计 10 秒帧数）。T17 50 轮的总耗时与内存从 run_tests.py 的产物目录读取（--t17 给两个目录）。
结果写入 docs/playwright/perf.md。使用 profiles/ver0，不能和其他测试同时运行。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "fixtures"))

import server as fixture_server  # noqa: E402

from hands.actions import ActionExecutor  # noqa: E402
from hands.config import load_config  # noqa: E402
from hands.dom import DomReader  # noqa: E402
from hands.driver import create_browser  # noqa: E402
from hands.pagestate import build_page_state  # noqa: E402

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"


def med(xs):
    return round(statistics.median(xs), 1) if xs else None


async def bench(driver: str, rounds: int) -> dict:
    cfg = load_config(driver=driver)
    out: dict = {}
    starts = []
    for _ in range(10):
        b = create_browser(cfg)
        t = time.perf_counter()
        await b.start()
        await b.new_page()
        starts.append((time.perf_counter() - t) * 1000)
        await b.stop()
    out["start_ms"] = med(starts)

    b = create_browser(cfg)
    await b.start()
    page = await b.new_page()
    try:
        for name, path in (("f01", "f01_static/index.html"), ("f10", "f10_search/with_reddit.html")):
            await page.navigate(f"{BASE}/{path}")
            ts = []
            for _ in range(rounds):
                t = time.perf_counter()
                await build_page_state(page)
                ts.append((time.perf_counter() - t) * 1000)
            out[f"pagestate_{name}_ms"] = med(ts)
            els, _ = await DomReader().read(page)
            out[f"elements_{name}"] = len(els)

        await page.navigate(f"{BASE}/f01_static/index.html")
        ax = ActionExecutor(page)
        total, bare = [], []
        for _ in range(rounds):
            els, _ = await DomReader().read(page)
            e = next(x for x in els if x.role == "button" and x.visible)
            t = time.perf_counter()
            r = await ax.click(e)
            total.append((time.perf_counter() - t) * 1000)
            bare.append((time.perf_counter() - t) * 1000 - (r.wait.elapsed_ms if r.wait else 0))
        out["click_total_ms"] = med(total)
        out["click_without_wait_ms"] = med(bare)

        await page.navigate(f"{BASE}/panel_bench/index.html")       # 与面板推流基准（P05）同一页面
        sess = await page.panel_session()
        frames = []

        def on_frame(p):
            frames.append(time.monotonic())
            asyncio.ensure_future(sess.send("Page.screencastFrameAck", {"sessionId": p["sessionId"]}))

        sess.on("Page.screencastFrame", on_frame)
        await sess.send("Page.startScreencast", {"format": "jpeg", "quality": 70, "maxWidth": 1280,
                                                 "maxHeight": 800, "everyNthFrame": 1})
        t0 = time.monotonic()
        await asyncio.sleep(10)                         # 页面自带动画，每个动画帧都改变画面
        await sess.send("Page.stopScreencast")
        sess.off("Page.screencastFrame", on_frame)
        out["screencast_fps"] = round(sum(1 for f in frames if f - t0 <= 10) / 10, 1)
    finally:
        await b.stop()
    return out


def read_t17(run_dir: str | None) -> str:
    if not run_dir:
        return "—"
    p = Path(run_dir) / "acceptance.json"
    if not p.exists():
        return "—"
    rows = {r["id"]: r for r in json.loads(p.read_text(encoding="utf-8"))}
    r = rows.get("T17")
    return f"{r['seconds']:.0f}s；{r['reason']}" if r else "—"


async def main(args):
    srv = fixture_server.start(PORT)
    try:
        res = {d: await bench(d, args.rounds) for d in ("cdp", "playwright")}
    finally:
        srv.stop()
    rows = [("浏览器启动（start + new_page，10 次中位数）", "start_ms", "ms"),
            ("读取页面 F1（build_page_state 中位数）", "pagestate_f01_ms", "ms"),
            ("读取页面 F10（build_page_state 中位数）", "pagestate_f10_ms", "ms"),
            ("F1 元素数", "elements_f01", ""), ("F10 元素数", "elements_f10", ""),
            ("一次点击（含等待网络安静）", "click_total_ms", "ms"),
            ("一次点击（不含等待）", "click_without_wait_ms", "ms"),
            ("推流帧率（panel_bench 动画页，10 秒）", "screencast_fps", "帧/秒")]
    cfg = load_config()
    from importlib.metadata import version
    lines = ["# 两种驱动性能对比", "",
             f"生成时间：{time.strftime('%Y-%m-%d %H:%M')}　机器：{platform.processor() or platform.machine()}，"
             f"{platform.platform()}　Chrome：{cfg.chrome_path}　Playwright：{version('playwright')}",
             f"每项读取 / 点击重复 {args.rounds} 次取中位数。命令：`python tools/bench_drivers.py`", "",
             "| 项目 | CDP | Playwright |", "|---|---|---|"]
    for label, key, unit in rows:
        c, p = res["cdp"].get(key), res["playwright"].get(key)
        lines.append(f"| {label} | {c} {unit} | {p} {unit} |")
    lines.append(f"| T17 50 轮（总耗时；通过率与内存） | {read_t17(args.t17[0] if args.t17 else None)} | "
                 f"{read_t17(args.t17[1] if args.t17 else None)} |")
    lines.append("")
    out = ROOT / "docs" / "playwright" / "perf.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n已写入 {out}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=20)
    ap.add_argument("--t17", nargs=2, default=None, metavar=("CDP_RUN_DIR", "PW_RUN_DIR"))
    asyncio.run(main(ap.parse_args()))
