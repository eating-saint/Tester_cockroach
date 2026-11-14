"""一键验收：python run_tests.py [--only T03,T05] [--rounds 50] [--headed] [--humanize] [--seed N]
                               [--driver cdp|playwright]

依次运行 tests/acceptance/tNN_*.py（T01–T17、T21–T23），输出 编号 / 通过或失败 / 耗时 / 失败原因 表。
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import re
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tests" / "acceptance"))


def discover(only: set[str] | None):
    mods = []
    for p in sorted((ROOT / "tests" / "acceptance").glob("t[0-9][0-9]_*.py")):
        tid = "T" + re.match(r"t(\d\d)_", p.name).group(1)
        if only is None or tid in only:
            mods.append((tid, p.stem))
    return mods


def width(s: str) -> int:
    return sum(2 if ord(c) > 0x2E80 else 1 for c in s)


def pad(s: str, w: int) -> str:
    return s + " " * max(0, w - width(s))


async def main(args) -> int:
    from common import Ctx

    only = {t.strip().upper() for t in args.only.split(",")} if args.only else None
    try:
        ctx = Ctx(headed=args.headed, rounds=args.rounds, humanize=args.humanize, seed=args.seed, driver=args.driver)
    except Exception as e:                       # 例如选了 Playwright 但未安装
        print(e, file=sys.stderr)
        return 2
    print(f"驱动: {ctx.config.driver}", flush=True)
    rows = []
    try:
        for tid, mod in discover(only):
            print(f"[{tid}] 运行中……", flush=True)
            t0 = time.monotonic()
            try:
                ok, reason = await importlib.import_module(mod).run(ctx)
            except Exception as e:
                ok, reason = False, f"{type(e).__name__}: {e}"
                traceback.print_exc()
            dt = time.monotonic() - t0
            rows.append({"id": tid, "passed": ok, "seconds": round(dt, 2), "reason": reason})
            print(f"[{tid}] {'通过' if ok else '失败'} {dt:.1f}s  {reason}", flush=True)
    finally:
        await ctx.close()
    print()
    print(pad("编号", 6) + pad("结果", 6) + pad("耗时(s)", 9) + "说明 / 失败原因")
    print("-" * 100)
    for r in rows:
        print(pad(r["id"], 6) + pad("通过" if r["passed"] else "失败", 6) + pad(f"{r['seconds']:.1f}", 9) + r["reason"])
    passed = sum(r["passed"] for r in rows)
    print("-" * 100)
    print(f"驱动 {ctx.config.driver}    通过 {passed}/{len(rows)}    产物目录: {ctx.run_root}")
    (ctx.run_root / "acceptance.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if passed == len(rows) and rows else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--rounds", type=int, default=50)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--humanize", action="store_true", help="整轮验收都打开鼠标轨迹和干扰项")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--driver", choices=["cdp", "playwright"], default=None, help="浏览器驱动，默认取 config.yaml")
    sys.exit(asyncio.run(main(ap.parse_args())))
