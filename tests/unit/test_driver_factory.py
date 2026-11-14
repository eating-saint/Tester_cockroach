import builtins

import pytest

from hands.browser import BrowserManager
from hands.config import load_config
from hands.driver import DriverUnavailable, create_browser


def test_default_is_cdp():
    cfg = load_config(None)
    assert cfg.driver == "cdp"
    assert isinstance(create_browser(cfg), BrowserManager)


def test_override_and_invalid():
    assert load_config(None, driver="playwright").driver == "playwright"
    with pytest.raises(ValueError):
        load_config(None, driver="foo")


def test_playwright_missing(monkeypatch):
    real_import = builtins.__import__

    def fake(name, *a, **kw):
        if name == "playwright" or name.startswith("playwright."):
            raise ImportError("no playwright")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake)
    with pytest.raises(DriverUnavailable) as ei:
        create_browser(load_config(None, driver="playwright"))
    assert "pip install -e .[pw]" in str(ei.value)
