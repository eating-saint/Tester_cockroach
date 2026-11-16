"""低级鼠标 / 键盘钩子：区分本程序注入的输入和人工输入（spec F8）。

钩子回调只做分类和投递，不阻塞系统输入。dwExtraInfo == INJECT_MARK 的事件是本程序注入的；
其余一律按人工处理（包括其他软件的注入：宁可多停一次）。

自测：python -m hands.desktop.inputwatch --selftest
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes as wt

from . import win32

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_QUIT = 0x0012
LLMHF_INJECTED, LLKHF_INJECTED = 0x01, 0x10


def classify(injected_flag: bool, extra: int) -> str:
    """self = 本程序注入；human = 物理输入；other = 别的程序注入（按人工处理）。"""
    if extra == win32.INJECT_MARK:
        return "self"
    return "other" if injected_flag else "human"


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)
_u = win32.user32
_u.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
_u.SetWindowsHookExW.restype = wt.HHOOK
_u.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
_u.CallNextHookEx.restype = ctypes.c_ssize_t
_u.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
_u.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, ctypes.c_uint, ctypes.c_uint]
_u.PostThreadMessageW.argtypes = [wt.DWORD, ctypes.c_uint, wt.WPARAM, wt.LPARAM]


class InputWatch:
    """on_human(t_monotonic) 在 asyncio 循环线程里被调用（loop 为 None 时直接在钩子线程里调用）。"""

    def __init__(self, loop, on_human, min_gap_s: float = 0.05):
        self.loop = loop
        self.on_human = on_human
        self.min_gap_s = min_gap_s         # 人工输入投递节流：鼠标移动一秒上百个事件
        self.last_human = 0.0
        self.counts = {"self": 0, "human": 0, "other": 0}
        self._tid = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._last_post = 0.0
        self._anchor: tuple[int, int] | None = None   # 上次"有效"位置：本程序注入或人的有效移动
        self.last_info: dict | None = None
        # 回调对象必须一直被引用，否则会被回收导致崩溃
        self._mproc = HOOKPROC(self._mouse)
        self._kproc = HOOKPROC(self._key)

    JITTER_PX = 6                          # 鼠标静止时的微小抖动（光学鼠标、触控板）不算"人碰了"

    def _deliver(self, kind: str, event: str, detail: dict | None = None):
        self.counts[kind] += 1
        if kind == "self":
            return
        t = time.monotonic()
        self.last_human = t
        if t - self._last_post < self.min_gap_s:
            return
        self._last_post = t
        info = {"source": kind, "event": event, **(detail or {})}
        self.last_info = info
        if self.loop is None:
            self.on_human(t, info)
        else:
            try:
                self.loop.call_soon_threadsafe(self.on_human, t, info)
            except RuntimeError:            # 循环已关闭
                pass

    def _mouse(self, n, wp, lp):
        if n >= 0:
            s = ctypes.cast(lp, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            kind = classify(bool(s.flags & LLMHF_INJECTED), s.dwExtraInfo)
            x, y = s.pt.x, s.pt.y
            if kind == "self":
                self._anchor = (x, y)
                self.counts["self"] += 1
            elif wp == 0x0200:                 # WM_MOUSEMOVE：累计位移超过阈值才算人动了鼠标
                ax, ay = self._anchor or (x, y)
                if self._anchor is None:
                    self._anchor = (x, y)
                d = max(abs(x - ax), abs(y - ay))
                if d >= self.JITTER_PX:
                    self._anchor = (x, y)
                    self._deliver(kind, "mouse_move", {"px": int(d)})
                else:
                    self.counts["jitter"] = self.counts.get("jitter", 0) + 1
            else:                              # 点击、滚轮：立刻算
                self._anchor = (x, y)
                self._deliver(kind, {0x0201: "left_down", 0x0204: "right_down", 0x020A: "wheel",
                                     0x0207: "middle_down"}.get(wp, f"mouse_{wp:#x}"))
        return _u.CallNextHookEx(None, n, wp, lp)

    def _key(self, n, wp, lp):
        if n >= 0:
            s = ctypes.cast(lp, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            self._deliver(classify(bool(s.flags & LLKHF_INJECTED), s.dwExtraInfo), "key", {"vk": int(s.vkCode)})
        return _u.CallNextHookEx(None, n, wp, lp)

    def _run(self):
        self._tid = win32.kernel32.GetCurrentThreadId()
        hm = _u.SetWindowsHookExW(WH_MOUSE_LL, self._mproc, None, 0)
        hk = _u.SetWindowsHookExW(WH_KEYBOARD_LL, self._kproc, None, 0)
        self._ready.set()
        msg = wt.MSG()
        try:
            while _u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass
        finally:
            if hm:
                _u.UnhookWindowsHookEx(hm)
            if hk:
                _u.UnhookWindowsHookEx(hk)

    def start(self) -> "InputWatch":
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="hands-inputwatch", daemon=True)
            self._thread.start()
            self._ready.wait(3)
        return self

    def stop(self):
        if self._thread is not None and self._tid:
            _u.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            self._thread.join(2)
        self._thread = None


def _selftest():
    got = []
    w = InputWatch(None, lambda t, info=None: got.append(t)).start()
    time.sleep(0.3)
    x, y = 400, 400
    for i in range(5):
        win32.move_abs(x + i * 5, y)
        time.sleep(0.05)
    time.sleep(0.3)
    print(f"注入 5 次移动：self={w.counts['self']} human={w.counts['human']} 回调={len(got)}（期望回调 0）")
    print("请在 5 秒内动一下鼠标……")
    end = time.time() + 5
    while time.time() < end and not got:
        time.sleep(0.1)
    print(f"人工输入回调={len(got)}（期望 ≥1）")
    w.stop()


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
