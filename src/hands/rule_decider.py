"""M8 规则决策器：按用例规则输出与大模型相同格式的决策 JSON（ver1 替换为大模型）。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .decision import DecisionInput


@dataclass
class Rule:
    when: dict = field(default_factory=dict)
    do: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "Rule":
        return cls(when=dict(d.get("when") or {}), do=dict(d["do"]))


def _as_list(v):
    return v if isinstance(v, list) else [v]


def _match_element(page, spec: dict):
    """按条件找元素：默认返回第一个满足的；largest: true 时返回满足条件里面积最大的。"""
    if spec.get("largest"):
        found = list(_iter_matches(page, spec))
        return max(found, key=lambda e: e.box.width * e.box.height) if found else None
    return next(_iter_matches(page, spec), None)


def _iter_matches(page, spec: dict):
    roles = _as_list(spec["role"]) if "role" in spec else None
    for e in page.elements:
        if not e.visible:
            continue
        if roles is not None and e.role not in roles:
            continue
        if "name_contains" in spec and spec["name_contains"].lower() not in (e.name or "").lower():
            continue
        if "name_not_contains" in spec and any(
                x.lower() in (e.name or "").lower() for x in _as_list(spec["name_not_contains"])):
            continue
        if "text_contains" in spec and spec["text_contains"].lower() not in (e.text or "").lower():
            continue
        if "href_contains" in spec and spec["href_contains"].lower() not in (e.href or "").lower():
            continue
        if "context_contains" in spec and not any(
                x.lower() in (getattr(e, "context", "") or e.text or e.name or "").lower()
                for x in _as_list(spec["context_contains"])):
            continue                       # 所在块（标题 + 摘要）含任一关键词
        if "href_not_contains" in spec and (not e.href or any(
                x.lower() in e.href.lower() for x in _as_list(spec["href_not_contains"]))):
            continue                       # 没有链接地址的也排除：要的是指向站外的结果链接
        if "in_viewport" in spec and bool(e.in_viewport) != bool(spec["in_viewport"]):
            continue                       # 只认当前屏幕里看得到的（找不到就先用滚轮往下滑）
        if "enabled" in spec and e.enabled != spec["enabled"]:
            continue
        if spec.get("largest") and (e.box.width * e.box.height <= 0 or e.frame_path):
            continue                       # 比面积只比主文档里有尺寸的
        yield e


def _reads(inp: DecisionInput) -> list[dict]:
    return [s for s in inp.task.recent_steps if str(s.get("tool", "")).startswith("read_")]


def _cmp_count(n: int, spec) -> bool:
    if isinstance(spec, dict):
        return spec.get("min", n) <= n <= spec.get("max", n)
    return n == spec


def _c_node_depth(inp, v, ctx): return inp.task.depth == v
def _c_goal_is(inp, v, ctx): return inp.task.goal == v
def _c_url_contains(inp, v, ctx): return all(x.lower() in inp.page.url.lower() for x in _as_list(v))
def _c_url_not_contains(inp, v, ctx): return all(x.lower() not in inp.page.url.lower() for x in _as_list(v))
def _c_text_contains(inp, v, ctx): return all(x.lower() in inp.page.body_text.lower() for x in _as_list(v))


def _c_element(inp, v, ctx):
    e = _match_element(inp.page, v)
    if e is not None:
        ctx["match"] = e.id
        return True
    return False


def _c_no_element(inp, v, ctx): return _match_element(inp.page, v) is None
def _c_has_child_summary(inp, v, ctx): return bool(inp.task.child_summaries) == bool(v)
def _c_no_child_with_goal(inp, v, ctx): return all(c["goal"] != v for c in inp.task.child_summaries)


def _c_last_tool(inp, v, ctx):
    return bool(inp.task.recent_steps) and inp.task.recent_steps[-1].get("tool") == v


def _c_last_effective(inp, v, ctx):
    return bool(inp.task.recent_steps) and bool(inp.task.recent_steps[-1].get("effective")) == bool(v)


def _c_read_count(inp, v, ctx): return _cmp_count(len(_reads(inp)), v)
def _c_steps_used(inp, v, ctx): return _cmp_count(inp.task.steps_used, v)


CONDITIONS = {
    "node_depth": _c_node_depth, "goal_is": _c_goal_is, "url_contains": _c_url_contains,
    "url_not_contains": _c_url_not_contains, "text_contains": _c_text_contains,
    "element": _c_element, "no_element": _c_no_element, "has_child_summary": _c_has_child_summary,
    "no_child_with_goal": _c_no_child_with_goal, "last_tool": _c_last_tool,
    "last_effective": _c_last_effective, "read_count": _c_read_count, "steps_used": _c_steps_used,
}

_TPL = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


def _last_read_text(inp) -> str:
    reads = _reads(inp)
    if not reads:
        return ""
    data = reads[-1].get("data")
    if isinstance(data, list):
        parts = []
        for x in data:
            if isinstance(x, dict):
                parts.append(f"{x.get('title', '')} | {x.get('href', '')}")
            else:
                parts.append(str(x))
        return "\n".join(parts)
    return "" if data is None else str(data)


def _render(value, inp, ctx):
    if isinstance(value, str):
        def rep(m):
            key = m.group(1)
            if key == "match":
                return str(ctx.get("match", ""))
            if key == "last_read":
                return _last_read_text(inp)
            if key.startswith("inputs."):
                return str(inp.inputs.get(key[7:], ""))
            raise ValueError(f"unknown template variable: {key}")
        return _TPL.sub(rep, value)
    if isinstance(value, dict):
        return {k: _render(v, inp, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, inp, ctx) for v in value]
    return value


class RuleDecider:
    def __init__(self, rules: list[Rule], default: Rule | None = None):
        for r in rules + ([default] if default else []):
            for name in r.when:
                if name not in CONDITIONS:
                    raise ValueError(f"unknown condition: {name}")
        self.rules = rules
        self.default = default

    @classmethod
    def from_case(cls, case) -> "RuleDecider":
        return cls(list(case.rules), case.default)

    def _out(self, reason, do, inp, ctx) -> str:
        return json.dumps({"decision": "call_tool", "reason": reason, "tool": do["tool"],
                           "arguments": _render(do.get("arguments") or {}, inp, ctx)}, ensure_ascii=False)

    async def decide(self, inp: DecisionInput) -> str:
        for i, r in enumerate(self.rules):
            ctx: dict = {}
            if all(CONDITIONS[k](inp, v, ctx) for k, v in r.when.items()):
                return self._out(f"rule#{i} matched", r.do, inp, ctx)
        if self.default is not None:
            return self._out("default rule", self.default.do, inp, {})
        return json.dumps({"decision": "call_tool", "reason": "no rule matched", "tool": "fail",
                           "arguments": {"reason": "no rule matched"}}, ensure_ascii=False)
