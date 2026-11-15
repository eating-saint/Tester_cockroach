"""python -m hands run <case.yaml...> [--headed] [--config config.yaml]
                      [--humanize | --humanize-only mouse,think,scroll_jitter] [--seed N]
                      [--driver cdp|playwright] [--decision rule|vlm]"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .config import DECISIONS, DRIVERS, PROJECT_ROOT, load_config
from .driver import DriverUnavailable, create_browser
from .humanize import apply_cli
from .recorder import RunRecorder
from .runner import CaseRunner, load_case

FIXTURE_PORT = 8765


def vlm_model(cfg) -> str:
    from .config import vlm_settings
    return str(vlm_settings(cfg)["model"])


async def run(args) -> int:
    try:
        cfg = load_config(args.config, headless=False if args.headed else None, driver=args.driver,
                          decision=args.decision)
        browser = create_browser(cfg)
    except (DriverUnavailable, ValueError) as e:
        print(e, file=sys.stderr)
        return 2
    if cfg.decision == "vlm":                    # 启动浏览器之前先确认模型服务可用（ver1 F1）
        from .vlm.client import ModelUnavailable, OllamaClient
        from .config import vlm_settings
        try:
            await OllamaClient(vlm_settings(cfg)).check()
        except ModelUnavailable as e:
            print(f"[模型不可用] {e}", file=sys.stderr)
            return 2
    cfg.humanize = apply_cli(cfg.humanize, args.humanize, args.seed, args.humanize_only)
    print(f"驱动: {browser.driver}    决策方式: {'视觉大模型 ' + vlm_model(cfg) if cfg.decision == 'vlm' else '规则决策'}")
    texts = [Path(p).read_text(encoding="utf-8") for p in args.cases]
    server = None
    if any("${BASE}" in t or "${XBASE}" in t for t in texts):
        sys.path.insert(0, str(PROJECT_ROOT / "fixtures"))
        import server as fixture_server
        server = fixture_server.start(FIXTURE_PORT)
    vars = {"BASE": f"http://127.0.0.1:{FIXTURE_PORT}", "XBASE": f"http://localhost:{FIXTURE_PORT}"}
    cases = [load_case(p, vars) for p in args.cases]
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
    r.add_argument("--driver", choices=DRIVERS, default=None, help="浏览器驱动，覆盖 config.yaml 的 driver")
    r.add_argument("--decision", choices=DECISIONS, default=None,
                   help="决策方式：rule = 规则决策；vlm = 视觉大模型（需本机 Ollama），覆盖 config.yaml 的 decision")
    args = ap.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
