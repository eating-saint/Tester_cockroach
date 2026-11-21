import asyncio
import sys
from pathlib import Path

import pytest

from panel.tunnel import INSTALL_HINT, Tunnel, find_exe

FAKE = str(Path(__file__).resolve().parents[1] / "panel" / "fake_cloudflared.py")


def fake(*extra):
    return [sys.executable, FAKE, *extra]


async def test_gets_url_and_stop_without_down():
    downs = []
    t = Tunnel(fake(), 8791, on_down=lambda: downs.append(1), log=lambda m: None)
    url = await t.start(10)
    assert url == "https://fake-test.trycloudflare.com" and t.running
    await t.stop()
    await asyncio.sleep(0.2)
    assert not t.running and downs == []


async def test_timeout_when_silent():
    t = Tunnel(fake("--silent"), 8791, log=lambda m: None)
    with pytest.raises(RuntimeError, match="没有拿到地址"):
        await t.start(1.5)
    assert not t.running


async def test_unexpected_exit_calls_down():
    downs = []
    t = Tunnel(fake("--exit-after", "0.8"), 8791, on_down=lambda: downs.append(1), log=lambda m: None)
    await t.start(10)
    for _ in range(50):
        await asyncio.sleep(0.1)
        if downs:
            break
    assert downs == [1] and t.url is None


async def test_missing_exe():
    assert find_exe("Z:/no/such/cloudflared.exe") is None
    with pytest.raises(RuntimeError, match="winget install Cloudflare.cloudflared"):
        await Tunnel(None, 8791).start()
    assert "winget" in INSTALL_HINT
