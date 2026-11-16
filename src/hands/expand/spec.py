"""任务类型定义（spec F11）：读 YAML（kind: expand）、替换参数、校验，错误指出具体是哪一项。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

OUTPUT_TYPES = ("leaves", "ranked")


class TaskTypeInvalid(ValueError):
    pass


@dataclass
class Condition:
    id: str
    text: str                 # 渲染后的自然语言（给 4b 看）
    params: dict = field(default_factory=dict)


@dataclass
class TaskType:
    name: str
    title: str
    search_url: str
    query: str
    groups: list[tuple[str, list[str]]]
    start: str
    sources: list[str]
    conditions: list[Condition]
    budget: int = 120
    keep_tabs: int = 30
    read_per_node: int = 1
    node_steps: int = 10
    output: dict = field(default_factory=lambda: {"type": "leaves"})
    params: list[dict] = field(default_factory=list)
    values: dict = field(default_factory=dict)          # 本次运行实际用的参数值
    path: str = ""

    def mapping(self) -> dict[str, list[str]]:
        return {k: list(v) for k, v in self.groups}

    def to_dict(self) -> dict:
        return {"name": self.name, "title": self.title, "search_url": self.search_url, "query": self.query,
                "groups": [{k: v} for k, v in self.groups], "start": self.start, "sources": self.sources,
                "conditions": [{"id": c.id, "text": c.text, "params": c.params} for c in self.conditions],
                "budget": self.budget, "keep_tabs": self.keep_tabs, "read_per_node": self.read_per_node,
                "node_steps": self.node_steps, "output": self.output, "params": self.params, "values": self.values}


def _fmt(v) -> str:
    if isinstance(v, float) and 0 < v <= 1:
        return f"{v:.0%}"
    return str(v)


def _render(text: str, params: dict) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: _fmt(params[m.group(1)]) if m.group(1) in params else m.group(0), text)


def _groups(raw) -> list[tuple[str, list[str]]]:
    items = []
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, list):
        for i, g in enumerate(raw):
            if not isinstance(g, dict) or len(g) != 1:
                raise TaskTypeInvalid(f"groups[{i}] 应写成「键: [值, …]」")
            items.append(next(iter(g.items())))
    else:
        raise TaskTypeInvalid("groups 缺失或格式不对（应为「键: [值, …]」的列表）")
    out = []
    for i, (k, v) in enumerate(items):
        if not isinstance(v, list) or not v:
            raise TaskTypeInvalid(f"groups[{i}]（{k}）的值为空")
        out.append((str(k).strip(), [str(x).strip() for x in v if str(x).strip()]))
    return out


def load_task_type(path, overrides: dict | None = None) -> TaskType:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    pre = yaml.safe_load(text) or {}
    if not isinstance(pre, dict):
        raise TaskTypeInvalid("文件内容不是一个对象")
    defaults = {str(x.get("name")): x.get("default", "") for x in pre.get("params") or [] if isinstance(x, dict)}
    values = {**defaults, **{k: v for k, v in (overrides or {}).items() if v is not None}}
    for k, v in values.items():
        text = text.replace("${" + k + "}", str(v))
    d = yaml.safe_load(text) or {}
    if d.get("kind") != "expand":
        raise TaskTypeInvalid("kind 必须是 expand")
    for k in ("search_url", "query", "start"):
        if not d.get(k):
            raise TaskTypeInvalid(f"缺少 {k}")
    if "{q}" not in d["search_url"]:
        raise TaskTypeInvalid("search_url 里要有 {q}（搜索词的位置）")
    if "{value}" not in d["query"]:
        raise TaskTypeInvalid("query 里要有 {value}（当前词的位置）")
    groups = _groups(d.get("groups"))
    start = str(d["start"]).strip()
    words = {k for k, _ in groups} | {w for _, vs in groups for w in vs}
    if start not in words:
        raise TaskTypeInvalid(f"start（{start}）不在任何组的键或值里")
    conds = []
    for i, c in enumerate(d.get("conditions") or []):
        if not isinstance(c, dict) or not c.get("text"):
            raise TaskTypeInvalid(f"conditions[{i}] 缺少 text")
        params = dict(c.get("params") or {})
        if "ratio" in params:
            r = params["ratio"]
            if not isinstance(r, (int, float)) or not 0 < float(r) <= 1:
                raise TaskTypeInvalid(f"conditions[{i}].params.ratio={r!r} 不在 0–1 之间")
        conds.append(Condition(str(c.get("id") or f"c{i + 1}"), _render(str(c["text"]), params), params))
    if not conds:
        raise TaskTypeInvalid("conditions 至少要有一条")
    budget = int(d.get("budget", 120))
    if budget < 1:
        raise TaskTypeInvalid(f"budget={budget} 必须 ≥ 1")
    out = dict(d.get("output") or {"type": "leaves"})
    if out.get("type") not in OUTPUT_TYPES:
        raise TaskTypeInvalid(f"output.type={out.get('type')!r}，可选 {' / '.join(OUTPUT_TYPES)}")
    if out["type"] == "ranked":
        if not out.get("fields") or not out.get("n"):
            raise TaskTypeInvalid("output.type 为 ranked 时要写 fields 和 n")
        out["n"] = int(out["n"])
        out.setdefault("sort", ["price:asc", "rating:desc"])
    return TaskType(
        name=str(d.get("name") or p.stem), title=str(d.get("title") or p.stem), search_url=d["search_url"],
        query=d["query"], groups=groups, start=start, sources=[str(s) for s in d.get("sources") or []],
        conditions=conds, budget=budget, keep_tabs=int(d.get("keep_tabs", 30)),
        read_per_node=int(d.get("read_per_node", 1)), node_steps=int(d.get("node_steps", 10)),
        output=out, params=list(pre.get("params") or []), values=values, path=str(p))
