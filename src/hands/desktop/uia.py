"""UIA 无障碍树读取（spec F4）。

- 网页内容 = 当前标签页 Document 控件的子树；Document 之外是 Chrome 自身界面，不进元素清单。
- 网址读 Document 的 ValuePattern（probe.md 第 1 条：地址栏省略 http://）。
- 行数据 → 元素清单的转换（rows_to_elements）是纯函数，可脱离真实桌面单测。
需要可选依赖 comtypes。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..dom import INTERACTIVE_ROLES, Element
from ..geometry import Box
from .coords import Frame

# UIA ControlType id
CT = {"Button": 50000, "CheckBox": 50002, "ComboBox": 50003, "Edit": 50004, "Hyperlink": 50005,
      "Image": 50006, "ListItem": 50007, "List": 50008, "Menu": 50009, "MenuItem": 50011,
      "RadioButton": 50013, "Slider": 50015, "Spinner": 50016, "Tab": 50018, "TabItem": 50019,
      "Text": 50020, "Group": 50026, "DataItem": 50029, "Document": 50030, "SplitButton": 50031,
      "Pane": 50033, "Table": 50036}
CT_ROLE = {50000: "button", 50002: "checkbox", 50003: "combobox", 50004: "textbox", 50005: "link",
           50011: "menuitem", 50013: "radio", 50015: "slider", 50016: "spinbutton", 50019: "tab",
           50031: "button"}
ROLE_TAG = {"link": "a", "button": "button", "textbox": "input", "searchbox": "input", "combobox": "select",
            "checkbox": "input", "radio": "input", "menuitem": "li", "tab": "div", "slider": "input",
            "spinbutton": "input", "switch": "input", "listbox": "select"}
BLOCK_CT = {50007, 50029, 50026}          # 列表项、数据项、分组：用来取元素所在"块"的文字
TEXT_CT = 50020


@dataclass
class UiaRef:
    runtime_id: tuple
    element: Any = field(default=None, repr=False, compare=False)   # IUIAutomationElement，仅本纪元有效
    epoch: int = 0


@dataclass
class UiaHit:
    role: str
    name: str
    runtime_id: tuple
    ancestors: list[tuple]
    rect: tuple
    in_document: bool


def _role(row: dict) -> str | None:
    aria = (row.get("aria") or "").lower()
    if aria in INTERACTIVE_ROLES:
        return aria
    return CT_ROLE.get(row.get("ct"))


def _contains(outer, inner_pt) -> bool:
    l, t, r, b = outer
    x, y = inner_pt
    return l <= x <= r and t <= y <= b


def rows_to_elements(rows: list[dict], frame: Frame, epoch: int = 0, context_chars: int = 200
                     ) -> tuple[list[Element], str]:
    """rows：Document 子树内按文档顺序的行，键 ct/name/rect/aria/off/enabled/pw/value/rid/_e。
    返回 (元素清单（id 已编号 e1..）, 正文)。"""
    vw, vh = frame.css_size
    viewport = Box(0, 0, vw, vh)
    texts = [r for r in rows if r.get("ct") == TEXT_CT and (r.get("name") or "").strip()]
    blocks = [r for r in rows if r.get("ct") in BLOCK_CT and r["rect"][2] > r["rect"][0]]
    elements: list[Element] = []
    for r in rows:
        role = _role(r)
        if role is None or r.get("ct") == CT["Document"]:
            continue
        if role == "textbox" and (r.get("aria") or "").lower() == "searchbox":
            role = "searchbox"
        rect = r["rect"]
        box = frame.box_to_css(rect)
        visible = box.width > 0 and box.height > 0
        name = " ".join((r.get("name") or "").split())[:200]
        value = r.get("value") or None
        pw = bool(r.get("pw"))
        href = None
        if role == "link":
            href, value = value, None
        if pw:
            value = None
        ctx = ""
        if visible and blocks:
            cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
            inside = [b for b in blocks if _contains(b["rect"], (cx, cy))]
            if inside:
                blk = min(inside, key=lambda b: (b["rect"][2] - b["rect"][0]) * (b["rect"][3] - b["rect"][1]))
                parts = [t["name"].strip() for t in texts if _contains(blk["rect"], (
                    (t["rect"][0] + t["rect"][2]) / 2, (t["rect"][1] + t["rect"][3]) / 2))]
                ctx = " ".join(parts)[:context_chars]
        elements.append(Element(
            id="", role=role, name=name, text=name, tag=ROLE_TAG.get(role, ""), href=href, value=value,
            box=box, visible=visible, in_viewport=visible and box.intersects(viewport),
            enabled=bool(r.get("enabled", True)), _ref=UiaRef(tuple(r.get("rid") or ()), r.get("_e"), epoch),
            context=ctx, source="uia", is_password=pw))
    for k, e in enumerate(elements, 1):
        e.id = f"e{k}"
    body = "\n".join(" ".join(t["name"].split()) for t in texts)
    return elements, body


# ---------------------------------------------------------------- COM 部分
_U = None


def _mod():
    global _U
    if _U is None:
        import comtypes.client
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as U
        _U = U
    return _U


class UiaUnavailable(RuntimeError):
    pass


class UiaReader:
    def __init__(self, hwnd: int, timeout_s: float = 5.0):
        import comtypes
        import comtypes.client
        try:
            comtypes.CoInitialize()
        except OSError:
            pass
        U = _mod()
        self.U = U
        self.hwnd = hwnd
        self.timeout_s = timeout_s
        self.uia = comtypes.client.CreateObject(U.CUIAutomation8, interface=U.IUIAutomation)
        self._true = self.uia.CreateTrueCondition()
        pids = [U.UIA_ControlTypePropertyId, U.UIA_NamePropertyId, U.UIA_BoundingRectanglePropertyId,
                U.UIA_AriaRolePropertyId, U.UIA_IsOffscreenPropertyId, U.UIA_IsEnabledPropertyId,
                U.UIA_IsPasswordPropertyId, U.UIA_ValueValuePropertyId, U.UIA_RuntimeIdPropertyId]
        self._cr = self.uia.CreateCacheRequest()
        for p in pids:
            self._cr.AddProperty(p)
        self._doc_cond = self.uia.CreateAndCondition(
            self.uia.CreatePropertyCondition(U.UIA_ControlTypePropertyId, U.UIA_DocumentControlTypeId),
            self.uia.CreatePropertyCondition(U.UIA_IsOffscreenPropertyId, False))
        self._tab_cond = self.uia.CreatePropertyCondition(U.UIA_ControlTypePropertyId, U.UIA_TabItemControlTypeId)
        self._tab_cr = self.uia.CreateCacheRequest()
        for p in (U.UIA_NamePropertyId, U.UIA_RuntimeIdPropertyId, U.UIA_SelectionItemIsSelectedPropertyId):
            self._tab_cr.AddProperty(p)
        self._doc = None
        self._walker = self.uia.ControlViewWalker

    # ---------------- Document ----------------
    def root(self):
        return self.uia.ElementFromHandle(self.hwnd)

    def document(self, refresh: bool = True):
        """当前标签页的 Document；refresh=False 时尽量用缓存。"""
        if self._doc is not None and not refresh:
            return self._doc
        end = time.monotonic() + self.timeout_s
        last = None
        while time.monotonic() < end:
            try:
                d = self.root().FindFirst(self.U.TreeScope_Descendants, self._doc_cond)
                if d:
                    self._doc = d
                    return d
            except Exception as e:      # COMError：窗口正在切换
                last = e
            time.sleep(0.2)
        raise UiaUnavailable(f"读不到网页内容区（Document 控件）{': ' + str(last) if last else ''}")

    def document_rect(self, doc=None) -> tuple[int, int, int, int]:
        r = (doc or self.document()).CurrentBoundingRectangle
        return r.left, r.top, r.right, r.bottom

    def doc_url(self, doc=None) -> str:
        try:
            vp = (doc or self.document(False)).GetCurrentPattern(self.U.UIA_ValuePatternId).QueryInterface(
                self.U.IUIAutomationValuePattern)
            return vp.CurrentValue or ""
        except Exception:
            return ""

    def scroll(self, doc=None) -> tuple[float, float] | None:
        """(VerticalScrollPercent, VerticalViewSize)；不可滚动时 percent 为 -1。"""
        try:
            sp = (doc or self.document(False)).GetCurrentPattern(self.U.UIA_ScrollPatternId).QueryInterface(
                self.U.IUIAutomationScrollPattern)
            return float(sp.CurrentVerticalScrollPercent), float(sp.CurrentVerticalViewSize)
        except Exception:
            return None

    # ---------------- 读取 ----------------
    def rows(self, doc) -> list[dict]:
        U = self.U
        arr = doc.FindAllBuildCache(U.TreeScope_Subtree, self._true, self._cr)
        out = []
        for i in range(arr.Length):
            e = arr.GetElement(i)
            r = e.CachedBoundingRectangle
            try:
                rid = tuple(e.GetCachedPropertyValue(U.UIA_RuntimeIdPropertyId) or ())
            except Exception:
                rid = ()
            out.append({"ct": e.CachedControlType, "name": e.CachedName or "",
                        "rect": (r.left, r.top, r.right, r.bottom),
                        "aria": e.GetCachedPropertyValue(U.UIA_AriaRolePropertyId) or "",
                        "off": bool(e.GetCachedPropertyValue(U.UIA_IsOffscreenPropertyId)),
                        "enabled": bool(e.GetCachedPropertyValue(U.UIA_IsEnabledPropertyId)),
                        "pw": bool(e.GetCachedPropertyValue(U.UIA_IsPasswordPropertyId)),
                        "value": e.GetCachedPropertyValue(U.UIA_ValueValuePropertyId) or "",
                        "rid": rid, "_e": e})
        return out

    def read(self, frame: Frame, epoch: int, doc=None) -> tuple[list[Element], str]:
        doc = doc or self.document()
        last = None
        for _ in range(2):                         # 页面正在变化时偶发 COMError：重试一次
            try:
                rows = self.rows(doc)
                for r in rows:
                    if r["pw"]:
                        r["value"] = ""            # 密码框的值不读
                return rows_to_elements(rows, frame, epoch)
            except Exception as e:
                last = e
                time.sleep(0.2)
                doc = self.document()
        raise UiaUnavailable(f"读取无障碍树失败：{last}")

    # ---------------- 点查询、重新定位 ----------------
    def element_at(self, sx: int, sy: int) -> UiaHit | None:
        from ctypes import wintypes as wt
        U = self.U
        try:
            e = self.uia.ElementFromPoint(wt.POINT(int(sx), int(sy)))
        except Exception:
            return None
        if not e:
            return None
        try:
            doc_rid = tuple(self.document(False).GetRuntimeId())
        except Exception:
            doc_rid = ()
        ancestors, cur, in_doc = [], e, False
        for _ in range(40):
            try:
                cur = self._walker.GetParentElement(cur)
            except Exception:
                break
            if not cur:
                break
            rid = tuple(cur.GetRuntimeId())
            ancestors.append(rid)
            if rid == doc_rid:
                in_doc = True
                break
        r = e.CurrentBoundingRectangle
        row = {"ct": e.CurrentControlType, "aria": e.CurrentAriaRole or ""}
        return UiaHit(_role(row) or "", e.CurrentName or "", tuple(e.GetRuntimeId()), ancestors,
                      (r.left, r.top, r.right, r.bottom), in_doc)

    def refresh_rect(self, ref: UiaRef) -> tuple | None:
        if ref.element is not None:
            try:
                r = ref.element.CurrentBoundingRectangle
                return r.left, r.top, r.right, r.bottom
            except Exception:
                pass
        return None

    def value(self, ref: UiaRef) -> str | None:
        try:
            vp = ref.element.GetCurrentPattern(self.U.UIA_ValuePatternId).QueryInterface(
                self.U.IUIAutomationValuePattern)
            return vp.CurrentValue
        except Exception:
            return None

    # ---------------- 标签页 ----------------
    def tabs(self) -> list[dict]:
        """[{rid, name, selected}]，按标签条顺序。"""
        U = self.U
        try:
            arr = self.root().FindAllBuildCache(U.TreeScope_Descendants, self._tab_cond, self._tab_cr)
        except Exception:
            return []
        out = []
        for i in range(arr.Length):
            e = arr.GetElement(i)
            try:
                rid = tuple(e.GetCachedPropertyValue(U.UIA_RuntimeIdPropertyId) or ())
                sel = bool(e.GetCachedPropertyValue(U.UIA_SelectionItemIsSelectedPropertyId))
            except Exception:
                rid, sel = (), False
            out.append({"rid": rid, "name": e.CachedName or "", "selected": sel})
        return out
