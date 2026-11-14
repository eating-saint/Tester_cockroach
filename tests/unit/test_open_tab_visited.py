"""规则条件 not_visited（跳过本次用例里已点过的链接）与 open_tab 工具。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from helpers import el, page, view  # noqa: E402

from hands.decision import DecisionInput, DecisionInvalid, validate_decision  # noqa: E402
from hands.rule_decider import Rule, RuleDecider  # noqa: E402

CLICK_NEW = Rule({"element": {"role": "link", "name_contains": "megatron", "not_visited": True}},
                 {"tool": "click", "arguments": {"element": "{{match}}"}})
DONE = Rule({}, {"tool": "complete", "arguments": {"summary": "全部点过"}})


async def decide(d, p, v=None):
    return validate_decision(await d.decide(DecisionInput(p, v or view(), {})))


async def test_not_visited_walks_through_all_matches():
    p = page([el("e0", name="Megatron A", href="https://a/1"), el("e1", name="x", href="https://b"),
              el("e2", name="megatron B", href="https://a/2")])
    d = RuleDecider([CLICK_NEW], DONE)
    got = [(await decide(d, p)).arguments.get("element") for _ in range(3)]
    assert got == ["e0", "e2", None]
    assert (await decide(d, p)).tool == "complete"


async def test_visited_survives_element_renumbering():
    """后退后重新读取页面，元素编号会变；按链接地址记，不按编号。"""
    d = RuleDecider([CLICK_NEW], DONE)
    first = await decide(d, page([el("e0", name="megatron A", href="https://a/1"),
                                  el("e1", name="megatron B", href="https://a/2")]))
    assert first.arguments["element"] == "e0"
    again = await decide(d, page([el("e5", name="other", href="https://z"),
                                  el("e6", name="megatron A", href="https://a/1"),
                                  el("e7", name="megatron B", href="https://a/2")]))
    assert again.arguments["element"] == "e7"


async def test_without_not_visited_same_element_repeats():
    rule = Rule({"element": {"role": "link", "name_contains": "megatron"}},
                {"tool": "click", "arguments": {"element": "{{match}}"}})
    d = RuleDecider([rule])
    p = page([el("e0", name="megatron A", href="https://a/1"), el("e1", name="megatron B", href="https://a/2")])
    assert [(await decide(d, p)).arguments["element"] for _ in range(2)] == ["e0", "e0"]


async def test_create_child_does_not_mark_visited():
    rules = [Rule({"node_depth": 0, "element": {"role": "link", "name_contains": "megatron", "not_visited": True}},
                  {"tool": "create_child", "arguments": {"goal": "查看结果", "done_when": "读到"}}),
             Rule({"goal_is": "查看结果", "element": {"role": "link", "name_contains": "megatron", "not_visited": True}},
                  {"tool": "open_tab", "arguments": {"element": "{{match}}", "switch": True}})]
    d = RuleDecider(rules)
    p = page([el("e0", name="megatron A", href="https://a/1")])
    assert (await decide(d, p, view())).tool == "create_child"
    dec = await decide(d, p, view(goal="查看结果", depth=1))
    assert dec.tool == "open_tab" and dec.arguments == {"element": "e0", "switch": True}
    # open_tab 之后该链接算点过：母任务不再为它建子任务（没有规则匹配，决策器给出 fail）
    assert (await decide(d, p, view())).tool == "fail"


def test_open_tab_validation():
    ok = validate_decision('{"decision":"call_tool","reason":"r","tool":"open_tab","arguments":{"element":"e1"}}')
    assert ok.tool == "open_tab"
    with pytest.raises(DecisionInvalid):
        validate_decision('{"decision":"call_tool","reason":"r","tool":"open_tab","arguments":{}}')
    with pytest.raises(DecisionInvalid):
        validate_decision('{"decision":"call_tool","reason":"r","tool":"open_tab",'
                          '"arguments":{"element":"e1","switch":"yes"}}')
