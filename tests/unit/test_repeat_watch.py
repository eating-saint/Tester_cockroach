"""原地打转检测（ver1 调试问题 2）：连续同一个动作且页面没变化时计数，页面一变就清零。"""
from hands.runner import REPEAT_BLOCK, REPEAT_FAIL, RepeatWatch


class D:
    def __init__(self, tool, arguments):
        self.tool, self.arguments = tool, arguments


def test_counts_only_identical_and_ineffective():
    w = RepeatWatch()
    click = D("click", {"element": "e4"})
    assert w.check(click) == 1
    w.note(False)                       # 点了，页面没变
    assert w.check(click) == 2
    w.note(False)
    assert w.check(click) == REPEAT_BLOCK == 3     # 第 3 次就该被拦下


def test_effective_action_resets():
    w = RepeatWatch()
    click = D("click", {"element": "e4"})
    w.check(click)
    w.note(False)
    w.check(click)
    w.note(True)                        # 这次页面变了：不算原地打转
    assert w.check(click) == 1


def test_different_action_resets():
    w = RepeatWatch()
    w.check(D("click", {"element": "e4"}))
    w.note(False)
    assert w.check(D("click", {"element": "e5"})) == 1          # 换了元素
    w.note(False)
    assert w.check(D("type", {"element": "e5", "text": "x"})) == 1   # 换了工具


def test_blocked_steps_keep_counting_to_fail():
    """被拦下的那几步不会调用 note()，计数继续涨，到 REPEAT_FAIL 判失败。"""
    w = RepeatWatch()
    click = D("click", {"element": "e4"})
    n = 0
    for _ in range(REPEAT_FAIL):
        n = w.check(click)
        if n < REPEAT_BLOCK:
            w.note(False)
    assert n == REPEAT_FAIL


def test_none_decision_resets():
    w = RepeatWatch()
    click = D("click", {"element": "e4"})
    w.check(click)
    w.note(False)
    assert w.check(None) == 1
    assert w.check(click) == 1


def test_signature_ignores_key_order():
    w = RepeatWatch()
    assert w.check(D("type", {"element": "e1", "text": "a"})) == 1
    w.note(False)
    assert w.check(D("type", {"text": "a", "element": "e1"})) == 2
