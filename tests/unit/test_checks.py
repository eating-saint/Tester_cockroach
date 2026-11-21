import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from helpers import el, page  # noqa: E402

from hands import checks  # noqa: E402
from hands.checks import HandoffChecker, HandoffFailed, respect_interval  # noqa: E402
from hands.config import DEFAULT_CAPTCHA_MARKERS as M  # noqa: E402
from hands.pause import PauseGate  # noqa: E402

CFG = SimpleNamespace(captcha_markers=M)


class Login:
    enabled = True

    def __init__(self, hit):
        self.hit = hit

    def login_hit(self, state):
        return self.hit


def make(handoff_log, confirm=None):
    gate = PauseGate(watch_human=False)
    seen_reason = []

    async def handoff_fn(browser, url, sdir, input_fn, log):
        seen_reason.append(gate.paused.reason if gate.paused else None)
        handoff_log.append(url)
        return "page-after"
    c = HandoffChecker(None, CFG, confirm, gate, handoff_fn, log=lambda m: None)
    return c, gate, seen_reason


async def test_captcha_pauses_and_resumes(tmp_path):
    log = []
    c, gate, reason = make(log)
    st = page(url="https://x.com/captcha")
    hit = await c.detect(st, object())
    assert hit == "url:captcha"
    p, rec = await c.handle(hit, st, tmp_path)
    assert p == "page-after" and reason == ["handoff"] and not gate.is_paused
    assert rec["hit"] == hit and rec["url"] == st.url and c.records == [rec]
    assert gate.log[-1]["reason"] == "handoff"


async def test_iframe_handled_once():
    c, _, _ = make([])
    f = el("e0", role="iframe", name="recaptcha", tag="iframe")
    st = page([f], url="https://a.com/p")
    hit = await c.detect(st, object())
    assert hit.startswith("iframe:")
    await c.handle(hit, st, ".")
    assert await c.detect(st, object()) is None           # 同一验证框已处理：不重复转人工


async def test_login_and_pending_and_dialog():
    c, _, _ = make([], confirm=Login("password"))
    st = page(url="https://a.com/login")
    assert await c.detect(st, object()) == "login:password"
    await c.handle("login:password", st, ".")
    assert await c.detect(st, object()) is None
    c.set_pending("login:sms")
    assert await c.detect(st, object()) == "login:sms"
    assert await c.detect(st, object()) is None           # pending 只用一次

    class DlgPage:
        async def dialogs(self):
            return [{"title": "打印"}]
    assert await c.detect(st, DlgPage()) == "dialog:打印"


async def test_check_page_gives_up_after_three(tmp_path, monkeypatch):
    calls = []

    async def fake_state(p, d):
        return page(url="https://x.com/captcha")
    monkeypatch.setattr("hands.pagestate.build_page_state", fake_state)
    c, _, _ = make(calls)
    with pytest.raises(HandoffFailed):
        await c.check_page(object(), tmp_path)
    assert len(calls) == checks.MAX_HANDOFFS


async def test_check_page_clean(tmp_path):
    c, _, _ = make([])
    p, recs = await c.check_page("pg", tmp_path, state=page(url="https://a.com/"))
    assert p == "pg" and recs == []


async def test_respect_interval(monkeypatch):
    waits = []

    async def fake_sleep(s):
        waits.append(s)
    monkeypatch.setattr(checks.asyncio, "sleep", fake_sleep)
    last = {}
    await respect_interval(last, "https://www.bing.com/search?q=a", {"bing.com": 20}, log=lambda m: None)
    await respect_interval(last, "https://cn.bing.com/search?q=b", {"bing.com": 20}, log=lambda m: None)
    await respect_interval(last, "https://other.com/", {"bing.com": 20}, log=lambda m: None)
    assert len(waits) == 1 and 19 < waits[0] <= 20
