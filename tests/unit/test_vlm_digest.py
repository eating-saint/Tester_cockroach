"""PageDigest：编号顺序、视口外挑选与计数、清单格式、编号换 id、正文摘要。"""
import io

from PIL import Image

from hands.dom import Element
from hands.geometry import Box
from hands.pagestate import PageState
from hands.vlm.digest import PageDigest, body_summary, clue_words

S = {"max_side": 1280, "offscreen_top_k": 2, "body_chars": 300}


def _png(w=1280, h=800):
    out = io.BytesIO()
    Image.new("RGB", (w, h), (250, 250, 250)).save(out, format="PNG")
    return out.getvalue()


def _el(i, x, y, name, inv=True, role="button", ctx="", enabled=True, href=None):
    return Element(f"e{i}", role, name, name, "button", href, None, Box(x, y, 60, 20), True, inv, enabled,
                   context=ctx)


def _state(els, occluded=()):
    return PageState("http://x/", "t", Box(0, 0, 1280, 800), 1.0, els, "", occluded=list(occluded))


def test_order_top_to_bottom_left_to_right():
    els = [_el(0, 300, 100, "C"), _el(1, 10, 103, "B"), _el(2, 500, 10, "A")]   # y=100 与 103 同一行带
    d = PageDigest.build(_state(els), _png(), {}, "", S)
    assert [d.to_id(n) for n in (1, 2, 3)] == ["e2", "e1", "e0"]
    assert d.to_id(4) is None and d.to_id("x") is None


def test_offscreen_selection_and_count():
    els = [_el(0, 10, 10, "搜索")]
    els += [_el(i, 10, 900 + i * 30, f"Item {i}", inv=False) for i in range(1, 5)]
    els.append(_el(9, 10, 2000, "Spicy Ramen", inv=False))
    d = PageDigest.build(_state(els), _png(), {"hints": ["spicy"]}, "", S)
    ids = [n.element_id for n in d.numbered]
    assert ids[0] == "e0" and ids[1] == "e9"          # 视口内在前；视口外命中线索词的优先
    assert len(ids) == 3 and d.offscreen_omitted == 3  # top_k=2，其余 3 个只计数
    assert "另有 3 个视口外元素未列出" in d.element_lines()


def test_line_format():
    els = [_el(0, 10, 10, "提交", enabled=False), _el(1, 10, 50, "详情", role="link", href="http://x/item?id=3",
                                                     ctx="Spicy Ramen $12.99 4.7 25 min 详情")]
    d = PageDigest.build(_state(els, occluded=["e1"]), _png(), {}, "", S)
    l1, l2 = d.numbered[0].line, d.numbered[1].line
    assert l1.startswith("1 | 按钮 | 提交") and "不可用" in l1
    assert "链接 http://x/item?id=3" in l2 and "被遮挡" in l2 and "所在块：Spicy Ramen" in l2
    assert d.numbered[1].occluded


def test_features_and_dict():
    d = PageDigest.build(_state([_el(0, 640, 400, "搜索")]), _png(), {}, "", S)
    f = d.stable_features("e0")
    assert f["role"] == "button" and f["rel_box"]["x"] == 0.5 and f["rel_box"]["y"] == 0.5
    assert d.to_dict()["numbered"][0]["features"]["name"] == "搜索"
    assert d.scale == 1.0 and len(d.labels) == 1


def test_scale_for_large_viewport():
    st = PageState("http://x/", "t", Box(0, 0, 1920, 1080), 2.0, [_el(0, 10, 10, "a")], "")
    d = PageDigest.build(st, _png(3840, 2160), {}, "", S)
    assert abs(d.scale - 1280 / 1920) < 1e-6
    assert Image.open(io.BytesIO(d.clean_png)).size == (1280, 720)


def test_body_summary_and_clues():
    text = "开头" + "x" * 1000 + "这里有 spicy 的菜" + "y" * 1000
    s = body_summary(text, ["spicy"], 300)
    assert "spicy" in s and len(s) <= 300
    assert body_summary("短文本", ["z"], 300) == "短文本"
    assert clue_words({"hints": ["spicy"]}, "搜索 spicy，进入第一个商品")[:3] == ["spicy", "搜索", "进入第一个商品"]
