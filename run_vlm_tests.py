"""ver1 一键验收：python run_vlm_tests.py [--only V01,V06] [--driver cdp|playwright] [--headed]

验收模块 tests/acceptance/vNN_*.py 的 NN 与 spec 的 AC 编号相同（V06 = AC6）。
模块里 NEEDS_MODEL = True 的项需要本机模型服务；服务不可用时记为"跳过"，不算通过。
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
    for p in sorted((ROOT / "tests" / "acceptance").glob("v[0-9][0-9]_*.py")):
        vid = "V" + re.match(r"v(\d\d)_", p.name).group(1)
        if only is None or vid in only:
            mods.append((vid, p.stem))
    return mods


def width(s: str) -> int:
    return sum(2 if ord(c) > 0x2E80 else 1 for c in s)


def pad(s: str, w: int) -> str:
    return s + " " * max(0, w - width(s))


async def main(args) -> int:
    from common import Ctx
    from vcommon import model_ok

    only = {t.strip().upper() for t in args.only.split(",")} if args.only else None
    try:
        ctx = Ctx(headed=args.headed, driver=args.driver)
    except Exception as e:
        print(e, file=sys.stderr)
        return 2
    ok, info = await model_ok(ctx)
    print(f"驱动: {ctx.config.driver}    模型服务: {'可用 ' + info if ok else '不可用（' + info + '）'}", flush=True)
    rows = []
    try:
        for vid, mod in discover(only):
            m = importlib.import_module(mod)
            if getattr(m, "NEEDS_MODEL", True) and not ok:
                rows.append({"id": vid, "passed": False, "skipped": True, "seconds": 0, "reason": "跳过：模型服务不可用"})
                print(f"[{vid}] 跳过：模型服务不可用", flush=True)
                continue
            print(f"[{vid}] 运行中……", flush=True)
            t0 = time.monotonic()
            try:
                passed, reason = await m.run(ctx)
            except Exception as e:
                passed, reason = False, f"{type(e).__name__}: {e}"
                traceback.print_exc()
            dt = time.monotonic() - t0
            rows.append({"id": vid, "passed": passed, "seconds": round(dt, 1), "reason": reason})
            print(f"[{vid}] {'通过' if passed else '失败'} {dt:.1f}s  {reason}", flush=True)
    finally:
        await ctx.close()
    print()
    print(pad("编号", 6) + pad("结果", 6) + pad("耗时(s)", 9) + "说明 / 失败原因")
    print("-" * 100)
    for r in rows:
        res = "跳过" if r.get("skipped") else ("通过" if r["passed"] else "失败")
        print(pad(r["id"], 6) + pad(res, 6) + pad(f"{r['seconds']:.1f}", 9) + r["reason"])
    passed = sum(r["passed"] for r in rows)
    print("-" * 100)
    print(f"驱动 {ctx.config.driver}    通过 {passed}/{len(rows)}    产物目录: {ctx.run_root}")
    (ctx.run_root / "vlm_acceptance.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if rows and passed == len(rows) else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    ap.add_argument("--driver", default=None)
    ap.add_argument("--headed", action="store_true")
    sys.exit(asyncio.run(main(ap.parse_args())))
