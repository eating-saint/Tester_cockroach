import pytest

pytest.importorskip("playwright")

from hands.pw.dom import parse_snapshot  # noqa: E402

SNAP = '''- generic [active] [ref=e1] [box=8,8,1264,704]:
  - button "普通按钮" [ref=e2] [box=8,10,23,21]: x
  - text: 搜索
  - textbox "搜索" [ref=e3] [box=67,10,177,21]
  - link "链接0" [ref=e4] [cursor=pointer] [box=8,8,41,21]:
    - /url: /p0
  - button [disabled] [ref=e5] [box=8,82,50,23]
  - button "说 \\"你好\\"" [ref=e6] [box=1,2,3,4]
  - 'button "时间: 3 点" [ref=e7] [box=1,2,3,4]'
  - iframe [ref=e8] [box=30,264,324,144]:
    - button "跨源按钮" [ref=f2e2] [box=40,25,69,23]
'''


def test_parse_basic():
    es = {e.ref: e for e in parse_snapshot(SNAP)}
    assert es["e2"].role == "button" and es["e2"].name == "普通按钮" and es["e2"].frame_key == ""
    assert es["e3"].role == "textbox" and es["e3"].name == "搜索"
    assert es["e4"].role == "link" and es["e4"].name == "链接0"
    assert "e1" in es and es["e1"].role == "generic"


def test_disabled_and_unnamed():
    e = next(x for x in parse_snapshot(SNAP) if x.ref == "e5")
    assert e.disabled and e.name == ""


def test_escaped_and_quoted_names():
    es = {e.ref: e for e in parse_snapshot(SNAP)}
    assert es["e6"].name == '说 "你好"'
    assert es["e7"].name == "时间: 3 点"


def test_iframe_children_have_frame_key():
    es = {e.ref: e for e in parse_snapshot(SNAP)}
    assert es["e8"].role == "iframe"
    assert es["f2e2"].frame_key == "f2" and es["f2e2"].name == "跨源按钮"


def test_lines_without_ref_ignored():
    assert all(e.ref for e in parse_snapshot(SNAP))
    assert not any(e.role == "text" for e in parse_snapshot(SNAP))
