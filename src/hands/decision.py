"""决策协议：工具注册表、决策校验、决策器接口。ver1 的大模型决策器实现同一接口。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
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
}


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


def validate_decision(raw: str) -> Decision:
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
    return Decision(obj["decision"], str(obj["reason"]), tool, args)


@dataclass
class DecisionInput:
    page: "PageState"
    task: "TaskView"
    inputs: dict = field(default_factory=dict)


class Decider(Protocol):
    async def decide(self, inp: DecisionInput) -> str: ...


async def decide_with_retry(decider: Decider, inp: DecisionInput, max_tries: int = 3
                            ) -> tuple[Decision | None, list[str], int]:
    raws: list[str] = []
    for i in range(1, max_tries + 1):
        raw = await decider.decide(inp)
        raws.append(raw)
        try:
            return validate_decision(raw), raws, i
        except DecisionInvalid:
            continue
    return None, raws, max_tries
