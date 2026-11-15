"""ver1 验收公共设施：用指定决策方式（可换替身模型）跑用例、读记录、检查模型服务。"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from hands.config import PROJECT_ROOT, vlm_settings
from hands.recorder import RunRecorder
from hands.runner import CaseRunner, load_case


def cfg_with(ctx, decision="vlm", vlm=None, guard=None, driver=None):
    c = dataclasses.replace(ctx.config)
    c.decision = decision
    c.vlm = {**(ctx.config.vlm or {}), **(vlm or {})}
    c.guard = dict(guard if guard is not None else (ctx.config.guard or {}))
    if driver:
        c.driver = driver
    return c


async def run_case(ctx, case_file: str, decision="vlm", factory=None, vlm=None, guard=None, browser=None,
                   label=None):
    """返回 (CaseResult, RunRecorder, 用例目录)。"""
    b = browser or await ctx.get_browser()
    cfg = cfg_with(ctx, decision, vlm, guard)
    case = load_case(PROJECT_ROOT / "cases" / case_file, {"BASE": ctx.base, "XBASE": ctx.xbase})
    rec = RunRecorder(ctx.run_root / "cases", label=label or f"{decision}-{case.name}")
    runner = CaseRunner(b, cfg, rec, decider_factory=factory, log=lambda *a: None)
    res = await runner.run_case(case)
    rec.write_report([res])
    return res, rec, rec.root / case.name


def scripted(cfg, replies):
    from hands.vlm.client import ScriptedClient
    from hands.vlm.decider import VlmDecider
    client = ScriptedClient(list(replies))
    return VlmDecider.factory(cfg, client), client


def steps(case_dir: Path) -> list[Path]:
    return sorted(Path(case_dir).glob("step_*"))


def jload(p: Path):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def decision_of(step_dir: Path) -> dict:
    return (jload(step_dir / "decision.json") or {}).get("decision") or {}


def action_of(step_dir: Path) -> dict:
    return jload(step_dir / "action.json")


async def model_ok(ctx, model: str | None = None) -> tuple[bool, str]:
    from hands.vlm.client import ModelUnavailable, OllamaClient
    s = vlm_settings(ctx.config)
    if model:
        s["model"] = model
    try:
        await OllamaClient(s).check()
        return True, s["model"]
    except ModelUnavailable as e:
        return False, str(e)


DESC_OK = json.dumps({"regions": [{"position": "中部", "kind": "其他", "note": "替身描述"}], "overlay": False})


def say(tool, reason="替身", **args):
    return json.dumps({"reason": reason, "tool": tool, "arguments": args}, ensure_ascii=False)
