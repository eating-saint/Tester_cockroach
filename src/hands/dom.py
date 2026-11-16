"""M3 DOM 读取：从所有 frame（含跨源 iframe、Shadow DOM）提取可交互元素与正文。"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urljoin

from .geometry import Box


@dataclass(frozen=True)
class NodeRef:
    session_id: str | None
    backend_node_id: int


@dataclass
class Element:
    id: str
    role: str
    name: str
    text: str
    tag: str
    href: str | None
    value: str | None
    box: Box
    visible: bool
    in_viewport: bool
    enabled: bool
    frame_path: list[str] = field(default_factory=list)
    in_shadow: bool = False
    _ref: NodeRef | None = field(default=None, repr=False)
    context: str = ""          # 所在“块”的文字（如搜索结果的标题 + 摘要），见 _Doc.context_of
    source: str = "dom"        # dom / uia（桌面驱动无障碍树）/ grid（网格兜底）
    is_password: bool = False  # 密码框：值恒为 None，不读不记

    def to_dict(self) -> dict:
        d = {
            "id": self.id, "role": self.role, "name": self.name, "text": self.text,
            "tag": self.tag, "href": self.href, "value": None if self.is_password else self.value,
            "box": self.box.to_dict(), "visible": self.visible,
            "in_viewport": self.in_viewport, "enabled": self.enabled,
            "frame_path": list(self.frame_path), "in_shadow": self.in_shadow,
            "context": self.context[:300],
        }
        if self.source != "dom":
            d["source"] = self.source
        if self.is_password:
            d["is_password"] = True
        return d


INTERACTIVE_ROLES = {"button", "link", "textbox", "searchbox", "combobox", "checkbox", "radio",
                     "listbox", "menuitem", "tab", "switch", "slider", "spinbutton"}
INPUT_ROLES = {"checkbox": "checkbox", "radio": "radio", "range": "slider", "number": "spinbutton",
               "search": "searchbox", "submit": "button", "button": "button", "reset": "button",
               "image": "button"}


def _infer_role(tag: str, attrs: dict) -> str:
    if attrs.get("role"):
        return attrs["role"]
    if tag == "a":
        return "link"
    if tag == "button":
        return "button"
    if tag == "input":
        return INPUT_ROLES.get(attrs.get("type", "text").lower(), "textbox")
    if tag == "select":
        return "combobox"
    if tag == "textarea":
        return "textbox"
    if tag == "iframe":
        return "iframe"
    return "generic"


def _is_candidate(tag: str, attrs: dict) -> bool:
    if tag == "a":
        return "href" in attrs
    if tag == "input":
        return attrs.get("type", "").lower() != "hidden"
    return tag in ("button", "select", "textarea", "iframe")


def _collapse(s: str) -> str:
    return " ".join(s.split())


class _Doc:
    """解析 DOMSnapshot 中的一个 document。"""

    def __init__(self, doc: dict, strings: list[str]):
        S = lambda i: strings[i] if i is not None and i >= 0 else ""  # noqa: E731
        n = doc["nodes"]
        self.frame_id = S(doc.get("frameId", -1))
        self.base_url = S(doc.get("baseURL", -1)) or S(doc.get("documentURL", -1))
        self.scroll = (doc.get("scrollOffsetX", 0), doc.get("scrollOffsetY", 0))
        self.parent = n.get("parentIndex", [])
        self.types = n.get("nodeType", [])
        self.names = [S(i) for i in n.get("nodeName", [])]
        self.values = [S(i) for i in n.get("nodeValue", [])]
        self.backend = n.get("backendNodeId", [])
        self.attrs = []
        for arr in n.get("attributes", []):
            self.attrs.append({S(arr[i]).lower(): S(arr[i + 1]) for i in range(0, len(arr) - 1, 2)})
        self.shadow_root = set(n.get("shadowRootType", {}).get("index", []))
        self.input_value = dict(zip(n.get("inputValue", {}).get("index", []),
                                    [S(v) for v in n.get("inputValue", {}).get("value", [])]))
        self.content_doc = dict(zip(n.get("contentDocumentIndex", {}).get("index", []),
                                    n.get("contentDocumentIndex", {}).get("value", [])))
        lay = doc["layout"]
        self.layout: dict[int, dict] = {}
        self.text_boxes: list[tuple[int, str, list]] = []
        for k, ni in enumerate(lay.get("nodeIndex", [])):
            styles = [S(s) for s in lay["styles"][k]] if k < len(lay.get("styles", [])) else []
            bounds = lay["bounds"][k]
            text = S(lay["text"][k]) if k < len(lay.get("text", [])) else ""
            self.layout[ni] = {"bounds": bounds, "styles": styles}
            if text and self.types[ni] == 3:
                self.text_boxes.append((ni, text, bounds))
        self.children: dict[int, list[int]] = {}
        for i, p in enumerate(self.parent):
            if p >= 0:
                self.children.setdefault(p, []).append(i)

    def in_shadow(self, i: int) -> bool:
        while i >= 0:
            if i in self.shadow_root:
                return True
            i = self.parent[i]
        return False

    def text_of(self, i: int, limit: int = 300) -> str:
        out: list[str] = []
        stack = [i]
        while stack and sum(map(len, out)) < limit:
            j = stack.pop()
            if self.types[j] == 3:
                out.append(self.values[j])
            elif self.names[j] not in ("SCRIPT", "STYLE"):
                stack.extend(reversed(self.children.get(j, [])))
        return _collapse(" ".join(out))[:limit]

    def context_of(self, i: int, own: str, max_len: int = 600, max_up: int = 6) -> str:
        """元素所在的“块”的文字：往上找祖先，取文字不超过 max_len 的最大那一层。
        例：搜索结果链接只有标题，往上一两层就是“标题 + 网址 + 摘要”的结果块；再往上整页结果太长，停止。"""
        best = own
        j = self.parent[i] if i < len(self.parent) else -1
        for _ in range(max_up):
            if j is None or j < 0 or self.names[j] in ("BODY", "HTML", "#document"):
                break
            t = self.text_of(j, max_len + 1)
            if len(t) > max_len:
                break
            if len(t) > len(best):
                best = t
            j = self.parent[j]
        return best

    def body_text(self) -> str:
        lines: list[str] = []
        prev_y = None
        for _, text, b in self.text_boxes:
            t = _collapse(text)
            if not t:
                continue
            if prev_y is not None and abs(b[1] - prev_y) <= 2 and lines:
                lines[-1] += " " + t
            else:
                lines.append(t)
            prev_y = b[1]
        return "\n".join(lines)


class DomReader:
    STYLES = ["display", "visibility", "opacity"]

    async def read(self, page) -> tuple[list[Element], str]:
        from .cdp import CDPError

        if getattr(page, "driver", "cdp") != "cdp":      # 其他驱动有自己的读取实现
            return await page.read_dom()

        metrics = await page.session.send("Page.getLayoutMetrics")
        vp = metrics.get("cssLayoutViewport", {})
        viewport = Box(0, 0, vp.get("clientWidth", 0), vp.get("clientHeight", 0))
        await page.refresh_offsets()
        elements: list[Element] = []
        texts: list[str] = []
        for fs in page.sessions():
            try:
                snap = await fs.session.send("DOMSnapshot.captureSnapshot",
                                             {"computedStyles": self.STYLES, "includeDOMRects": True})
            except CDPError:
                continue  # frame 已移除
            docs = [_Doc(d, snap["strings"]) for d in snap["documents"]]
            ax = await self._ax(fs.session, docs)
            base_path = self._frame_path(fs)
            # 文档 0 是该会话的根文档；同进程子文档通过 contentDocumentIndex 挂在 iframe 元素下
            await self._walk(fs, docs, 0, fs.owner_offset, base_path, ax, viewport, elements, texts)
        for k, e in enumerate(elements):
            e.id = f"e{k}"
        return elements, "\n".join(t for t in texts if t)

    @staticmethod
    def _frame_path(fs) -> list[str]:
        path = []
        while fs.parent is not None:
            path.append(fs.frame_id)
            fs = fs.parent
        return list(reversed(path))

    async def _ax(self, session, docs: list[_Doc]) -> dict[int, tuple]:
        from .cdp import CDPError

        out: dict[int, tuple] = {}
        frame_ids = [None] + [d.frame_id for d in docs[1:]]
        for fid in frame_ids:
            try:
                r = await session.send("Accessibility.getFullAXTree", {"frameId": fid} if fid else {})
            except CDPError:
                continue
            for node in r.get("nodes", []):
                bid = node.get("backendDOMNodeId")
                if bid is None or node.get("ignored"):
                    continue
                role = node.get("role", {}).get("value", "")
                name = node.get("name", {}).get("value", "")
                disabled = any(p.get("name") == "disabled" and p.get("value", {}).get("value")
                               for p in node.get("properties", []))
                out[bid] = (role, name, disabled)
        return out

    async def _walk(self, fs, docs, di, offset, path, ax, viewport, elements, texts):
        from .cdp import CDPError

        d = docs[di]
        texts.append(d.body_text())
        ox, oy = offset
        for i, name in enumerate(d.names):
            if d.types[i] != 1:
                continue
            tag = name.lower()
            attrs = d.attrs[i] if i < len(d.attrs) else {}
            bid = d.backend[i]
            ax_role, ax_name, ax_disabled = ax.get(bid, ("", "", False))
            if not (_is_candidate(tag, attrs) or ax_role in INTERACTIVE_ROLES):
                continue
            lay = d.layout.get(i)
            if lay:
                bx, by, bw, bh = lay["bounds"]
                styles = dict(zip(self.STYLES, lay["styles"]))
                box = Box(bx - d.scroll[0] + ox, by - d.scroll[1] + oy, bw, bh)
                visible = (styles.get("visibility", "visible") not in ("hidden", "collapse")
                           and bw > 0 and bh > 0 and bx + bw > 0 and by + bh > 0)
            else:
                box = Box(0, 0, 0, 0)
                visible = False
            role = ax_role if ax_role and ax_role not in ("generic", "none", "StaticText") else _infer_role(tag, attrs)
            text = d.text_of(i)
            value = d.input_value.get(i)
            name_ = ax_name or attrs.get("aria-label") or text or attrs.get("placeholder") or attrs.get("title") \
                or attrs.get("name") or (value or "")
            enabled = not ax_disabled and "disabled" not in attrs
            if tag == "iframe":
                name_ = attrs.get("title") or attrs.get("name") or ""
            elements.append(Element(
                id="", role=role, name=_collapse(name_)[:200], text=text, tag=tag,
                href=self._abs_href(attrs.get("href"), d) if tag == "a" else (attrs.get("src") if tag == "iframe" else None),
                value=value, box=box, visible=visible, in_viewport=visible and box.intersects(viewport),
                enabled=enabled, frame_path=list(path), in_shadow=d.in_shadow(i),
                _ref=NodeRef(fs.session.session_id, bid),
                context=d.context_of(i, text) if visible and tag != "iframe" else text))
            # 同进程子文档（同源 iframe）
            if tag == "iframe" and i in d.content_doc:
                cdi = d.content_doc[i]
                try:
                    model = await fs.session.send("DOM.getBoxModel", {"backendNodeId": bid})
                    c = model["model"]["content"]
                    # getBoxModel 返回本会话视口坐标；再加上本会话（跨源时）的偏移
                    child_off = (c[0] + fs.owner_offset[0], c[1] + fs.owner_offset[1])
                except CDPError:
                    child_off = (box.x, box.y)
                await self._walk(fs, docs, cdi, child_off, path + [docs[cdi].frame_id], ax, viewport, elements, texts)

    @staticmethod
    def _abs_href(href, d: _Doc):
        if href is None:
            return None
        return urljoin(d.base_url, href) if d.base_url else href

