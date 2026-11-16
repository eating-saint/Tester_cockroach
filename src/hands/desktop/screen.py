"""代理窗口截图与"截图稳定"判断。

截图用 PrintWindow(PW_RENDERFULLCONTENT)：只拿 Chrome 窗口自己的像素，被别的窗口挡住也不会截进去（spec N5）。
没有加载信号（probe.md 第 2 条），稳定 = 连续截图基本不变且"还在等提交"为假，持续 stable_ms。
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import time
from typing import Awaitable, Callable

from ..page import WaitResult
from .coords import Frame


def crop_content(img, hwnd_rect: tuple[int, int, int, int], frame: Frame):
    """整窗图像 → 网页内容区。"""
    l, t, _, _ = hwnd_rect
    x0, y0 = frame.content_left - l, frame.content_top - t
    return img.crop((x0, y0, x0 + frame.content_w, y0 + frame.content_h))


def capture_image(hwnd: int, frame: Frame):
    from . import win32
    img = win32.print_window(hwnd)
    if img is None:
        raise OSError("PrintWindow 失败：代理窗口可能已关闭或最小化")
    return crop_content(img, win32.window_rect(hwnd), frame)


def to_png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _small(img, size=(64, 40)):
    return img.convert("L").resize(size)


def diff(a, b) -> float:
    """两张图缩成 64×40 灰度后的平均绝对差（0–1）。"""
    sa, sb = _small(a), _small(b)
    pa, pb = sa.tobytes(), sb.tobytes()
    return sum(abs(x - y) for x, y in zip(pa, pb)) / (255 * len(pa))


def visual_hash(img) -> str:
    return hashlib.sha1(_small(img, (16, 10)).tobytes()).hexdigest()[:16]


async def wait_stable(grab: Callable[[], Awaitable], pending: Callable[[], Awaitable[bool]],
                      stable_ms: int, timeout_s: float, interval_s: float = 0.15,
                      threshold: float = 0.01) -> WaitResult:
    """grab() 返回一张图；pending() 为真表示还在等新页面提交。"""
    t0 = time.monotonic()
    prev = await grab()
    stable_since = None
    while True:
        await asyncio.sleep(interval_s)
        cur = await grab()
        still = diff(prev, cur) < threshold and not await pending()
        now = time.monotonic()
        if still:
            stable_since = stable_since or now
            if (now - stable_since) * 1000 >= stable_ms:
                return WaitResult("ok", int((now - t0) * 1000))
        else:
            stable_since = None
        prev = cur
        if now - t0 > timeout_s:
            return WaitResult("timeout", int((now - t0) * 1000), ["（桌面驱动无网络请求列表：页面截图一直在变或新页面未提交）"])
