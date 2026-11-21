import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_taskbook_book import DINNER, TRANS  # noqa: E402

from hands.taskbook.book import parse  # noqa: E402
from hands.taskbook.validate import check, check_text  # noqa: E402

HEAD = """format: taskbook/1
name: t
params:
  - {name: lang, type: text, default: en}
groups:
  - a: [b, c]
start: a
"""


def probs(flow: str, head: str = HEAD):
    _, ps = check_text(head + "flow:\n" + flow)
    return [(p.step, p.block, p.param) for p in ps], ps


def test_examples_clean():
    assert check(parse(TRANS)) == []
    assert check(parse(DINNER)) == []


def test_unknown_block_and_param():
    got, ps = probs("  - fly: {to: moon}\n  - scroll_all: {speed: 3}\n")
    assert ("flow[0]", "fly", None) in got
    assert ("flow[1]", "scroll_all", "speed") in got
    assert "没有这个积木" in ps[0].why


def test_required_type_range_choice():
    got, ps = probs("  - pick_list: {max: 99}\n  - ratio: {in: 已知词, words: 已知词, at_least: 1.5, as: r, top: x}\n"
                    "  - tree: {use: r, op: grow}\n  - filter: {in: r, stop: x, as: f2}\n")
    assert ("flow[0]", "pick_list", "region") in got and ("flow[0]", "pick_list", "as") in got
    assert ("flow[0]", "pick_list", "max") in got
    assert ("flow[1]", "ratio", "at_least") in got and ("flow[1]", "ratio", "top") in got
    assert ("flow[2]", "tree", "op") in got
    assert ("flow[3]", "filter", "stop") in got
    why = {(p.step, p.param): p.why for p in ps}
    assert "必填" in why[("flow[0]", "region")] and "不能大于 50" in why[("flow[0]", "max")]
    assert "不能大于 1" in why[("flow[1]", "at_least")]


def test_combination_rules():
    got, _ = probs("  - rank: {in: 已知词, as: x}\n  - page: {}\n  - scroll_all: {mode: to_text}\n"
                   "  - tree: {use: x, op: add_to_key}\n")
    assert ("flow[0]", "rank", "k") in got and ("flow[1]", "page", "to") in got
    assert ("flow[2]", "scroll_all", "text") in got and ("flow[3]", "tree", "key") in got


def test_list_references_order():
    got, ps = probs("  - tree: {use: 结果, op: add_to_current}\n"
                    "  - pick_list: {region: r, as: 结果}\n  - tree: {use: 结果, op: add_to_current}\n"
                    "  - pick_list: {region: r, as: 结果}\n  - pick_list: {region: r, as: 已知词}\n"
                    "  - stop: {when: 当前词}\n")
    assert got == [("flow[0]", "tree", "use"), ("flow[3]", "pick_list", "as"), ("flow[4]", "pick_list", "as")]
    assert "还没有产出" in ps[0].why


def test_nested_open_flow():
    got, _ = probs("  - pick_list: {region: r, as: 榜单}\n"
                   "  - open: {from: 榜单, flow: [{read_fields: {names: [价位]}}, {tree: {use: 外面, op: terminal}}]}\n"
                   "  - open: {from: 榜单, flow: [{read_fields: {names: [价位], as: 详情}}]}\n"
                   "  - compare: {in: 详情, field: 价位, op: '<=', value: $$, as: ok}\n")
    assert ("flow[1].open.flow[0]", "read_fields", "as") in got
    assert ("flow[1].open.flow[1]", "tree", "use") in got
    assert not any(s.startswith("flow[3]") for s, _, _ in got)      # 子流程产出外层可引用


def test_templates():
    got, ps = probs("  - search: {url: 'https://x/?s={word}', query: '{word} {city}', extra: {hl: '{q}'}}\n"
                    "  - search: {url: 'https://x/?q={q}', query: {word}}\n")
    assert ("flow[0]", "search", "url") in got
    assert ("flow[0]", "search", "query") in got
    assert ("flow[0]", "search", "extra.hl") in got
    assert ("flow[1]", "search", "query") in got
    assert any("没加引号" in p.why for p in ps)


def test_globals():
    head = """format: taskbook/1
name: t
params:
  - {name: word, type: text}
  - {name: x, type: date}
groups: []
start: zz
limits: {budget: 0}
output: {type: ranked, sort: [价位], unknown: maybe}
"""
    _, ps = check_text(head + "flow: []\n")
    steps = {(p.step, p.param) for p in ps}
    assert {("params[0]", "name"), ("params[1]", "type"), ("groups", None), ("limits", "budget"),
            ("output", "sort"), ("output", "unknown"), ("flow", None)} <= steps
    _, ps = check_text(HEAD.replace("start: a", "start: zz") + "flow:\n  - scroll_all: {}\n")
    assert [(p.step, p.why) for p in ps] == [("start", "起始词「zz」不在任何组里")]


def test_unknown_version_and_status():
    b, ps = check_text("format: taskbook/2\n")
    assert b is None and ps[0].step == "文件" and "格式版本" in ps[0].why
    b, ps = check_text(TRANS)
    assert b.status == "ok" and ps == []
    b, ps = check_text(HEAD + "flow: []\n")
    assert b.status == "draft"
