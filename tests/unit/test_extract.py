"""抽取回查：真值命中、全角与空白差异仍命中、编造值不命中、元素不存在不命中、正文回查。"""
from hands.dom import Element
from hands.geometry import Box
from hands.pagestate import PageState
from hands.vlm.extract import verify_extract


def _state():
    el = Element("e1", "link", "Spicy Ramen", "Spicy Ramen", "a", "http://x/item?id=3", None,
                 Box(0, 0, 1, 1), True, True, True, context="Spicy Ramen $12.99 ★ 4.7 25 min")
    return PageState("http://x/", "t", Box(0, 0, 1, 1), 1.0, [el], "价格：$12.99\n评分：4.7\n配送时间：25 min")


def test_grounded_in_element_and_context():
    rows = verify_extract([{"field": "标题", "value": "Spicy  Ramen", "element": "e1"},
                           {"field": "价格", "value": "＄12.99", "element": "e1"}], _state())
    assert rows[0]["grounded"] and rows[0]["where"] == "name"
    assert rows[1]["grounded"] and rows[1]["where"] == "context"


def test_body_lookup():
    rows = verify_extract([{"field": "评分", "value": "4.7"}, {"field": "配送", "value": "25 min"}], _state())
    assert all(r["grounded"] and r["where"] == "body" for r in rows)


def test_fabricated_values():
    rows = verify_extract([{"field": "价格", "value": "$9.99", "element": "e1"},
                           {"field": "价格", "value": "$9.99"},
                           {"field": "标题", "value": "Spicy Ramen", "element": "e7"},
                           {"field": "评分", "value": " "}], _state())
    assert [r["grounded"] for r in rows] == [False, False, False, False]
    assert rows[2]["note"] == "元素不存在" and rows[3]["note"] == "值为空"
