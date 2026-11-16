"""动作执行器接入需确认、网格核对、密码框、打断（desktop T28）。"""
import asyncio

from hands.actions import ActionExecutor, ActionResult
from hands.confirm import ConfirmPolicy
from hands.dom import Element
from hands.geometry import Box
from hands.pause import Interrupted


class FakePage:
    def __init__(self, hit=None):
        self.hit = hit
        self.clicks = []

    async def form_action(self, el):
        return None

    async def evaluate(self, expr):
        return None

    async def element_at(self, x, y):
        return self.hit


def el(name, role="button", source="dom", pw=False):
    return Element("e1", role, name, name, "button", None, None, Box(0, 0, 10, 10), True, True, True,
                   source=source, is_password=pw)


def executor(page, answer=None, enabled=True):
    asked = []

    async def confirm_fn(req):
        asked.append(req)
        return answer
    ex = ActionExecutor(page, confirm=ConfirmPolicy.from_config({}, enabled), confirm_fn=confirm_fn)
    ex.ran, ex.asked = [], asked

    async def fake_run(fn, *, measure=True, tool=""):
        ex.ran.append(tool)
        return ActionResult(True, True)
    ex._run = fake_run
    return ex


def test_reject_no_click():
    ex = executor(FakePage(), answer=False)
    r = asyncio.run(ex.click(el("加入购物车")))
    assert not r.ok and r.error.startswith("用户拒绝") and ex.ran == []
    assert ex.confirms[-1]["allowed"] is False and ex.confirms[-1]["kind"] == "account_change"


def test_allow_once_then_ask_again():
    ex = executor(FakePage(), answer=True)
    assert asyncio.run(ex.click(el("收藏"))).ok and ex.ran == ["click"]
    asyncio.run(ex.click(el("收藏")))
    assert len(ex.asked) == 2                      # 允许一次只管这一次


def test_guard_before_confirm():
    ex = executor(FakePage(), answer=True)
    r = asyncio.run(ex.click(el("提交订单")))
    assert r.error.startswith("guard_blocked") and ex.asked == []


def test_grid_point():
    ex = executor(FakePage(hit=None), answer=False)
    r = asyncio.run(ex.click(el("格 3", role="grid_cell", source="grid")))
    assert not r.ok and ex.asked[0].kind == "unverifiable_point"
    ex2 = executor(FakePage(hit=el("Place order")), answer=True)
    assert asyncio.run(ex2.click(el("格 3", role="grid_cell", source="grid"))).error.startswith("guard_blocked")
    ex3 = executor(FakePage(hit=el("普通按钮")), answer=True)
    assert asyncio.run(ex3.click(el("格 3", role="grid_cell", source="grid"))).ok and ex3.asked == []


def test_password_never_typed():
    ex = executor(FakePage(), answer=True)
    r = asyncio.run(ex.type(el("密码", role="textbox", pw=True), "x"))
    assert not r.ok and r.data["handoff"] == "login" and ex.ran == []


def test_enter_in_textbox_needs_confirm():
    ex = executor(FakePage(), answer=False)
    ex._last_typed = el("收货地址", role="textbox")
    assert asyncio.run(ex.press("Enter")).error.startswith("用户拒绝")
    ex._last_typed = el("搜索", role="searchbox")
    assert asyncio.run(ex.press("Enter")).ok


def test_disabled_policy_is_old_behaviour():
    ex = executor(FakePage(), answer=False, enabled=False)
    assert asyncio.run(ex.click(el("加入购物车"))).ok and ex.asked == []


def test_interrupted_becomes_result():
    ex = ActionExecutor(FakePage())

    async def boom():
        raise Interrupted("human_input")
    r = asyncio.run(ex._run_inner(boom, measure=False))
    assert not r.ok and r.data["interrupted"] and r.error == "interrupted"
