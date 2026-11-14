import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from helpers import el, page, view  # noqa: E402

from hands.decision import DecisionInput, validate_decision  # noqa: E402
from hands.rule_decider import Rule, RuleDecider  # noqa: E402

RULES = [
    Rule({"node_depth": 0, "element": {"role": "link", "href_contains": "reddit.com"},
          "no_child_with_goal": "读取评论"},
         {"tool": "create_child", "arguments": {"goal": "读取评论", "done_when": "全部读到"}}),
    Rule({"goal_is": "读取评论", "element": {"role": "link", "href_contains": "reddit"}},
         {"tool": "click", "arguments": {"element": "{{match}}"}}),
    Rule({"node_depth": 0, "no_element": {"role": "link", "href_contains": "reddit"}},
         {"tool": "complete", "arguments": {"summary": "未出现 {{inputs.q}}"}}),
]


async def decide(d, p, v, inputs=None):
    raw = await d.decide(DecisionInput(p, v, inputs or {}))
    return validate_decision(raw)


async def test_element_match_creates_child():
    p = page([el("e0", href="http://a"), el("e1", href="https://www.reddit.com/r")])
    dec = await decide(RuleDecider(RULES), p, view())
    assert dec.tool == "create_child" and dec.arguments["goal"] == "读取评论"


async def test_match_rendering():
    p = page([el("e0", href="http://a"), el("e1", href="https://www.reddit.com/r")])
    dec = await decide(RuleDecider(RULES), p, view(goal="读取评论", depth=1))
    assert dec.tool == "click" and dec.arguments["element"] == "e1"


async def test_href_not_contains_skips_own_links():
    rules = [Rule({"element": {"role": "link", "name_contains": "腿", "href_not_contains": ["google.", "/search?"]}},
                  {"tool": "click", "arguments": {"element": "{{match}}"}})]
    p = page([el("e0", name="王楚钦 腿伤", href="https://www.google.com/search?q=x"),
              el("e1", name="王楚钦 腿伤", href=None),
              el("e2", name="王楚钦 腿伤近况", href="https://news.example.com/a")])
    dec = await decide(RuleDecider(rules), p, view())
    assert dec.tool == "click" and dec.arguments["element"] == "e2"


async def test_context_contains_matches_snippet():
    rules = [Rule({"element": {"role": "link", "context_contains": ["出走", "德国"], "href_not_contains": "google."}},
                  {"tool": "click", "arguments": {"element": "{{match}}"}})]
    a = el("e0", name="樊振东 - 维基百科", href="https://zh.wikipedia.org/x")
    a.context = "樊振东 - 维基百科 中国乒乓球运动员"
    b = el("e1", name="樊振东最新消息", href="https://news.example.com/b")
    b.context = "樊振东最新消息 德国乒乓球甲级联赛官方宣布，樊振东当选最佳球员"
    dec = await decide(RuleDecider(rules), page([a, b]), view())
    assert dec.tool == "click" and dec.arguments["element"] == "e1"


async def test_largest_button_with_exclusions():
    from hands.geometry import Box
    rules = [Rule({"element": {"role": "button", "largest": True, "name_not_contains": ["checkout", "pay"]}},
                  {"tool": "click", "arguments": {"element": "{{match}}"}})]
    small = el("e0", role="button", name="Search")
    small.box = Box(0, 0, 40, 20)
    big = el("e1", role="button", name="Shop now")
    big.box = Box(0, 0, 300, 60)
    huge_bad = el("e2", role="button", name="Checkout")
    huge_bad.box = Box(0, 0, 500, 80)
    dec = await decide(RuleDecider(rules), page([small, big, huge_bad]), view())
    assert dec.tool == "click" and dec.arguments["element"] == "e1"


async def test_invisible_not_matched_and_no_element():
    p = page([el("e1", href="https://reddit.com", visible=False)])
    dec = await decide(RuleDecider(RULES), p, view(), {"q": "reddit"})
    assert dec.tool == "complete" and dec.arguments["summary"] == "未出现 reddit"


async def test_order_priority():
    rules = [Rule({}, {"tool": "back"}), Rule({}, {"tool": "fail", "arguments": {"reason": "x"}})]
    dec = await decide(RuleDecider(rules), page(), view())
    assert dec.tool == "back"


async def test_default_and_no_match():
    rules = [Rule({"url_contains": "zzz"}, {"tool": "back"})]
    dec = await decide(RuleDecider(rules, Rule({}, {"tool": "scroll", "arguments": {"to_bottom": True}})), page(), view())
    assert dec.tool == "scroll"
    dec = await decide(RuleDecider(rules), page(), view())
    assert dec.tool == "fail"


async def test_last_read_and_read_count():
    rules = [Rule({"read_count": {"min": 1}}, {"tool": "complete", "arguments": {"summary": "{{last_read}}"}})]
    recent = [{"tool": "read_links", "data": [{"title": "T", "href": "H"}]}]
    dec = await decide(RuleDecider(rules), page(), view(recent=recent))
    assert dec.arguments["summary"] == "T | H"


def test_unknown_condition():
    with pytest.raises(ValueError):
        RuleDecider([Rule({"bogus": 1}, {"tool": "back"})])
