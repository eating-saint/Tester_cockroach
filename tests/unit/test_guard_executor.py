"""动作执行器接入兜底拦截：命中时不动鼠标、直接返回 guard_blocked；未命中照常执行。"""
import asyncio

from hands.actions import ActionExecutor, ActionResult
from hands.dom import Element
from hands.geometry import Box
from hands.guard import Guard


class FakePage:
    def __init__(self, form_action=None, active_form=None):
        self._fa = form_action
        self._active = active_form
        self.clicks = []

    async def form_action(self, el):
        return self._fa

    async def evaluate(self, expr):
        return self._active

    async def mouse_click(self, x, y, ctrl=False):
        self.clicks.append((x, y))


def _el(name):
    return Element("e1", "button", name, name, "button", None, None, Box(0, 0, 10, 10), True, True, True)


def _executor(page, guard=None):
    ex = ActionExecutor(page, guard=guard)
    ex.ran = []

    async def fake_run(fn, *, measure=True, tool=""):
        ex.ran.append(tool)
        return ActionResult(True, True)
    ex._run = fake_run
    return ex


def test_click_blocked_by_word():
    page = FakePage()
    ex = _executor(page)
    r = asyncio.run(ex.click(_el("提交订单")))
    assert not r.ok and r.error.startswith("guard_blocked") and r.data["guard"]["matched"] == "提交订单"
    assert ex.ran == [] and page.clicks == []


def test_click_blocked_by_form_action():
    ex = _executor(FakePage(form_action="https://shop/checkout/submit"))
    r = asyncio.run(ex.click(_el("继续")))
    assert r.error.startswith("guard_blocked") and r.data["guard"]["where"] == "form_action"
    assert ex.ran == []


def test_normal_click_runs():
    ex = _executor(FakePage(form_action="https://shop/search"))
    r = asyncio.run(ex.click(_el("搜索")))
    assert r.ok and ex.ran == ["click"]


def test_open_tab_blocked():
    ex = _executor(FakePage())
    assert asyncio.run(ex.open_tab(_el("Buy now"))).error.startswith("guard_blocked")


def test_enter_in_checkout_form_blocked():
    ex = _executor(FakePage(active_form="https://shop/order/submit"))
    assert asyncio.run(ex.press("Enter")).error.startswith("guard_blocked")
    ex2 = _executor(FakePage(active_form="https://shop/search"))
    assert asyncio.run(ex2.press("Enter")).ok


def test_config_guard_used():
    ex = _executor(FakePage(), guard=Guard.from_config({"add_words": ["领取优惠"]}))
    assert asyncio.run(ex.click(_el("领取优惠"))).error.startswith("guard_blocked")
