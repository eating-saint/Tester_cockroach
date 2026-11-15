"""兜底拦截：词与路径命中、增删清单、不误伤普通元素。"""
from hands.dom import Element
from hands.geometry import Box
from hands.guard import Guard


def _el(name="", text="", href=None, role="button"):
    return Element("e1", role, name, text, "button", href, None, Box(0, 0, 10, 10), True, True, True,
                   context="Spicy Ramen  $12.99  Buy now")


def test_words_zh_en():
    g = Guard()
    assert g.check(_el(name="Place Order")).matched == "place order"
    assert g.check(_el(text="  提交订单 ")).matched == "提交订单"
    assert g.check(_el(name="立即购买")).where == "name"


def test_paths():
    g = Guard()
    assert g.check(_el(name="继续"), form_action="https://shop.x/checkout/submit").where == "form_action"
    assert g.check(_el(name="Go", href="https://x/cart/checkout?step=1", role="link")).kind == "path"
    assert g.check(_el(name="Guide", href="https://x/checkoutguide", role="link")) is None


def test_not_over_blocking():
    g = Guard()
    assert g.check(_el(name="搜索")) is None
    assert g.check(_el(name="Spicy Ramen", href="https://x/item?id=3", role="link")) is None   # 卡片 context 里有 Buy now 也不拦
    assert g.check(_el(name="支付宝帮助", href="https://x/help/alipay", role="link")) is None
    assert g.check(None, None) is None


def test_config_add_remove():
    g = Guard.from_config({"add_words": ["领取优惠"], "remove_words": ["Place Order"]})
    assert g.check(_el(name="领取优惠")).matched == "领取优惠"
    assert g.check(_el(name="place order")) is None
    g2 = Guard.from_config({"remove_paths": ["/checkout"], "add_paths": ["/settle"]})
    assert g2.check(_el(name="继续"), form_action="https://x/checkout/submit") is None
    assert g2.check(_el(name="继续"), form_action="https://x/settle") is not None


def test_no_disable_switch():
    import inspect
    params = set(inspect.signature(Guard.from_config).parameters) | set(inspect.signature(Guard).parameters)
    assert not ({"enabled", "disable", "off"} & params)
    assert Guard.from_config({"enabled": False}).check(_el(name="Place order")) is not None
