"""T16 M10 报告：每条用例有通过/失败、每一步的 PageState 和决策 JSON、截图、耗时；运行目录互不覆盖。"""
import json

from PIL import Image

from hands.recorder import RunRecorder
from hands.runner import load_case
from hands.config import PROJECT_ROOT


async def run(ctx):
    from hands.runner import CaseRunner

    b = await ctx.get_browser()
    cases = [load_case(PROJECT_ROOT / "cases" / f, {"BASE": ctx.base}) for f in ("t16_report.yaml", "t13_reddit_yes.yaml")]
    rec = RunRecorder(ctx.run_root / "cases", label="t16")
    runner = CaseRunner(b, ctx.config, rec, log=lambda *a: None)
    results = await runner.run_cases(cases)
    problems = []
    for r, c in zip(results, cases):
        cd = rec.case_dir(c.name)
        res = json.loads((cd / "result.json").read_text(encoding="utf-8"))
        for k in ("passed", "reason", "duration_ms"):
            if k not in res:
                problems.append(f"{c.name} result.json 缺 {k}")
        steps = sorted(cd.glob("step_*"))
        if len(steps) != r.steps or not steps:
            problems.append(f"{c.name} 步骤目录数 {len(steps)} ≠ {r.steps}")
        for sd in steps:
            ps = json.loads((sd / "pagestate.json").read_text(encoding="utf-8"))
            if "elements" not in ps:
                problems.append(f"{sd} pagestate 无 elements")
            dec = json.loads((sd / "decision.json").read_text(encoding="utf-8"))
            if not dec.get("decision"):
                problems.append(f"{sd} 无决策")
            act = json.loads((sd / "action.json").read_text(encoding="utf-8"))
            if "total" not in act.get("durations_ms", {}):
                problems.append(f"{sd} 无耗时")
            Image.open(sd / "screenshot.png").verify()
    if not (rec.root / "report.json").exists() or not (rec.root / "report.html").exists():
        problems.append("缺少 report.json / report.html")
    html = (rec.root / "report.html").read_text(encoding="utf-8")
    if not all(c.name in html for c in cases):
        problems.append("report.html 未列出全部用例")
    # AC20：再跑一次，生成新目录，旧目录不变
    before = {p: p.stat().st_mtime for p in rec.root.rglob("*") if p.is_file()}
    rec2 = RunRecorder(ctx.run_root / "cases", label="t16")
    await CaseRunner(b, ctx.config, rec2, log=lambda *a: None).run_cases(cases[:1])
    if rec2.root == rec.root:
        problems.append("第二次运行未生成新目录")
    after = {p: p.stat().st_mtime for p in rec.root.rglob("*") if p.is_file()}
    if before != after:
        problems.append("第二次运行修改了第一次运行的文件")
    if problems:
        return False, "; ".join(problems[:5])
    return True, f"2 条用例记录完整（{sum(r.steps for r in results)} 步）；两次运行目录 {rec.root.name} / {rec2.root.name} 互不覆盖"
