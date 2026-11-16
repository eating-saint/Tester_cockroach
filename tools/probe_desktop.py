"""桌面驱动先行验证（docs/desktop/plan.md「先行验证」6 项）。

python tools/probe_desktop.py --item all|1|2|3|4|5|6
运行期间会真实移动鼠标、打开 Chrome 窗口：请勿触碰电脑。结论打印到终端并写入 runs/probe/probe.json。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import sys
import threading
import time
from ctypes import wintypes as wt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "fixtures"))

from hands.desktop import win32  # noqa: E402

OUT = ROOT / "runs" / "probe"
OUT.mkdir(parents=True, exist_ok=True)
RESULT: dict = {}
PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"


def uia_mod():
    import comtypes.client
    comtypes.client.GetModule("UIAutomationCore.dll")
    from comtypes.gen import UIAutomationClient as U
    return U


class Probe:
    def __init__(self):
        import comtypes.client
        win32.set_dpi_aware()
        self.U = uia_mod()
        self.uia = comtypes.client.CreateObject(self.U.CUIAutomation8, interface=self.U.IUIAutomation)
        self.progid, self.exe = win32.default_browser()
        print(f"默认浏览器: {self.progid} {self.exe}")
        before = win32.chrome_windows()
        win32.launch_new_window(self.exe, BASE + "/f01_static/index.html")
        self.hwnd = win32.wait_new_window(before)
        if not self.hwnd:
            raise SystemExit("没有等到新 Chrome 窗口")
        win32.place(self.hwnd, (60, 40, 1280, 900))
        win32.focus(self.hwnd)
        time.sleep(2.0)
        print(f"代理窗口 hwnd={self.hwnd} 标题={win32.window_text(self.hwnd)!r} dpi={win32.dpi_of(self.hwnd)}")

    # ---------- UIA 工具 ----------
    def root(self):
        return self.uia.ElementFromHandle(self.hwnd)

    def document(self, timeout=8.0):
        U = self.U
        cond = self.uia.CreateAndCondition(
            self.uia.CreatePropertyCondition(U.UIA_ControlTypePropertyId, U.UIA_DocumentControlTypeId),
            self.uia.CreatePropertyCondition(U.UIA_IsOffscreenPropertyId, False))
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                d = self.root().FindFirst(U.TreeScope_Descendants, cond)
                if d:
                    return d
            except Exception:
                pass
            time.sleep(0.3)
        return None

    def cache(self, *pids):
        cr = self.uia.CreateCacheRequest()
        for p in pids:
            cr.AddProperty(p)
        return cr

    def read_all(self, elem, scope=None):
        U = self.U
        pids = [U.UIA_ControlTypePropertyId, U.UIA_NamePropertyId, U.UIA_BoundingRectanglePropertyId,
                U.UIA_AriaRolePropertyId, U.UIA_IsOffscreenPropertyId, U.UIA_IsEnabledPropertyId,
                U.UIA_IsPasswordPropertyId, U.UIA_ClassNamePropertyId, U.UIA_ValueValuePropertyId]
        arr = elem.FindAllBuildCache(scope or U.TreeScope_Subtree, self.uia.CreateTrueCondition(), self.cache(*pids))
        rows = []
        for i in range(arr.Length):
            e = arr.GetElement(i)
            r = e.CachedBoundingRectangle
            rows.append({"ct": e.CachedControlType, "name": e.CachedName, "rect": (r.left, r.top, r.right, r.bottom),
                         "aria": e.GetCachedPropertyValue(U.UIA_AriaRolePropertyId),
                         "off": e.GetCachedPropertyValue(U.UIA_IsOffscreenPropertyId),
                         "pw": e.GetCachedPropertyValue(U.UIA_IsPasswordPropertyId),
                         "cls": e.CachedClassName, "value": e.GetCachedPropertyValue(U.UIA_ValueValuePropertyId),
                         "_e": e})
        return rows

    def navigate(self, url):
        win32.focus(self.hwnd)
        win32.chord("Ctrl", "L")
        time.sleep(0.15)
        win32.text(url)
        win32.key(win32.VK["Enter"], True)
        win32.key(win32.VK["Enter"], False)
        time.sleep(2.0)


def item1(p: Probe):
    print("\n== 1. 无障碍树开启与读取耗时 ==")
    t0 = time.monotonic()
    doc = p.document()
    t_doc = time.monotonic() - t0
    if not doc:
        RESULT["1"] = {"ok": False, "why": "找不到 Document"}
        print("找不到 Document 控件")
        return
    r = doc.CurrentBoundingRectangle
    times, counts = [], []
    for _ in range(3):
        t = time.monotonic()
        rows = p.read_all(doc)
        times.append(round(time.monotonic() - t, 3))
        counts.append(len(rows))
    print(f"Document 位置框 {(r.left, r.top, r.right, r.bottom)}，找 Document 用时 {t_doc:.2f}s")
    print(f"读取 3 次：元素数 {counts}，耗时 {times}")
    for row in rows[:20]:
        print(f"  ct={row['ct']} aria={row['aria']!r} name={row['name'][:30]!r} rect={row['rect']} cls={row['cls']!r}")
    RESULT["1"] = {"ok": counts[-1] >= 10, "doc_rect": (r.left, r.top, r.right, r.bottom), "find_doc_s": round(t_doc, 2),
                   "counts": counts, "times_s": times}


def item2_3(p: Probe):
    U = p.U
    print("\n== 2. 滚动位置 ==")
    p.navigate(BASE + "/f08_lazy/index.html")
    doc = p.document()
    res = {}
    try:
        sp = doc.GetCurrentPattern(U.UIA_ScrollPatternId).QueryInterface(U.IUIAutomationScrollPattern)
        before = (sp.CurrentVerticalScrollPercent, sp.CurrentVerticalViewSize)
        r = doc.CurrentBoundingRectangle
        win32.move_abs((r.left + r.right) // 2, (r.top + r.bottom) // 2)
        for _ in range(3):
            win32.wheel(-120)
            time.sleep(0.1)
        time.sleep(0.8)
        after = (sp.CurrentVerticalScrollPercent, sp.CurrentVerticalViewSize)
        res = {"ok": after[0] != before[0] and after[0] >= 0, "before": before, "after": after}
    except Exception as e:
        res = {"ok": False, "why": f"{type(e).__name__}: {e}"}
    print(res)
    RESULT["2"] = res

    print("\n== 3. 链接地址 ==")
    p.navigate(BASE + "/f11_nav/index.html")
    doc = p.document()
    rows = [r for r in p.read_all(doc) if r["ct"] == U.UIA_HyperlinkControlTypeId]
    out = []
    for r in rows[:8]:
        legacy = None
        try:
            lp = r["_e"].GetCurrentPattern(U.UIA_LegacyIAccessiblePatternId).QueryInterface(
                U.IUIAutomationLegacyIAccessiblePattern)
            legacy = lp.CurrentValue
        except Exception:
            pass
        out.append({"name": r["name"], "value": r["value"], "legacy": legacy})
        print(f"  {r['name']!r}: Value={r['value']!r} Legacy={legacy!r}")
    full = [o for o in out if (o["value"] or "").startswith("http")]
    RESULT["3"] = {"ok": bool(out) and len(full) == len(out), "links": out}


def item4(p: Probe):
    print("\n== 4. PrintWindow 截图 ==")
    import tkinter as tk
    p.navigate(BASE + "/f01_static/index.html")
    img = win32.print_window(p.hwnd)
    if img is None:
        RESULT["4"] = {"ok": False, "why": "PrintWindow 失败"}
        print("PrintWindow 失败")
        return
    img.save(OUT / "printwindow.png")
    px = img.convert("L").get_flattened_data()
    nonblack = sum(1 for v in px if v > 16) / len(px)
    # 用一个红色置顶窗口压在 Chrome 内容区上，再截一次
    l, t, r, b = win32.window_rect(p.hwnd)
    holder = {}
    root = tk.Tk()                       # 主线程里跑：Tk 不能在子线程退出
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="#ff0000")
    root.geometry(f"400x300+{l + 300}+{t + 300}")

    def grab():
        holder["img"] = win32.print_window(p.hwnd)
        root.destroy()
    root.after(1500, grab)
    root.mainloop()
    img2 = holder["img"]
    img2.save(OUT / "printwindow_covered.png")
    crop = img2.crop((300, 300, 700, 600)).convert("RGB")
    red = sum(1 for (R, G, B) in crop.get_flattened_data() if R > 200 and G < 60 and B < 60) / (400 * 300)
    res = {"ok": nonblack > 0.5 and red < 0.05, "nonblack_ratio": round(nonblack, 3), "red_ratio_when_covered": round(red, 3),
           "size": img.size}
    print(res)
    RESULT["4"] = res


def item5(p: Probe):
    print("\n== 5. 低级钩子读注入标记 ==")
    WH_MOUSE_LL, WM_QUIT = 14, 0x0012

    class MSLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD), ("time", wt.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]
    HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)
    u = win32.user32
    u.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
    u.SetWindowsHookExW.restype = wt.HHOOK
    u.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
    u.CallNextHookEx.restype = ctypes.c_ssize_t
    events = []
    state = {}

    @HOOKPROC
    def proc(n, wp, lp):
        if n >= 0:
            s = ctypes.cast(lp, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            events.append((s.flags, s.dwExtraInfo))
        return u.CallNextHookEx(None, n, wp, lp)

    def loop():
        state["tid"] = win32.kernel32.GetCurrentThreadId()
        state["hook"] = u.SetWindowsHookExW(WH_MOUSE_LL, proc, None, 0)
        msg = wt.MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        u.UnhookWindowsHookEx(state["hook"])
    th = threading.Thread(target=loop, daemon=True)
    th.start()
    time.sleep(0.5)
    l, t, r, b = win32.window_rect(p.hwnd)
    for i in range(5):
        win32.move_abs(l + 200 + i * 10, t + 300)
        time.sleep(0.05)
    time.sleep(0.3)
    u.PostThreadMessageW(state["tid"], WM_QUIT, 0, 0)
    th.join(2)
    marked = [e for e in events if e[1] == win32.INJECT_MARK]
    injected_flag = [e for e in marked if e[0] & 0x01]
    res = {"ok": len(marked) >= 5, "events": len(events), "marked": len(marked), "with_injected_flag": len(injected_flag),
           "note": "人工输入一侧（无标记）留到 d08 由用户亲手验证"}
    print(res)
    RESULT["5"] = res


def item6(p: Probe):
    U = p.U
    print("\n== 6. 浏览器界面类名 ==")
    p.navigate(BASE + "/f01_static/index.html")
    rows = p.read_all(p.root(), U.TreeScope_Descendants)
    want = ("OmniboxViewViews", "ReloadButton", "Tab", "TabStrip", "TabStripRegionView")
    found = {}
    for r in rows:
        if r["cls"] in want:
            found.setdefault(r["cls"], []).append({"name": r["name"], "ct": r["ct"], "value": r["value"], "rect": r["rect"]})
    for k, v in found.items():
        print(f"  {k}: {v[:3]}")
    # 加载中读 ReloadButton 名称：导航到 4 秒延迟接口
    win32.focus(p.hwnd)
    win32.chord("Ctrl", "L")
    time.sleep(0.15)
    win32.text(BASE + "/api/delay?ms=4000&data=x")
    win32.key(win32.VK["Enter"], True)
    win32.key(win32.VK["Enter"], False)
    time.sleep(1.0)
    loading_name = None
    for r in p.read_all(p.root(), U.TreeScope_Descendants):
        if r["cls"] == "ReloadButton":
            loading_name = r["name"]
    time.sleep(4.0)
    idle_name = None
    for r in p.read_all(p.root(), U.TreeScope_Descendants):
        if r["cls"] == "ReloadButton":
            idle_name = r["name"]
    print(f"  ReloadButton 加载中={loading_name!r} 加载完={idle_name!r}")
    others = sorted({r["cls"] for r in rows if r["cls"]})
    RESULT["6"] = {"ok": "OmniboxViewViews" in found and "ReloadButton" in found and loading_name != idle_name,
                   "found": found, "reload_loading": loading_name, "reload_idle": idle_name, "classes": others[:80]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--item", default="all")
    a = ap.parse_args()
    import server
    srv = server.start(PORT)
    p = Probe()
    items = a.item.split(",") if a.item != "all" else ["1", "2", "4", "5", "6"]
    try:
        for it in items:
            {"1": item1, "2": item2_3, "3": item2_3, "4": item4, "5": item5, "6": item6}[it](p)
    finally:
        srv.stop()
        def clean(o):
            if isinstance(o, dict):
                return {k: clean(v) for k, v in o.items() if k != "_e"}
            if isinstance(o, (list, tuple)):
                return [clean(v) for v in o]
            return o if isinstance(o, (str, int, float, bool, type(None))) else str(o)
        (OUT / "probe.json").write_text(json.dumps(clean(RESULT), ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n结果写入 {OUT / 'probe.json'}；代理窗口保留（hwnd={p.hwnd}）")


if __name__ == "__main__":
    main()
