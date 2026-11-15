"""决策方式对比（ver1 F10、AC10）：同一批用例分别用规则决策和大模型决策跑，输出对比表 docs/ver1/compare.md。

用法：python tools/compare_deciders.py [--pairs v01r_shop_rule:v01_shop_extract,t13_reddit_yes,t16_report]
       [--repeat 1] [--driver cdp|playwright] [--model qwen3.5:4b] [--append]
pairs：逗号分隔；"规则版:大模型版" 表示两种决策用不同的用例文件（同一起点、同一目标），只写一个名字表示两种都用它。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "fixtures"))

from hands.config import load_config, vlm_settings  # noqa: E402
from hands.driver import create_browser  # noqa: E402
from hands.recorder import RunRecorder  # noqa: E402
from hands.runner import CaseRunner, load_case  # noqa: E402

DEFAULT_PAIRS = "v01r_shop_rule:v01_shop_extract,t13_reddit_yes,t16_report"
BASE = "http://127.0.0.1:8765"


class VramSampler:
    """每 0.5 秒采一次显存占用（nvidia-smi），取峰值；没有 nvidia-smi 时返回 None。"""

    def __init__(self):
        self.peak = None
        self._task = None
        self.ok = shutil.which("nvidia-smi") is not None

    def _read(self):
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=5).stdout
            return max(int(x) for x in out.split() if x.strip().isdigit())
        except Exception:
            return None

    async def _loop(self):
        while True:
            v = await asyncio.to_thread(self._read)
            if v is not None:
                self.peak = v if self.peak is None else max(self.peak, v)
            await asyncio.sleep(0.5)        # 采样间隔

    def start(self):
        self.peak = None
        if self.ok:
            self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        return self.peak


def step_times(case_dir: Path) -> list[dict]:
    rows = []
    for sd in sorted(case_dir.glob("step_*")):
        a = json.loads((sd / "action.json").read_text(encoding="utf-8"))
        dur = a.get("durations_ms", {})
        desc = dec = 0
        v = sd / "vlm"
        if (v / "describe.json").exists():
            desc = sum(c.get("elapsed_ms", 0) for c in json.loads((v / "describe.json").read_text(encoding="utf-8")).get("calls", []))
        for r in v.glob("response*.json"):
            dec += json.loads(r.read_text(encoding="utf-8")).get("elapsed_ms", 0)
        rows.append({"check": dur.get("check", 0), "decide": dur.get("decide", 0), "execute": dur.get("execute", 0),
                     "describe_model": desc, "decide_model": dec})
    return rows


def med(xs):
    return int(statistics.median(xs)) if xs else 0


async def run_mode(decision: str, cases: list[str], repeat: int, args, sampler: VramSampler) -> dict:
    cfg = load_config(driver=args.driver, decision=decision)
    if args.model:
        cfg.vlm = {**cfg.vlm, "model": args.model}
    if decision == "rule":                 # 先把模型移出显存，规则决策那一列才是不含模型的占用
        from hands.vlm.client import OllamaClient
        await OllamaClient(vlm_settings(cfg)).unload()
    browser = create_browser(cfg)
    await browser.start()
    results, steps = [], []
    sampler.start()
    try:
        for name in cases:
            for k in range(repeat):
                case = load_case(ROOT / "cases" / f"{name}.yaml", {"BASE": BASE, "XBASE": "http://localhost:8765"})
                rec = RunRecorder(cfg.runs_dir, f"compare-{decision}")
                res = await CaseRunner(browser, cfg, rec, log=lambda *a: None).run_case(case)
                rec.write_report([res])
                st = step_times(rec.root / case.name)
                steps += st
                results.append({"case": name, "passed": res.passed, "steps": res.steps, "reason": res.reason,
                                "run": rec.root.name})
                print(f"[{decision}] {name} #{k + 1}: {'通过' if res.passed else '失败'} {res.steps} 步 {res.reason}",
                      flush=True)
    finally:
        peak = await sampler.stop()
        await browser.stop()
    model = vlm_settings(cfg)["model"] if decision == "vlm" else "-"
    return {
        "decision": decision, "model": model, "results": results,
        "success": sum(r["passed"] for r in results) / len(results) if results else 0,
        "avg_steps": statistics.mean(r["steps"] for r in results) if results else 0,
        "describe_ms": med([s["describe_model"] for s in steps if s["describe_model"]]),
        "decide_model_ms": med([s["decide_model"] for s in steps if s["decide_model"]]),
        "decide_ms": med([s["decide"] for s in steps]),
        "execute_ms": med([s["execute"] for s in steps]),
        "model_ms": med([s["describe_model"] + s["decide_model"] for s in steps if s["decide_model"]]),
        "vram_peak_mb": peak,
    }


async def compare(args) -> Path:
    """跑两种决策并写 compare.md；夹具服务（8765）由调用方负责开着。"""
    pairs = [p.split(":") if ":" in p else [p, p] for p in args.pairs.split(",") if p.strip()]
    sampler = VramSampler()
    rule = await run_mode("rule", [p[0] for p in pairs], args.repeat, args, sampler)
    vlm = await run_mode("vlm", [p[1] for p in pairs], args.repeat, args, sampler)
    f = lambda v, unit="": "-" if v in (None, 0) and unit != "%" else (f"{v * 100:.0f}%" if unit == "%" else f"{v}{unit}")  # noqa: E731
    lines = [] if args.append else ["# 决策方式对比（AC10）", ""]
    lines += [
        f"## {time.strftime('%Y-%m-%d %H:%M')}　驱动 {args.driver or 'cdp'}　大模型 `{vlm['model']}`　每个用例 {args.repeat} 次", "",
        f"用例：{'、'.join(':'.join(p) if p[0] != p[1] else p[0] for p in pairs)}（规则版:大模型版）", "",
        "| 指标 | 规则决策 | 大模型决策 |", "|---|---|---|",
        f"| 任务成功率 | {f(rule['success'], '%')} | {f(vlm['success'], '%')} |",
        f"| 平均步数 | {rule['avg_steps']:.1f} | {vlm['avg_steps']:.1f} |",
        f"| 每步决策耗时中位数 | {rule['decide_ms']} ms | {vlm['decide_ms']} ms |",
        f"| └ 描述调用中位数（只算重新描述的步） | - | {f(vlm['describe_ms'], ' ms')} |",
        f"| └ 决策调用中位数 | - | {f(vlm['decide_model_ms'], ' ms')} |",
        f"| 每步模型耗时中位数（描述 + 决策；N5 参考目标 ≤ 20 秒） | - | {f(vlm['model_ms'], ' ms')} |",
        f"| 每步动作与等待中位数 | {rule['execute_ms']} ms | {vlm['execute_ms']} ms |",
        f"| 显存峰值（整卡已用；规则决策前已卸载模型） | {f(rule['vram_peak_mb'], ' MB')} | {f(vlm['vram_peak_mb'], ' MB')} |", "",
        "说明：t13、t16 等 ver0 用例的通过标准（摘要关键词、任务树形状）是按规则决策写的，大模型决策在这些用例上的失败如实列出。", "",
        "| 决策方式 | 用例 | 结果 | 步数 | 原因 | 运行目录 |", "|---|---|---|---|---|---|",
    ]
    for m in (rule, vlm):
        for r in m["results"]:
            lines.append(f"| {m['decision']} | {r['case']} | {'通过' if r['passed'] else '失败'} | {r['steps']} | "
                         f"{(r['reason'] or '').replace('|', '/')[:80]} | `{r['run']}` |")
    lines.append("")
    out = ROOT / "docs" / "ver1" / "compare.md"
    text = "\n".join(lines)
    if args.append and out.exists():
        text = out.read_text(encoding="utf-8").rstrip() + "\n\n" + text
    out.write_text(text + "\n", encoding="utf-8")
    print(f"已写入 {out}")
    return out


async def main(args) -> int:
    import server as fixture_server
    srv = fixture_server.start(8765)
    try:
        await compare(args)
    finally:
        srv.stop()
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default=DEFAULT_PAIRS)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--driver", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--append", action="store_true", help="追加到已有 compare.md 后面（换模型对比时用）")
    sys.exit(asyncio.run(main(ap.parse_args())))
