"""python -m hands run <case.yaml...> [--headed] [--config config.yaml]
                      [--humanize | --humanize-only mouse,think,scroll_jitter] [--seed N]
                      [--driver cdp|playwright|desktop] [--decision rule|vlm] [--confirm on|off]
python -m hands expand <任务类型.yaml> [--param k=v ...] [--budget N] [--driver ...]"""
from __future__ import annotations

import argparse
import asyncio
import queue
import sys
import threading
from pathlib import Path

from .config import DECISIONS, DRIVERS, PROJECT_ROOT, load_config
from .driver import DriverUnavailable, create_browser
from .humanize import apply_cli
from .recorder import RunRecorder
from .runner import CaseRunner, load_case

FIXTURE_PORT = 8765
EOF_IDLE_S = 3.0                             # 没有控制台时：人停手几秒自动恢复


def vlm_model(cfg) -> str:
    from .config import vlm_settings
    return str(vlm_settings(cfg)["model"])


class ConsoleLines:
    """桌面驱动的命令行输入：一个后台线程读标准输入，暂停恢复、需确认、人工介入共用，互不抢行。"""

    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.waiters = 0
        self.eof = False                     # 标准输入已结束（后台运行、没有控制台）
        threading.Thread(target=self._run, name="hands-stdin", daemon=True).start()

    def _run(self):
        try:
            for line in sys.stdin:
                self.q.put(line.rstrip("\r\n"))
        except (OSError, ValueError):
            pass
        self.eof = True
        self.q.put(None)

    def input(self, prompt: str = "") -> str:
        if prompt:
            print(prompt, end="", flush=True)
        self.waiters += 1
        try:
            v = self.q.get()
        finally:
            self.waiters -= 1
        if v is None:
            self.q.put(None)
            raise EOFError
        return v

    def poll(self):
        try:
            v = self.q.get_nowait()
        except queue.Empty:
            return False, None
        if v is None:
            self.q.put(None)
        return True, v


async def cli_pause_watch(gate, lines: ConsoleLines, grace: float, stop: dict):
    """暂停（人工输入 / 用户）时提示：回车恢复，q 取消。停手自动恢复由暂停门自己处理。"""
    reasons = {"human_input": "检测到人工操作鼠标键盘", "user": "用户暂停"}
    shown = None
    while True:
        await asyncio.sleep(0.2)
        p = gate.paused
        if p is None or p.reason not in reasons or lines.waiters:
            shown = None
            continue
        if shown is not p:
            if lines.eof:
                print(f"\n[已暂停] {reasons[p.reason]}。没有可交互的控制台：停手 {EOF_IDLE_S:.0f} 秒后自动恢复", flush=True)
            else:
                print(f"\n[已暂停] {reasons[p.reason]}。回车恢复，输入 q 取消任务", flush=True)
            shown = p
        if lines.eof:
            # 后台运行（标准输入已结束）：不能当成"取消"；人停手一会儿就自动恢复
            import time as _t
            if _t.monotonic() - gate.last_human >= EOF_IDLE_S:
                gate.resume("idle")
                gate.arm(grace)
            continue
        got, line = lines.poll()
        if not got:
            continue
        if line is None:
            continue                         # 刚读到输入结束：下一轮按上面的自动恢复处理
        if line.strip().lower() == "q":
            stop["v"] = True
        gate.resume("cli")
        gate.arm(grace)


def _desktop_cli(cfg, browser):
    """桌面驱动命令行附件：(input_fn, should_stop, 监听任务协程或 None)。"""
    from .config import desktop_settings
    from .driver import caps_of
    stop = {"v": False}
    if not caps_of(browser).os_input:
        return input, (lambda: False), None, stop
    lines = ConsoleLines()
    grace = float(desktop_settings(cfg)["resume_grace_s"])
    return lines.input, (lambda: stop["v"]), cli_pause_watch(browser.gate, lines, grace, stop), stop


async def run(args) -> int:
    try:
        cfg = load_config(args.config, headless=False if args.headed else None, driver=args.driver,
                          decision=args.decision)
        if args.confirm is not None:
            cfg.confirm = {**cfg.confirm, "enabled": args.confirm == "on"}
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
    try:
        await browser.start()
    except DriverUnavailable as e:
        print(e, file=sys.stderr)
        if server:
            server.stop()
        return 2
    input_fn, should_stop, watch, _ = _desktop_cli(cfg, browser)
    watch_task = asyncio.ensure_future(watch) if watch is not None else None
    try:
        runner = CaseRunner(browser, cfg, RunRecorder(cfg.runs_dir, args.label), input_fn=input_fn,
                            should_stop=should_stop)
        results = await runner.run_cases(cases)
    finally:
        if watch_task is not None:
            watch_task.cancel()
        await browser.stop()
        if server:
            server.stop()
    for r in results:
        print(f"{'通过' if r.passed else '失败'}  {r.name}  steps={r.steps} crashes={r.crashes}  {r.reason}")
    print(f"报告: {runner.report}")
    return 0 if all(r.passed for r in results) else 1


def print_tree(tree):
    def walk(nid, depth):
        n = tree.nodes[nid]
        if n.state == "deleted":
            return
        mark = {"pending": "待查", "key": "键", "tentative": "暂定", "terminal": "叶子"}.get(n.state, n.state)
        print(f"{'  ' * depth}- {n.word} [{mark}] ({n.id})")
        for c in n.children:
            walk(c, depth + 1)
    for r in tree.roots:
        if r["node"] and tree.nodes[r["node"]].parent is None:
            walk(r["node"], 0)
    print(tree.summary_text())


async def expand_menu(runner, lines: ConsoleLines, grace: float, stop: dict):
    """等待决策（次数用满）时的命令行菜单：回车看树、+N 再给 N 次、f 结束并输出。"""
    gate, tree = runner.gate, runner.tree
    shown = False
    while True:
        await asyncio.sleep(0.2)
        if runner.state != "waiting":
            shown = False
            continue
        if not shown:
            print(f"\n[等待决策] 已用 {tree.used} / {tree.budget} 次。回车看树，+N 再给 N 次，f 结束并输出", flush=True)
            shown = True
        got, line = lines.poll()
        if not got:
            continue
        s = (line or "").strip().lower()
        if line is None or s == "f":
            tree.finish("user")
            gate.resume("cli")
        elif s.startswith("+") and s[1:].isdigit():
            tree.grant(int(s[1:]))
            gate.resume("cli")
            gate.arm(grace)
        else:
            print_tree(tree)
            shown = False


async def expand(args) -> int:
    from .config import desktop_settings
    from .driver import caps_of
    from .expand.run import ExpandRunner
    from .expand.spec import TaskTypeInvalid, load_task_type
    try:
        cfg = load_config(args.config, headless=False if args.headed else None, driver=args.driver,
                          decision=args.decision)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    if cfg.decision != "vlm":
        print("关联展开任务只能用大模型决策（加 --decision vlm，或在 config.yaml 写 decision: vlm）", file=sys.stderr)
        return 2
    params = {}
    for p in args.param or []:
        k, _, v = p.partition("=")
        params[k.strip()] = v.strip()
    server = None
    text = Path(args.type).read_text(encoding="utf-8")
    if "${BASE}" in text:
        sys.path.insert(0, str(PROJECT_ROOT / "fixtures"))
        import server as fixture_server
        server = fixture_server.start(FIXTURE_PORT)
        params.setdefault("BASE", f"http://127.0.0.1:{FIXTURE_PORT}")
    try:
        tt = load_task_type(args.type, params)
    except TaskTypeInvalid as e:
        print(f"任务类型定义不对：{e}", file=sys.stderr)
        if server:
            server.stop()
        return 2
    if args.budget:
        tt.budget = int(args.budget)
    from .vlm.client import ModelUnavailable, OllamaClient
    from .config import vlm_settings
    try:
        await OllamaClient(vlm_settings(cfg)).check()
        browser = create_browser(cfg)
        await browser.start()
    except (ModelUnavailable, DriverUnavailable) as e:
        print(f"{e}", file=sys.stderr)
        if server:
            server.stop()
        return 2
    print(f"关联展开：{tt.title}    驱动: {browser.driver}    模型: {vlm_model(cfg)}    次数上限: {tt.budget}")
    lines = ConsoleLines()
    stop = {"v": False}
    grace = float(desktop_settings(cfg)["resume_grace_s"]) if caps_of(browser).os_input else 0.0
    runner = ExpandRunner(browser, cfg, RunRecorder(cfg.runs_dir, args.label or f"expand-{tt.name}"), tt,
                          input_fn=lines.input, should_stop=lambda: stop["v"])
    tasks = [asyncio.ensure_future(expand_menu(runner, lines, grace, stop))]
    if caps_of(browser).os_input:
        tasks.append(asyncio.ensure_future(cli_pause_watch(browser.gate, lines, grace, stop)))
    try:
        res = await runner.run()
    finally:
        for t in tasks:
            t.cancel()
        await browser.stop()
        if server:
            server.stop()
    print_tree(runner.tree)
    print(f"末端叶子 {len(res['leaves'])} 个；结果：{runner.dir / 'result.json'}")
    for g, rows in (res.get("rank") or {}).get("top", {}).items():
        print(f"【{g or '结果'}】")
        for i, r in enumerate(rows, 1):
            print(f"  {i}. {r['name']}  价位 {r.get('price_text') or '未知'}  评分 {r.get('rating_text') or '-'}  {r.get('url') or ''}")
    return 0


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
    r.add_argument("--confirm", choices=("on", "off"), default=None,
                   help="需确认（加购、收藏、提交表单等先问）：默认桌面驱动开、其他驱动关")
    x = sub.add_parser("expand", help="关联展开任务（tasks/*.yaml）")
    x.add_argument("type", help="任务类型定义文件，如 tasks/dinner.yaml")
    x.add_argument("--param", action="append", help="参数，如 --param location=Irvine --param n=3")
    x.add_argument("--budget", type=int, default=None, help="次数上限（覆盖定义里的 budget）")
    x.add_argument("--headed", action="store_true")
    x.add_argument("--config", default="config.yaml")
    x.add_argument("--label", default="")
    x.add_argument("--driver", choices=DRIVERS, default=None)
    x.add_argument("--decision", choices=DECISIONS, default=None)
    args = ap.parse_args()
    sys.exit(asyncio.run(run(args) if args.cmd == "run" else expand(args)))


if __name__ == "__main__":
    main()
