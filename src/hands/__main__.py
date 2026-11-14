"""python -m hands run <case.yaml...> [--headed] [--config config.yaml]
                      [--humanize | --humanize-only mouse,think,scroll_jitter] [--seed N]"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .browser import BrowserManager
from .config import PROJECT_ROOT, load_config
from .humanize import apply_cli
from .recorder import RunRecorder
from .runner import CaseRunner, load_case

FIXTURE_PORT = 8765


async def run(args) -> int:
    cfg = load_config(args.config, headless=False if args.headed else None)
    cfg.humanize = apply_cli(cfg.humanize, args.humanize, args.seed, args.humanize_only)
    texts = [Path(p).read_text(encoding="utf-8") for p in args.cases]
    server = None
    if any("${BASE}" in t or "${XBASE}" in t for t in texts):
        sys.path.insert(0, str(PROJECT_ROOT / "fixtures"))
        import server as fixture_server
        server = fixture_server.start(FIXTURE_PORT)
    vars = {"BASE": f"http://127.0.0.1:{FIXTURE_PORT}", "XBASE": f"http://localhost:{FIXTURE_PORT}"}
    cases = [load_case(p, vars) for p in args.cases]
    browser = BrowserManager(cfg)
    await browser.start()
    try:
        runner = CaseRunner(browser, cfg, RunRecorder(cfg.runs_dir, args.label))
        results = await runner.run_cases(cases)
    finally:
        await browser.stop()
        if server:
            server.stop()
    for r in results:
        print(f"{'通过' if r.passed else '失败'}  {r.name}  steps={r.steps} crashes={r.crashes}  {r.reason}")
    print(f"报告: {runner.report}")
    return 0 if all(r.passed for r in results) else 1


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="hands")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="运行用例")
    r.add_argument("cases", nargs="+")
    r.add_argument("--headed", action="store_true")
    r.add_argument("--config", default="config.yaml")
    r.add_argument("--label", default="")
    r.add_argument("--humanize", action="store_true", help="打开鼠标轨迹、思考时间、滚轮乱动三项")
    r.add_argument("--humanize-only", default=None, help="只打开部分，例如 mouse 或 mouse,think")
    r.add_argument("--seed", type=int, default=None, help="固定随机种子，用于重放")
    args = ap.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
