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

    def save_step(self, case: str, rec: StepRecord, page_state=None):
        d = self.step_dir(case, rec.index)
        if page_state is not None:
            page_state.to_json(d / "pagestate.json")
        _dump(d / "decision.json", {"raw": rec.decision_raw, "decision": rec.decision, "retries": rec.retries})
        _dump(d / "action.json", {"result": rec.action_result, "durations_ms": rec.durations_ms})
        _dump(d / "step.json", rec.to_dict())
        self.steps.setdefault(case, []).append(rec)

    def save_tree(self, case: str, tree):
        _dump(self.case_dir(case) / "tree_events.json", {"events": tree.events_json(), "shape": tree.shape()})

    def save_case_result(self, case: str, result):
        _dump(self.case_dir(case) / "result.json", result.to_dict())

    def write_report(self, results) -> Path:
        _dump(self.root / "report.json", {"run": self.root.name, "results": [r.to_dict() for r in results]})
        e = html.escape
        rows, details = [], []
        for r in results:
            rows.append(f"<tr class={'ok' if r.passed else 'bad'}><td>{e(r.name)}</td>"
                        f"<td>{'通过' if r.passed else '失败'}</td><td>{e(r.reason or '')}</td>"
                        f"<td>{r.crashes}</td><td>{r.steps}</td><td>{r.duration_ms / 1000:.1f}s</td></tr>")
            items = []
            for s in self.steps.get(r.name, []):
                d = s.decision or {}
                ar = s.action_result or {}
                shot = f"{r.name}/step_{s.index:03d}/{s.screenshot_file}" if s.screenshot_file else ""
                link = '<a href="%s">截图</a>' % e(shot) if shot else ""
                items.append(
                    f"<tr><td>{s.index}</td><td>{e(s.node_id)}</td><td>{e(d.get('tool', '-'))}</td>"
                    f"<td><code>{e(json.dumps(d.get('arguments', {}), ensure_ascii=False))}</code></td>"
                    f"<td>{'是' if ar.get('effective') else '否'}</td><td>{e(str(ar.get('error') or ''))}</td>"
                    f"<td>{s.durations_ms.get('total', 0)} ms</td>"
                    f"<td>{link}</td></tr>")
            details.append(f"<h3>{e(r.name)}</h3><table><tr><th>#</th><th>节点</th><th>工具</th><th>参数</th>"
                           f"<th>生效</th><th>错误</th><th>耗时</th><th>截图</th></tr>{''.join(items)}</table>")
        page = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>运行报告 {e(self.root.name)}</title>
<style>body{{font:14px sans-serif;margin:24px}} table{{border-collapse:collapse;margin-bottom:16px}}
td,th{{border:1px solid #ccc;padding:4px 8px;text-align:left}} tr.ok td:nth-child(2){{color:#080}}
tr.bad td:nth-child(2){{color:#c00}} code{{font-size:12px}}</style></head><body>
<h1>运行报告 {e(self.root.name)}</h1>
<p>通过 {sum(r.passed for r in results)} / {len(results)}</p>
<table><tr><th>用例</th><th>结果</th><th>原因</th><th>崩溃次数</th><th>步数</th><th>耗时</th></tr>{''.join(rows)}</table>
{''.join(details)}</body></html>"""
        out = self.root / "report.html"
        out.write_text(page, encoding="utf-8")
        return out
