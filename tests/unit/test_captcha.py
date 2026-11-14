import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))
from helpers import el, page  # noqa: E402

from hands import captcha  # noqa: E402
from hands.config import DEFAULT_CAPTCHA_MARKERS as M  # noqa: E402


def test_url_hit():
    assert captcha.detect(page(url="https://x.com/Captcha?x"), M) == "url:captcha"


def test_text_hit_chinese():
    assert captcha.detect(page(body="请完成人机验证后继续"), M) == "text:人机验证"


def test_iframe_hit():
    f = el("e0", role="iframe", name="recaptcha", tag="iframe")
    assert captcha.detect(page([f]), M).startswith("iframe:")


def test_hidden_zero_size_iframe_not_hit():
    """新浪文章页：广告带进来的 0×0 不可见 recaptcha/api2/aframe 不是验证框（g02 误报转人工）。"""
    f = el("e0", role="iframe", name="", tag="iframe", href="https://www.google.com/recaptcha/api2/aframe")
    f.visible = False
    f.box = SimpleNamespace(x=0, y=0, width=0, height=0)
    assert captcha.detect(page([f]), M) is None
    f.visible = True
    assert captcha.detect(page([f]), M) is None      # 可见但 0 尺寸也不算
    f.box = SimpleNamespace(x=10, y=10, width=304, height=78)
    assert captcha.detect(page([f]), M).startswith("iframe:")


def test_plain_word_in_text_not_hit():
    assert captcha.detect(page(body="Coding challenge results"), M) is None


class FakePage:
    def __init__(self, log):
        self.log = log

    async def navigate(self, url):
        self.log.append(("navigate", url))


class FakeBrowser:
    def __init__(self):
        self.headless = True
        self.log = []
        self.page = None

    async def relaunch(self, headless):
        self.headless = headless
        self.log.append(("relaunch", headless))

    async def new_page(self):
        self.page = FakePage(self.log)
        return self.page


async def test_handoff_order(tmp_path, monkeypatch):
    async def fake_capture(page, full_page=False):
        return b"png"
    monkeypatch.setattr("hands.screenshot.capture", fake_capture)
    b = FakeBrowser()
    b.page = FakePage(b.log)
    captcha_log = []
    await captcha.handoff(b, "http://u", tmp_path, input_fn=lambda: b.log.append(("enter",)), log=captcha_log.append)
    assert b.log == [("relaunch", False), ("navigate", "http://u"), ("enter",),
                     ("relaunch", True), ("navigate", "http://u")]
    assert (tmp_path / "captcha.png").exists()


async def test_handoff_eof_falls_back_to_polling(tmp_path, monkeypatch):
    """控制台读不到输入（EOFError）时不崩溃，改为轮询，验证码消失后继续。"""
    async def fake_capture(page, full_page=False):
        return b"png"
    monkeypatch.setattr("hands.screenshot.capture", fake_capture)
    polled = []

    async def fake_wait(browser, markers=None, log=print, **kw):
        polled.append(markers)
        return True
    monkeypatch.setattr(captcha, "wait_cleared", fake_wait)

    def no_console():
        raise EOFError
    b = FakeBrowser()
    b.page = FakePage(b.log)
    await captcha.handoff(b, "http://u", tmp_path, input_fn=no_console, log=lambda m: None, markers=["captcha"])
    assert polled == [["captcha"]]
    assert b.log[-2:] == [("relaunch", True), ("navigate", "http://u")]


def test_press_and_hold_block_page_hit():
    from hands.config import DEFAULT_CAPTCHA_MARKERS
    st = SimpleNamespace(url="https://www.walmart.com/blocked?url=Lw==", elements=[],
                         body_text="Robot or human?\nActivate and hold the button to confirm that you’re human.")
    assert captcha.detect(st, DEFAULT_CAPTCHA_MARKERS) == "text:robot or human"


def test_signature_drops_fragment():
    s1 = captcha.signature(page(url="https://a.com/p?x=1#top"), "iframe:turnstile")
    s2 = captcha.signature(page(url="https://a.com/p?x=1"), "iframe:turnstile")
    assert s1 == s2 == ("https://a.com/p?x=1", "iframe:turnstile")


def test_cloudflare_iframe_hit():
    f = el("e0", role="iframe", name="", tag="iframe")
    f.href = "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/turnstile/if/ov2"
    assert captcha.detect(page([f]), M).startswith("iframe:")


def test_normal_page():
    assert captcha.detect(page([el("e0", name="首页")], body="欢迎点餐"), M) is None
