"""ver1 决策协议：校验元素存在、重问时带回原因、extract 工具格式。"""
import asyncio
import json

import pytest

from hands.decision import DecisionInput, DecisionInvalid, decide_with_retry, validate_decision
from hands.dom import Element
from hands.geometry import Box
from hands.pagestate import PageState
from hands.tasktree import TaskTree


def _page():
    el = Element("e1", "button", "搜索", "搜索", "button", None, None, Box(0, 0, 10, 10), True, True, True)
    return PageState("http://x/", "t", Box(0, 0, 100, 100), 1.0, [el], "")


def _raw(tool, **args):
    return json.dumps({"decision": "call_tool", "reason": "r", "tool": tool, "arguments": args})


def test_unknown_element_rejected_only_with_page():
    raw = _raw("click", element="#99")
    validate_decision(raw)                                   # 不传 page：和以前一样只查格式
    with pytest.raises(DecisionInvalid, match="unknown element #99"):
        validate_decision(raw, _page())
    assert validate_decision(_raw("click", element="e1"), _page()).tool == "click"


def test_extract_items_format():
    ok = _raw("extract", items=[{"field": "标题", "value": "搜索", "element": "e1"}])
    assert validate_decision(ok, _page()).tool == "extract"
    with pytest.raises(DecisionInvalid):
        validate_decision(_raw("extract", items=[]))
    with pytest.raises(DecisionInvalid):
        validate_decision(_raw("extract", items=[{"field": "标题", "value": 3, "element": "e1"}]))
    with pytest.raises(DecisionInvalid, match="unknown element e7"):
        validate_decision(_raw("extract", items=[{"field": "标题", "value": "x", "element": "e7"}]), _page())


class _Scripted:
    def __init__(self, raws):
        self.raws = list(raws)
        self.seen_errors = []

    async def decide(self, inp):
        self.seen_errors.append(inp.last_error)
        return self.raws.pop(0)


def _inp():
    return DecisionInput(_page(), TaskTree("g").view())


def test_retry_carries_reason():
    d = _Scripted([_raw("click", element="#99"), _raw("click", element="e1")])
    dec, raws, tries = asyncio.run(decide_with_retry(d, _inp()))
    assert dec is not None and dec.arguments["element"] == "e1" and tries == 2
    assert d.seen_errors[0] is None and "unknown element" in d.seen_errors[1]


def test_three_invalid_returns_none():
    d = _Scripted([_raw("click", element="#9")] * 3)
    dec, raws, tries = asyncio.run(decide_with_retry(d, _inp()))
    assert dec is None and len(raws) == 3 and tries == 3
