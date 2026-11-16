import itertools

from PIL import Image

from hands.desktop.coords import Frame
from hands.desktop.screen import crop_content, diff, visual_hash, wait_stable


def test_diff_and_hash():
    a = Image.new("RGB", (200, 100), "white")
    b = Image.new("RGB", (200, 100), "black")
    assert diff(a, a) == 0
    assert abs(diff(a, b) - 1) < 1e-9
    assert visual_hash(a) == visual_hash(a.copy()) != visual_hash(b)


def test_crop():
    img = Image.new("RGB", (300, 200), "red")
    img.paste((0, 0, 255), (10, 50, 290, 190))
    c = crop_content(img, (100, 100, 400, 300), Frame(110, 150, 280, 140, 1.0))
    assert c.size == (280, 140) and c.getpixel((0, 0)) == (0, 0, 255)


async def _no():
    return False


async def test_wait_stable_ok_and_timeout():
    white = Image.new("RGB", (50, 50), "white")

    async def same():
        return white
    r = await wait_stable(same, _no, stable_ms=200, timeout_s=3, interval_s=0.05)
    assert r.status == "ok"

    cyc = itertools.cycle([Image.new("RGB", (50, 50), c) for c in ("white", "black")])

    async def flicker():
        return next(cyc)
    r = await wait_stable(flicker, _no, stable_ms=200, timeout_s=0.5, interval_s=0.05)
    assert r.status == "timeout" and "无网络请求列表" in r.pending_urls[0]
