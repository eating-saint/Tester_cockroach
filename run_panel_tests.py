"""面板验收表：python run_panel_tests.py [--only P05,P11] [--rounds 20]

进程内启动面板后台（随机端口），用 REST / WebSocket 驱动，输出 编号 / 通过 / 耗时 / 说明 表，
写入 runs/<时间>-panel-acceptance/panel_acceptance.json。需要人亲手做的项见 docs/panel/checklist.md。
注意：和 run_tests.py 一样使用 profiles/ver0，不能同时运行。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tests" / "panel"))


def width(s: str) -> int:
    return sum(2 if ord(c) > 0x2E80 else 1 for c in s)


def pad(s: str, w: int) -> str:
    return s + " " * max(0, w - width(s))


async def main(args) -> int:
    from checks import CDP_ONLY, CHECKS
    from common import PanelCtx

    only = {x.strip().upper() for x in args.only.split(",")} if args.only else None
    ctx = PanelCtx(driver=args.driver)
    ctx.rounds = args.rounds
    await ctx.start()
    driver = ctx.svc.config.driver
    print(f"驱动: {driver}", flush=True)
    rows = []
    try:
        await ctx.post("/api/browser/start")
        await ctx.wait_state(lambda s: s["browser"]["status"] == "running")
        ctx.svc.ensure_fixtures()
        for pid, fn in CHECKS:
            if only and pid not in only:
                continue
            if driver != "cdp" and pid in CDP_ONLY:
                rows.append({"id": pid, "passed": None, "seconds": 0, "reason": f"不适用（{driver} 驱动不支持）"})
                print(f"[{pid}] 不适用", flush=True)
                continue
            print(f"[{pid}] 运行中……", flush=True)
            t0 = time.monotonic()
            try:
                ok, reason = await fn(ctx)
            except Exception as e:
                ok, reason = False, f"{type(e).__name__}: {e}"
                traceback.print_exc()
            # 每项之后确保没有遗留任务、浏览器在跑
            try:
                if (await ctx.state())["busy"]:
                    await ctx.post("/api/jobs/stop")
                    await ctx.wait_job()
                if not ctx.svc.browser_alive():
                    await ctx.post("/api/browser/start")
            except Exception:
                pass
            dt = time.monotonic() - t0
            rows.append({"id": pid, "passed": bool(ok), "seconds": round(dt, 2), "reason": reason})
            print(f"[{pid}] {'通过' if ok else '失败'} {dt:.1f}s  {reason}", flush=True)
    finally:
        await ctx.close()
    print()
    print(pad("编号", 6) + pad("结果", 6) + pad("耗时(s)", 9) + "说明 / 失败原因")
    print("-" * 100)
    for r in rows:
        res = "不适用" if r["passed"] is None else ("通过" if r["passed"] else "失败")
        print(pad(r["id"], 6) + pad(res, 6) + pad(f"{r['seconds']:.1f}", 9) + r["reason"])
    counted = [r for r in rows if r["passed"] is not None]
    passed = sum(bool(r["passed"]) for r in counted)
    print("-" * 100)
    print(f"驱动 {driver}    通过 {passed}/{len(counted)}    产物目录: {ctx.run_root}")
    (ctx.run_root / "panel_acceptance.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if counted and passed == len(counted) else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--rounds", type=int, default=20, help="P24 连续运行次数")
    ap.add_argument("--driver", choices=["cdp", "playwright"], default=None, help="浏览器驱动，默认取 config.yaml")
    sys.exit(asyncio.run(main(ap.parse_args())))
