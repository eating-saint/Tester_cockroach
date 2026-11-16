from hands.confirm import ConfirmPolicy
from hands.dom import Element
from hands.geometry import Box
from hands.pagestate import PageState


def el(role, name, text="", pw=False, eid="e1"):
    return Element(eid, role, name, text, "button", None, None, Box(0, 0, 10, 10), True, True, True, is_password=pw)


def state(elements=(), url="https://example.com/", title="x"):
    return PageState(url, title, Box(0, 0, 100, 100), 1.0, list(elements), "")


P = ConfirmPolicy.from_config({}, default_on=True)


def test_default_off_for_other_drivers():
    p = ConfirmPolicy.from_config({}, default_on=False)
    assert p.check_click(el("button", "加入购物车")) is None
    assert ConfirmPolicy.from_config({"enabled": True}, default_on=False).enabled


def test_account_change():
    r = P.check_click(el("button", "加入购物车"))
    assert r.kind == "account_change" and r.hit == "加入购物车"
    assert P.check_click(el("link", "收藏")).kind == "account_change"
    assert P.check_click(el("button", "Save")).kind == "account_change"
    assert P.check_click(el("link", "Facebook")) is None          # 英文按词边界
    assert P.check_click(el("link", "Book a table")).hit == "book"


def test_submit_button():
    assert P.check_click(el("button", "提交")).kind == "form_submit"
    assert P.check_click(el("button", "Continue")).kind == "form_submit"
    assert P.check_click(el("button", "搜索")) is None
    assert P.check_click(el("link", "Next")) is None                # 翻页链接不算
    assert P.check_click(el("button", "继续阅读这篇很长的文章的全文内容")) is None


def test_enter():
    assert P.check_enter(el("textbox", "收货地址")).kind == "form_submit"
    assert P.check_enter(el("searchbox", "q")) is None
    assert P.check_enter(el("textbox", "搜索商品")) is None
    assert P.check_enter(None) is None


def test_login_hit():
    assert P.login_hit(state([el("textbox", "密码", pw=True)])).startswith("password")
    assert P.login_hit(state(url="https://login.taobao.com/member/login.jhtml")).startswith("url")
    assert P.login_hit(state(title="登录 - 某网站")).startswith("title")
    assert P.login_hit(state([el("textbox", "短信验证码")])).startswith("code_field")
    assert P.login_hit(state([el("button", "登录")], url="https://www.bing.com/search?q=x")) is None


def test_add_remove_words():
    p = ConfirmPolicy.from_config({"add_words": {"account": ["想吃"]}, "remove_words": {"account": ["收藏"]}}, True)
    assert p.check_click(el("button", "想吃")).hit == "想吃"
    assert p.check_click(el("link", "收藏")) is None
