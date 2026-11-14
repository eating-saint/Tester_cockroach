from hands.status import LiveStatus


def test_events_and_unsubscribe():
    s = LiveStatus()
    got = []
    cancel = s.subscribe(lambda name, p: got.append(name))
    s.set_wait("load", 3)
    assert s.wait.what == "load" and s.wait.to_dict()["timeout_ms"] == 3000
    s.clear_wait()
    s.set_phase("CHECK")
    s.page_changed()
    s.action_done({"tool": "click"})
    assert got == ["wait", "wait", "phase", "page_changed", "action_done"]
    assert s.page_epoch == 1 and s.last_action == {"tool": "click"}
    cancel()
    s.set_phase(None)
    assert len(got) == 5


def test_callback_exception_swallowed():
    s = LiveStatus()
    seen = []

    def bad(name, p):
        raise RuntimeError("boom")
    s.subscribe(bad)
    s.subscribe(lambda n, p: seen.append(n))
    s.set_wait("think")          # 不应抛出
    assert seen == ["wait"]
