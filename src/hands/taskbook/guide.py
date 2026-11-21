"""指导提示词：模板 + 从积木注册表生成的积木说明 + 两份夹具任务书原文。与程序支持的格式保持一致。"""
from __future__ import annotations

from pathlib import Path

from ..config import DATA_ROOT, PROJECT_ROOT
from ..vlm.prompts import builtin_dir
from .blocks import REGISTRY

EXAMPLES = ("fixture_transformers", "fixture_dinner")
TYPE_ZH = {"text": "文字", "int": "整数", "float": "数字", "bool": "true / false", "list": "列表", "map": "对照表",
           "choice": "选项", "listref": "清单名（引用）", "out": "清单名（产出）", "flow": "积木列表"}
ACTOR_ZH = {"program": "程序", "4b": "4b 只回编号，程序取原文"}


def blocks_text() -> str:
    out = []
    for i, b in enumerate(REGISTRY.values(), 1):
        lines = [f"### {i}. `{b.id}`（{b.label}）",
                 f"{b.help} 执行者：{ACTOR_ZH.get(b.actor, b.actor)}。消耗：{b.cost} 次。"
                 + (" 产出一张清单（`as`）。" if b.produces else ""), "",
                 "| 参数 | 类型 | 必填 | 默认 | 范围 / 可选值 | 说明 |", "|---|---|---|---|---|---|"]
        for p in b.params:
            rng = " / ".join(map(str, p.choices)) if p.choices else (
                f"{p.min:g}–{p.max:g}" if p.min is not None and p.max is not None else
                f"≥ {p.min:g}" if p.min is not None else "")
            dflt = "" if p.default is None else str(p.default).replace("|", "\\|")
            lines.append(f"| `{p.name}` | {TYPE_ZH.get(p.type, p.type)} | {'是' if p.required else ''} | {dflt} | "
                         f"{rng} | {p.label}。{p.help.replace('|', '/')} |")
        out.append("\n".join(lines))
    return "\n\n".join(out)


def _tasks_dir() -> Path:
    d = DATA_ROOT / "tasks"
    return d if d.exists() else PROJECT_ROOT / "tasks"


def examples_text(tasks_dir: Path | None = None) -> str:
    d = Path(tasks_dir) if tasks_dir else _tasks_dir()
    parts = []
    for name in EXAMPLES:
        p = d / f"{name}.yaml"
        if p.exists():
            parts.append(f"### {name}\n\n```yaml\n{p.read_text(encoding='utf-8').rstrip()}\n```")
    return "\n\n".join(parts)


def build(tasks_dir: Path | None = None) -> str:
    tpl = (builtin_dir() / "taskbook_guide.md").read_text(encoding="utf-8")
    return tpl.replace("{{BLOCKS}}", blocks_text()).replace("{{EXAMPLES}}", examples_text(tasks_dir))
