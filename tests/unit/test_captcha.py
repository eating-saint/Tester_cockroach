import sys
from pathlib import Path

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


def test_normal_page():
    assert captcha.detect(page([el("e0", name="首页")], body="欢迎点餐"), M) is None
