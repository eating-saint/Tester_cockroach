import json

import pytest

from hands.decision import DecisionInvalid, decide_with_retry, validate_decision


def mk(tool, **args):
    return json.dumps({"decision": "call_tool", "reason": "r", "tool": tool, "arguments": args})


def test_valid_click():
    d = validate_decision(mk("click", element="e3"))
    assert d.tool == "click" and d.arguments == {"element": "e3"}


@pytest.mark.parametrize("raw", [
    "not json",
    json.dumps([1, 2]),
    json.dumps({"decision": "call_tool", "tool": "click", "arguments": {}}),       # 缺 reason
    mk("hack", x="1"),                                                              # 未注册
    mk("click"),                                                                    # 缺参数
    mk("click", element=3),                                                         # 类型错
    mk("click", element="e1", extra="x"),                                           # 多余参数
    json.dumps({"decision": "wait", "reason": "r", "tool": "click", "arguments": {"element": "e1"}}),
])
def test_invalid(raw):
    with pytest.raises(DecisionInvalid):
        validate_decision(raw)


def test_scroll_forms():
    validate_decision(mk("scroll", to_bottom=True))
    validate_decision(mk("scroll", direction="down", pixels=300))
    with pytest.raises(DecisionInvalid):
        validate_decision(mk("scroll", direction="left", pixels=3))
    with pytest.raises(DecisionInvalid):
        validate_decision(mk("scroll"))


class FakeDecider:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    async def decide(self, inp):
        self.calls += 1
        return self.outputs.pop(0)


async def test_retry_succeeds_on_third():
    d = FakeDecider(["bad", mk("hack"), mk("back")])
    dec, raws, tries = await decide_with_retry(d, None)
    assert dec.tool == "back" and tries == 3 and len(raws) == 3


async def test_retry_gives_up():
    d = FakeDecider(["bad"] * 5)
    dec, raws, tries = await decide_with_retry(d, None)
    assert dec is None and tries == 3 and d.calls == 3
