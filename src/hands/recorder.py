"""M10 日志与报告：每次运行一个独立目录，记录每一步并汇总报告。"""
from __future__ import annotations

import html
import json
import random
import string
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class StepRecord:
    index: int
    node_id: str
    page_state_file: str
    screenshot_file: str | None
    decision_raw: list[str]
    decision: dict | None
    retries: int
    action_result: dict
    durations_ms: dict = field(default_factory=dict)
    target: dict | None = None        # ver1：被操作元素的稳定特征（以后固化成脚本回放用）

    def to_dict(self) -> dict:
        return asdict(self)


def _dump(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


class RunRecorder:
    def __init__(self, runs_dir, label: str = ""):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
        name = f"{stamp}-{suffix}" + (f"-{label}" if label else "")
        self.root = Path(runs_dir) / name
        self.root.mkdir(parents=True, exist_ok=False)
        self.steps: dict[str, list[StepRecord]] = {}

    def case_dir(self, case: str) -> Path:
        d = self.root / case
        d.mkdir(parents=True, exist_ok=True)
        return d

    def step_dir(self, case: str, index: int) -> Path:
        d = self.case_dir(case) / f"step_{index:03d}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def reset_case(self, case: str):
        """崩溃重跑时清空该用例已记录的步骤列表（旧文件保留在 attempt 子目录中）。"""
        self.steps[case] = []

    def save_step(self, case: str, rec: StepRecord, page_state=None, trajectory_png: bytes | None = None,
                  dpr: float = 1.0):
        d = self.step_dir(case, rec.index)
        if page_state is not None:
            page_state.to_json(d / "pagestate.json")
        traj = (rec.action_result or {}).get("trajectory")
        if traj is not None:
            _dump(d / "trajectory.json", traj)
            if trajectory_png:                 # 无头模式看不到光标：把轨迹画到鼠标到位时的截图上
                from .screenshot import draw_trajectory
                (d / "trajectory.png").write_bytes(draw_trajectory(trajectory_png, traj, dpr))
        _dump(d / "decision.json", {"raw": rec.decision_raw, "decision": rec.decision, "retries": rec.retries})
        action = {"result": rec.action_result, "durations_ms": rec.durations_ms}
        if rec.target is not None:
            action["target"] = rec.target
        _dump(d / "action.json", action)
        _dump(d / "step.json", rec.to_dict())
        self.steps.setdefault(case, []).append(rec)

    def save_tree(self, case: str, tree):
        _dump(self.case_dir(case) / "tree_events.json", {"events": tree.events_json(), "shape": tree.shape()})

    def save_case_result(self, case: str, result):
        _dump(self.case_dir(case) / "result.json", result.to_dict())

    def write_report(self, results, humanize: dict | None = None) -> Path:
        _dump(self.root / "report.json", {"run": self.root.name, "humanize": humanize,
                                          "results": [r.to_dict() for r in results]})
        e = html.escape
        rows, details = [], []
        for r in results:
            rows.append(f"<tr class={'ok' if r.passed else 'bad'}><td>{e(r.name)}</td>"
                        f"<td>{'通过' if r.passed else '失败'}</td><td>{e(r.reason or '')}</td>"
                        f"<td>{r.crashes}</td><td>{r.steps}</td><td>{r.duration_ms / 1000:.1f}s</td></tr>")
            for h in getattr(r, "human_handoffs", None) or []:
                fmt = lambda t: time.strftime("%H:%M:%S", time.localtime(t))  # noqa: E731
                rows.append(f"<tr><td></td><td colspan=5>人工介入：{fmt(h['start'])} – {fmt(h['end'])}"
                            f"（{h['end'] - h['start']:.1f}s），命中标记 {e(str(h.get('hit')))}</td></tr>")
            if getattr(r, "decision", "rule") == "vlm":
                rows.append("<tr><td></td><td colspan=5>决策方式：视觉大模型</td></tr>")
            ex = getattr(r, "extractions", None) or []
            if ex:
                cells = "；".join(f"{x['field']} = {x['value']}（{'有依据' if x['grounded'] else '无依据'}）" for x in ex)
                rows.append(f"<tr><td></td><td colspan=5>抽取：{e(cells)}</td></tr>")
            items = []
            for s in self.steps.get(r.name, []):
                d = s.decision or {}
                ar = s.action_result or {}
                shot = f"{r.name}/step_{s.index:03d}/{s.screenshot_file}" if s.screenshot_file else ""
                link = '<a href="%s">截图</a>' % e(shot) if shot else ""
                tdir = self.root / r.name / f"step_{s.index:03d}"
                if (tdir / "trajectory.png").exists():
                    link += ' <a href="%s">轨迹</a>' % e(f"{r.name}/step_{s.index:03d}/trajectory.png")
                if (tdir / "vlm" / "annotated.png").exists():
                    link += ' <a href="%s">标注图</a>' % e(f"{r.name}/step_{s.index:03d}/vlm/annotated.png")
                if (tdir / "vlm" / "response.json").exists():
                    link += ' <a href="%s">模型回答</a>' % e(f"{r.name}/step_{s.index:03d}/vlm/response.json")
                dist = ar.get("disturbances") or []
                if dist:
                    link += " " + e("、".join("思考 %dms" % x["ms"] if x["kind"] == "think"
                                             else "滚轮乱动(回滚误差 %spx)" % x["residual_px"] for x in dist))
                items.append(
                    f"<tr><td>{s.index}</td><td>{e(s.node_id)}</td><td>{e(d.get('tool', '-'))}</td>"
                    f"<td><code>{e(json.dumps(d.get('arguments', {}), ensure_ascii=False))}</code></td>"
                    f"<td>{'是' if ar.get('effective') else '否'}</td><td>{e(str(ar.get('error') or ''))}</td>"
                    f"<td>{s.durations_ms.get('total', 0)} ms</td>"
                    f"<td>{link}</td></tr>")
            details.append(f"<h3>{e(r.name)}</h3><table><tr><th>#</th><th>节点</th><th>工具</th><th>参数</th>"
                           f"<th>生效</th><th>错误</th><th>耗时</th><th>截图</th></tr>{''.join(items)}</table>")
        seed_line = ""
        if humanize:
            seed_line = ("<p>鼠标轨迹/干扰项已开启，运行种子 <code>%s</code>"
                         "（各用例种子见 report.json；用同一种子可重放）</p>" % humanize["run_seed"])
        page = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>运行报告 {e(self.root.name)}</title>
<style>body{{font:14px sans-serif;margin:24px}} table{{border-collapse:collapse;margin-bottom:16px}}
td,th{{border:1px solid #ccc;padding:4px 8px;text-align:left}} tr.ok td:nth-child(2){{color:#080}}
tr.bad td:nth-child(2){{color:#c00}} code{{font-size:12px}}</style></head><body>
<h1>运行报告 {e(self.root.name)}</h1>
<p>通过 {sum(r.passed for r in results)} / {len(results)}</p>
{seed_line}
<table><tr><th>用例</th><th>结果</th><th>原因</th><th>崩溃次数</th><th>步数</th><th>耗时</th></tr>{''.join(rows)}</table>
{''.join(details)}</body></html>"""
        out = self.root / "report.html"
        out.write_text(page, encoding="utf-8")
        return out
