import pytest

from hands.taskbook import book as B

TRANS = """format: taskbook/1
name: transformers
title: 变形金刚角色（Google）
params:
  - {name: lang, type: text, default: en, label: 结果语言}
groups:
  - optimus: [galvatron, megatron, starscream]
  - megatron: [starscream, shockwave, soundwave]
  - movie: [fallen, decepticon, autobot]
start: optimus
limits: {budget: 120, keep_tabs: 30, trial_budget: 5}
flow:
  - search:  {url: "https://www.google.com/search?q={q}", query: "{word} transformers", extra: {hl: "{lang}"}}
  - scroll_all: {mode: bottom_then_top}
  - pick_list: {region: 搜索结果的标题和摘要, max: 10, as: 结果}
  - pick_list: {region: 相关搜索, anchor: [相关搜索, Related searches], max: 12, as: 相关}
  - ratio:   {in: 结果, top: 10, words: 已知词, at_least: 0.8, drop_current: true, as: 高频}
  - tree:    {use: 高频, op: add_to_current}
  - filter:  {in: 相关, alias: {擎天柱: optimus, Optimus Prime: optimus}, drop_current: true, only_known: true, as: 相关已知}
  - tree:    {use: 相关已知, op: add_to_current}
output: {type: leaves}
"""

DINNER = """format: taskbook/1
name: dinner
params:
  - {name: city, type: text, default: 北京}
  - {name: n, type: int, default: 3, min: 1, max: 10}
groups:
  - 晚餐: [川菜, 寿司]
start: 晚餐
flow:
  - search: {url: "https://www.bing.com/search?q={q}", query: "{word} {city} 收藏榜"}
  - pick_list: {region: 收藏榜, max: 5, as: 榜单}
  - rank: {in: 榜单, k: 1, as: 榜首}
  - open: {from: 榜首, n: 1, back: true, flow: [
        {read_fields: {names: [店名, 价位, 评分], name_field: 店名, as: 详情}} ]}
  - compare: {in: 详情, field: 价位, op: "<=", value: "$$", as: 便宜, unknown_as: 价位未知}
  - tree: {use: 便宜, op: add_to_current, new_terminal: true}
  - tree: {use: 价位未知, op: add_to_current, new_terminal: true}
output: {type: ranked, sort: ["价位:asc", "评分:desc"], n: 3, group_by: key, unknown: last}
"""


def test_parse_examples():
    t = B.parse(TRANS)
    assert t.name == "transformers" and t.start == "optimus"
    assert t.groups[1] == ("megatron", ["starscream", "shockwave", "soundwave"])
    assert len(t.flow) == 8 and list(t.flow[0]) == ["search"]
    assert t.limits == {"budget": 120, "keep_tabs": 30, "trial_budget": 5}
    assert len(t.rev) == 16 and t.rev == B.rev_of(TRANS)
    d = B.parse(DINNER, "x")
    assert d.limits["budget"] == 120 and d.output["unknown"] == "last"
    assert d.flow[3]["open"]["flow"][0]["read_fields"]["as"] == "详情"


def test_roundtrip():
    for text in (TRANS, DINNER):
        b = B.parse(text)
        again = B.parse(B.dump(b))
        assert again.to_dict() == b.to_dict()


def test_bad_format():
    with pytest.raises(B.BookError, match="格式版本"):
        B.parse("format: taskbook/9\n")
    with pytest.raises(B.BookError, match="格式版本"):
        B.parse("name: old\nsearch_url: x\n")
    with pytest.raises(B.BookError):
        B.parse("format: taskbook/1\ngroups: [a, b]\n")


def test_bind_and_render():
    d = B.bind(B.parse(DINNER), {"city": "上海", "n": "5"})
    assert d.values == {"city": "上海", "n": 5}
    assert B.bind(B.parse(DINNER), {}).values == {"city": "北京", "n": 3}
    with pytest.raises(B.BookError, match="没有这些参数"):
        B.bind(B.parse(DINNER), {"zzz": 1})
    with pytest.raises(B.BookError, match="不能大于"):
        B.bind(B.parse(DINNER), {"n": 99})
    assert B.render("{word} {city} 收藏榜", "川菜", "晚餐", d.values) == "川菜 上海 收藏榜"
    assert B.render("https://x/?q={q}&k={key}", "w", "k", keep=("q",)) == "https://x/?q={q}&k=k"
    with pytest.raises(B.BookError, match="没有来源"):
        B.render("{nope}", "w")


def test_save_backup_and_conflict(tmp_path):
    r1 = B.save(tmp_path, "t", TRANS, None, save_as=True)
    assert (tmp_path / "t.yaml").read_text(encoding="utf-8") == TRANS
    r2 = B.save(tmp_path, "t", DINNER, r1)
    assert r2 == B.rev_of(DINNER)
    baks = list((tmp_path / ".bak").glob("t.*.yaml"))
    assert len(baks) == 1 and baks[0].read_text(encoding="utf-8") == TRANS
    with pytest.raises(B.BookConflict, match="另一端"):
        B.save(tmp_path, "t", TRANS, r1)
    assert (tmp_path / "t.yaml").read_text(encoding="utf-8") == DINNER
    with pytest.raises(B.BookConflict, match="同名"):
        B.save(tmp_path, "t", TRANS, None, save_as=True)
    with pytest.raises(B.BookError):
        B.save(tmp_path, "../evil", TRANS, None)
    B.save(tmp_path, "t", TRANS, r2)
    assert len(list((tmp_path / ".bak").glob("t.*.yaml"))) == 2
