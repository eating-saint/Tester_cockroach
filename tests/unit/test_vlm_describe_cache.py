"""描述缓存：五种重新描述的条件各一条；都不满足时复用。"""
from hands.dom import Element
from hands.geometry import Box
from hands.pagestate import PageState
from hands.vlm.describe_cache import DescribeCache, PageDescription

S = {"redescribe_scroll": 1.0, "redescribe_change": 0.5}


def _state(url="http://x/a", names=("a", "b", "c", "d"), scroll_y=0.0):
    els = [Element(f"e{i}", "button", n, n, "button", None, None, Box(0, 0, 1, 1), True, True, True)
           for i, n in enumerate(names)]
    return PageState(url, "t", Box(0, scroll_y, 1280, 800), 1.0, els, "")


def _cache():
    c = DescribeCache(S)
    c.store(_state(), PageDescription())
    return c


def test_first_time():
    assert DescribeCache(S).need(_state(), None) == (True, "首次描述")


def test_reuse_when_nothing_changed():
    assert _cache().need(_state(names=("a", "b", "c", "d")), {"changes": ["dom"]}) == (False, "复用")


def test_url_change():
    assert _cache().need(_state(url="http://x/b"), None)[1] == "网址变化"


def test_last_step_opened_page():
    assert _cache().need(_state(), {"changes": ["new_tab"]})[1] == "上一步打开了新页面"


def test_structure_change():
    need, why = _cache().need(_state(names=("a", "x", "y", "z")), None)
    assert need and why.startswith("页面结构变化")


def test_scroll_more_than_one_screen():
    c = _cache()
    assert c.need(_state(scroll_y=700), None) == (False, "复用")
    assert c.need(_state(scroll_y=1200), None) == (True, "滚动超过一屏")


def test_description_text():
    d = PageDescription(regions=[{"position": "顶部", "kind": "导航栏", "note": "首页、购物车"}], overlay=True)
    assert "顶部｜导航栏" in d.text() and "有弹窗遮挡" in d.text()
    assert PageDescription().text() == "（没有页面描述）"
