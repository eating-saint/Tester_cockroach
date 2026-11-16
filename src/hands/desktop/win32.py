"""Windows 底层：DPI、窗口、SendInput 注入、默认浏览器、对话框。只用 ctypes，不新增依赖。

坐标一律是屏幕物理像素（进程需先 set_dpi_aware()）。
每个注入事件的 dwExtraInfo 填 INJECT_MARK，低级钩子据此区分"本程序注入"和"人工输入"。
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes as wt

if sys.platform != "win32":                      # 让非 Windows 上 import 时给出清楚的错误
    raise ImportError("hands.desktop 只支持 Windows")

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

INJECT_MARK = 0x48414E44          # "HAND"
ULONG_PTR = ctypes.c_size_t

# ---------------------------------------------------------------- DPI
def set_dpi_aware() -> bool:
    """Per-Monitor V2；已设置过（或系统不支持）时返回 False，不抛异常。"""
    try:
        return bool(user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)))
    except Exception:
        return False


def dpi_of(hwnd: int) -> int:
    try:
        return int(user32.GetDpiForWindow(wt.HWND(hwnd))) or 96
    except Exception:
        return 96


# ---------------------------------------------------------------- 窗口
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.MoveWindow.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.BOOL]
user32.IsWindow.argtypes = [wt.HWND]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsHungAppWindow.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindow.argtypes = [wt.HWND, ctypes.c_uint]
user32.GetWindow.restype = wt.HWND
user32.PostMessageW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]


def class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_text(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def window_pid(hwnd: int) -> int:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def top_windows() -> list[int]:
    out: list[int] = []

    def cb(h, _):
        out.append(int(h or 0))
        return True
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return [h for h in out if h]


def chrome_windows() -> set[int]:
    """可见、有标题的 Chrome 浏览器顶层窗口。"""
    return {h for h in top_windows()
            if class_name(h) == "Chrome_WidgetWin_1" and user32.IsWindowVisible(h) and window_text(h)}


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def alive(hwnd: int) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def hung(hwnd: int) -> bool:
    return bool(user32.IsHungAppWindow(hwnd))


def foreground() -> int:
    return int(user32.GetForegroundWindow() or 0)


def focus(hwnd: int) -> bool:
    """还原最小化并拉到前台。前台锁定时先发一次 Alt（注入，带标记）再试。"""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)              # SW_RESTORE
    if foreground() == hwnd:
        return True
    user32.SetForegroundWindow(hwnd)
    if foreground() != hwnd:
        key(VK["Alt"], True)
        key(VK["Alt"], False)
        user32.SetForegroundWindow(hwnd)
    time.sleep(0.05)
    return foreground() == hwnd


def place(hwnd: int, rect: tuple[int, int, int, int]) -> None:
    """rect = (x, y, w, h)，屏幕物理像素。"""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)
    user32.ShowWindow(hwnd, 1)                  # SW_SHOWNORMAL（取消最大化）
    x, y, w, h = rect
    user32.MoveWindow(hwnd, x, y, w, h, True)


def close_window(hwnd: int) -> None:
    user32.PostMessageW(hwnd, 0x0010, 0, 0)     # WM_CLOSE


# ---------------------------------------------------------------- 默认浏览器、启动
def default_browser() -> tuple[str, str]:
    """(ProgId, exe 路径)。读不到时 exe 为空串。"""
    import shlex
    import winreg
    progid = ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice") as k:
            progid = winreg.QueryValueEx(k, "ProgId")[0]
    except OSError:
        return "", ""
    exe = ""
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{progid}\shell\open\command") as k:
            cmd = winreg.QueryValueEx(k, "")[0]
        exe = shlex.split(cmd, posix=False)[0].strip('"')
    except (OSError, IndexError):
        pass
    return progid, exe


def launch_new_window(exe: str, url: str) -> None:
    """用默认 Profile 新开一个窗口；不加任何其他启动参数。"""
    subprocess.Popen([exe, "--new-window", url], close_fds=True)


def wait_new_window(before: set[int], timeout: float = 15.0) -> int | None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        new = chrome_windows() - before
        if new:
            return max(new)
        time.sleep(0.15)
    return None


def owned_dialogs(pid: int) -> list[int]:
    """同进程的可见系统对话框（文件保存、打印等）。"""
    return [h for h in top_windows()
            if class_name(h) == "#32770" and user32.IsWindowVisible(h) and window_pid(h) == pid]


# ---------------------------------------------------------------- SendInput
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]

MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x0001, 0x8000, 0x4000
MOUSEEVENTF_WHEEL = 0x0800
_BUTTONS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, KEYEVENTF_EXTENDEDKEY = 0x0002, 0x0004, 0x0001

VK = {"Enter": 0x0D, "Tab": 0x09, "Escape": 0x1B, "Backspace": 0x08, "Space": 0x20,
      "ArrowDown": 0x28, "ArrowUp": 0x26, "ArrowLeft": 0x25, "ArrowRight": 0x27,
      "PageDown": 0x22, "PageUp": 0x21, "End": 0x23, "Home": 0x24, "Delete": 0x2E,
      "Ctrl": 0x11, "Alt": 0x12, "Shift": 0x10, "F5": 0x74, "F6": 0x75,
      **{c: ord(c) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"}}
_EXTENDED = {0x28, 0x26, 0x25, 0x27, 0x22, 0x21, 0x23, 0x24, 0x2E}


def _send(*inputs: INPUT) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    n = user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if n != len(inputs):
        raise OSError(f"SendInput 只注入了 {n}/{len(inputs)} 个事件（可能被更高权限的窗口挡住）")


def _mouse(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> INPUT:
    return INPUT(0, _INPUTUNION(mi=MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, INJECT_MARK)))


def virtual_desktop() -> tuple[int, int, int, int]:
    return (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77),
            user32.GetSystemMetrics(78), user32.GetSystemMetrics(79))


def cursor_pos() -> tuple[int, int]:
    p = wt.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def move_abs(x: int, y: int) -> None:
    vx, vy, vw, vh = virtual_desktop()
    nx = int(round((x - vx) * 65535 / max(vw - 1, 1)))
    ny = int(round((y - vy) * 65535 / max(vh - 1, 1)))
    _send(_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny))


def button(kind: str, down: bool) -> None:
    d, u = _BUTTONS[kind]
    _send(_mouse(d if down else u))


def wheel(delta: int) -> None:
    """delta > 0 向上滚（远离用户），一格 = 120。"""
    _send(_mouse(MOUSEEVENTF_WHEEL, data=delta))


def key(vk: int, down: bool) -> None:
    flags = (0 if down else KEYEVENTF_KEYUP) | (KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED else 0)
    _send(INPUT(1, _INPUTUNION(ki=KEYBDINPUT(vk, 0, flags, 0, INJECT_MARK))))


def chord(*names: str) -> None:
    """组合键：按顺序按下，逆序松开。names 用 VK 表里的键名。"""
    vks = [VK[n] for n in names]
    for v in vks:
        key(v, True)
    for v in reversed(vks):
        key(v, False)


def text(s: str) -> None:
    """KEYEVENTF_UNICODE 逐个 UTF-16 单元注入，不受输入法影响。"""
    units = s.encode("utf-16-le")
    for i in range(0, len(units), 2):
        code = int.from_bytes(units[i:i + 2], "little")
        _send(INPUT(1, _INPUTUNION(ki=KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, INJECT_MARK))),
              INPUT(1, _INPUTUNION(ki=KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, INJECT_MARK))))


# ---------------------------------------------------------------- 截图
class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


user32.GetWindowDC.argtypes = [wt.HWND]
user32.GetWindowDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, ctypes.c_uint]
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p,
                            ctypes.c_void_p, ctypes.c_uint]


def print_window(hwnd: int):
    """PrintWindow(PW_RENDERFULLCONTENT) 取整窗图像（含被遮挡部分），返回 PIL.Image（RGB），失败返回 None。"""
    from PIL import Image
    l, t, r, b = window_rect(hwnd)
    w, h = r - l, b - t
    if w <= 0 or h <= 0:
        return None
    hdc = user32.GetWindowDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mdc, bmp)
    try:
        if not user32.PrintWindow(hwnd, mdc, 2):
            return None
        bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        if not gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0):
            return None
        return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1).copy()
    finally:
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(hwnd, hdc)
