"""决策协议：工具注册表、决策校验、决策器接口。ver1 的大模型决策器实现同一接口。"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .pagestate import PageState
    from .tasktree import TaskView


@dataclass
class ToolSpec:
    kind: str                                   # tree / action / read
    required: dict[str, type | tuple] = field(default_factory=dict)
    optional: dict[str, type | tuple] = field(default_factory=dict)


TOOL_REGISTRY: dict[str, ToolSpec] = {
    "create_child": ToolSpec("tree", {"goal": str, "done_when": str}),
    "complete": ToolSpec("tree", {"summary": str}),
    "fail": ToolSpec("tree", {"reason": str}),
    "click": ToolSpec("action", {"element": str}),
    # 在新标签页打开链接（Ctrl+点击），原标签页保留；switch=true 时切到新标签页继续，否则留在原页
    "open_tab": ToolSpec("action", {"element": str}, {"switch": bool}),
    "type": ToolSpec("action", {"element": str, "text": str}),
    "press": ToolSpec("action", {"key": str}),
    "scroll": ToolSpec("action", {}, {"direction": str, "pixels": (int, float), "to_bottom": bool}),
    "back": ToolSpec("action"),
    "read_text": ToolSpec("read", {}, {"element": str, "contains": str}),
    "read_links": ToolSpec("read", {}, {"href_contains": str, "href_excludes": str, "title_min_len": int}),
    # 内容抽取（ver1）：items 每项 {field, value, element?}；执行时回查 element（省略则查正文）里有没有 value
    "extract": ToolSpec("read", {"items": list}),
}

ELEMENT_ARGS = ("click", "open_tab", "type", "read_text")   # 这些工具的 element 参数指向本步页面里的元素


def _check_extract_items(items: list):
    if not items:
        raise DecisionInvalid("extract: items must not be empty")
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise DecisionInvalid(f"extract: items[{i}] must be an object")
        for k in ("field", "value"):
            if not isinstance(it.get(k), str):
                raise DecisionInvalid(f"extract: items[{i}].{k} must be a string")
        # element 可省略：省略表示取自页面正文（价格、评分这类普通文字不在可交互元素清单里）
        if "element" in it and not isinstance(it["element"], str):
            raise DecisionInvalid(f"extract: items[{i}].element must be a string")


class DecisionInvalid(Exception):
    pass


@dataclass
class Decision:
    decision: str
    reason: str
    tool: str
    arguments: dict

    def to_dict(self) -> dict:
        return {"decision": self.decision, "reason": self.reason, "tool": self.tool, "arguments": self.arguments}


def validate_decision(raw: str, page: "PageState | None" = None) -> Decision:
    """page 不为 None 时，再检查决策里引用的元素在本步页面上存在（ver1：编号失效判为不合法）。"""
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError) as e:
        raise DecisionInvalid(f"not json: {e}")
    if not isinstance(obj, dict):
        raise DecisionInvalid("not an object")
    for k in ("decision", "reason", "tool", "arguments"):
        if k not in obj:
            raise DecisionInvalid(f"missing field: {k}")
    if obj["decision"] != "call_tool":
        raise DecisionInvalid(f"unsupported decision: {obj['decision']!r}")
    tool = obj["tool"]
    spec = TOOL_REGISTRY.get(tool)
    if spec is None:
        raise DecisionInvalid(f"unregistered tool: {tool!r}")
    args = obj["arguments"]
    if not isinstance(args, dict):
        raise DecisionInvalid("arguments must be an object")
    for name, typ in spec.required.items():
        if name not in args:
            raise DecisionInvalid(f"{tool}: missing argument {name!r}")
        if not isinstance(args[name], typ) or isinstance(args[name], bool) and typ is not bool:
            raise DecisionInvalid(f"{tool}: argument {name!r} has wrong type")
    allowed = {**spec.required, **spec.optional}
    for name, val in args.items():
        if name not in allowed:
            raise DecisionInvalid(f"{tool}: unknown argument {name!r}")
        typ = allowed[name]
        if not isinstance(val, typ) or (isinstance(val, bool) and typ is not bool):
            raise DecisionInvalid(f"{tool}: argument {name!r} has wrong type")
    if tool == "scroll":
        if args.get("to_bottom") is True:
            if "direction" in args or "pixels" in args:
                raise DecisionInvalid("scroll: to_bottom excludes direction/pixels")
        elif not ("direction" in args and "pixels" in args) or args["direction"] not in ("up", "down"):
            raise DecisionInvalid("scroll: need to_bottom=true or direction(up/down)+pixels")
    if tool == "extract":
        _check_extract_items(args["items"])
    if page is not None:
        refs = []
        if tool in ELEMENT_ARGS and isinstance(args.get("element"), str):
            refs.append(args["element"])
        if tool == "extract":
            refs += [it["element"] for it in args["items"] if it.get("element")]
        for eid in refs:
            if page.element(eid) is None:
                raise DecisionInvalid(f"unknown element {eid}")
    return Decision(obj["decision"], str(obj["reason"]), tool, args)


@dataclass
class DecisionInput:
    page: "PageState"
    task: "TaskView"
    inputs: dict = field(default_factory=dict)
    step_dir: Path | None = None                       # 本步目录：大模型决策器从这里读截图、写记录
    brief: dict = field(default_factory=dict)          # 任务书附加信息：{hints: [...], extract_fields: [...]}
    last_error: str | None = None                      # 上一次决策不合法的原因（重问时带给决策器）


class Decider(Protocol):
    async def decide(self, inp: DecisionInput) -> str: ...


async def decide_with_retry(decider: Decider, inp: DecisionInput, max_tries: int = 3
                            ) -> tuple[Decision | None, list[str], int]:
    raws: list[str] = []
    for i in range(1, max_tries + 1):
        raw = await decider.decide(inp)
        raws.append(raw)
        try:
            return validate_decision(raw, getattr(inp, "page", None)), raws, i
        except DecisionInvalid as e:
            if isinstance(inp, DecisionInput):
                inp = dataclasses.replace(inp, last_error=str(e))
            continue
    return None, raws, max_tries
