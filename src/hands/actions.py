"""M7 动作：点击/输入/按键/滚动/后退/读取；每个动作前后取指纹判断是否生效。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .cdp import BrowserCrashed, CDPError
from .dom import DomReader, Element
from .geometry import quad_to_box
from .page import WaitResult


@dataclass
class ActionResult:
    ok: bool
    effective: bool
    changes: list[str] = field(default_factory=list)
    wait: WaitResult | None = None
    data: Any = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {"ok": self.ok, "effective": self.effective, "changes": self.changes,
                "wait": self.wait.to_dict() if self.wait else None, "data": self.data, "error": self.error}


KEYS = {
    "Enter": ("Enter", 13, "\r"), "Tab": ("Tab", 9, None), "Escape": ("Escape", 27, None),
    "Backspace": ("Backspace", 8, None), "ArrowDown": ("ArrowDown", 40, None),
    "ArrowUp": ("ArrowUp", 38, None), "PageDown": ("PageDown", 34, None), "End": ("End", 35, None),
}


class ActionExecutor:
    def __init__(self, page):
        self.page = page

    # ---------------- 指纹 ----------------
    async def _fingerprint(self) -> tuple:
        from .pagestate import PageState
        from .geometry import Box

        els, body = await DomReader().read(self.page)
        url = await self.page.url()
        fp = PageState(url, "", Box(0, 0, 0, 0), 1, els, body).fingerprint()
        try:
            focus = await self.page.evaluate(
                "(()=>{const a=document.activeElement;return a?(a.tagName+'#'+(a.id||'')):''})()")
        except CDPError:
            focus = None
        return fp[0], fp[1], focus

    @staticmethod
    def _diff(before, after) -> list[str]:
        out = []
        if before[0] != after[0]:
            out.append("url")
        if before[1] != after[1]:
            out.append("dom")
        if before[2] != after[2]:
            out.append("focus")
        return out

    async def _run(self, fn, *, measure: bool = True) -> ActionResult:
        try:
            before = await self._fingerprint() if measure else None
            res: ActionResult = await fn()
            if measure and res.ok:
                tab = self.page.take_new_tab()
                if tab:
                    res.wait = await self.page.adopt_new_tab(tab)
                    res.changes.append("new_tab")
                after = await self._fingerprint()
                res.changes = self._diff(before, after) + res.changes
                res.effective = bool(res.changes)
            return res
        except BrowserCrashed:
            raise
        except (CDPError, LookupError, ValueError) as e:
            return ActionResult(False, False, error=f"{type(e).__name__}: {e}")

    # ---------------- 定位 ----------------
    async def _locate(self, el: Element) -> tuple[float, float]:
        if el._ref is None:
            raise LookupError(f"element {el.id} has no node reference")
        fs = self.page.frame_of(el._ref.session_id)
        chain = []
        f = fs
        while f.parent is not None:
            chain.append(f)
            f = f.parent
        for child in reversed(chain):          # 先把各级 <iframe> 滚到可见
            owner = await child.parent.session.send("DOM.getFrameOwner", {"frameId": child.frame_id})
            await child.parent.session.send("DOM.scrollIntoViewIfNeeded", {"backendNodeId": owner["backendNodeId"]})
        await fs.session.send("DOM.scrollIntoViewIfNeeded", {"backendNodeId": el._ref.backend_node_id})
        await self.page.refresh_offsets()
        q = await fs.session.send("DOM.getContentQuads", {"backendNodeId": el._ref.backend_node_id})
        if not q.get("quads"):
            raise LookupError(f"element {el.id} has no layout")
        box = quad_to_box(q["quads"][0]).offset(*fs.owner_offset)
        return box.center()

    async def _mouse_click(self, x: float, y: float):
        s = self.page.session
        await s.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        await s.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
        await s.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})

    async def _settle(self) -> WaitResult:
        return await self.page.wait_settled(False)

    # ---------------- 动作 ----------------
    async def click(self, el: Element) -> ActionResult:
        async def go():
            x, y = await self._locate(el)
            await self._mouse_click(x, y)
            return ActionResult(True, False, wait=await self._settle(), data={"x": x, "y": y})
        return await self._run(go)

    async def type(self, el: Element, text: str) -> ActionResult:
        async def go():
            x, y = await self._locate(el)
            await self._mouse_click(x, y)
            s = self.page.session
            await s.send("Input.dispatchKeyEvent", {"type": "rawKeyDown", "key": "a", "code": "KeyA",
                                                    "windowsVirtualKeyCode": 65, "modifiers": 2, "commands": ["selectAll"]})
            await s.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "code": "KeyA",
                                                    "windowsVirtualKeyCode": 65, "modifiers": 2})
            await s.send("Input.insertText", {"text": text})
            wait = await self._settle()
            els, _ = await DomReader().read(self.page)
            now = next((e for e in els if e._ref == el._ref), None)
            if now is None or (now.value or "") != text:
                return ActionResult(False, False, wait=wait,
                                    error=f"value mismatch: {None if now is None else now.value!r}")
            return ActionResult(True, False, wait=wait)
        return await self._run(go)

    async def press(self, key: str) -> ActionResult:
        async def go():
            if key not in KEYS:
                raise ValueError(f"unsupported key: {key}")
            name, vk, text = KEYS[key]
            s = self.page.session
            down = {"type": "keyDown" if text else "rawKeyDown", "key": name, "code": name, "windowsVirtualKeyCode": vk}
            if text:
                down["text"] = text
            await s.send("Input.dispatchKeyEvent", down)
            await s.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": name, "code": name, "windowsVirtualKeyCode": vk})
            return ActionResult(True, False, wait=await self._settle())
        return await self._run(go)

    async def _scroll_y(self) -> float:
        m = await self.page.session.send("Page.getLayoutMetrics")
        return m.get("cssVisualViewport", {}).get("pageY", 0)

    async def scroll(self, direction: str | None = None, pixels: float | None = None,
                     to_bottom: bool = False) -> ActionResult:
        async def go():
            m = await self.page.session.send("Page.getLayoutMetrics")
            vp = m.get("cssLayoutViewport", {})
            cx, cy = vp.get("clientWidth", 800) / 2, vp.get("clientHeight", 600) / 2
            s = self.page.session

            async def wheel(dy):
                before = await self._scroll_y()
                # 先移动鼠标确定滚轮命中目标，否则无头模式下滚轮事件可能不生效
                await s.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": cx, "y": cy})
                await s.send("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": cx, "y": cy, "deltaX": 0, "deltaY": dy})
                # 滚动由合成器异步完成：等到滚动位置变化（到底时不再变化，最多等 1.5 秒）
                await self.page.wait_for(lambda: self._scroll_y(), lambda y: y != before, timeout=1.5)
                return await self._settle()

            start_y = await self._scroll_y()
            if to_bottom:
                wait = None
                last = start_y
                for _ in range(50):
                    wait = await wheel(vp.get("clientHeight", 600))
                    y = await self._scroll_y()
                    if y <= last:
                        break
                    last = y
            else:
                wait = await wheel(pixels if direction == "down" else -pixels)
            end_y = await self._scroll_y()
            return ActionResult(True, False, changes=["scroll"] if end_y != start_y else [], wait=wait,
                                data={"scroll_y": end_y})
        return await self._run(go)

    async def back(self) -> ActionResult:
        async def go():
            w = await self.page.back()
            return ActionResult(w.status == "ok", False, wait=w, error=None if w.status == "ok" else w.status)
        return await self._run(go)

    # ---------------- 读取（不改变页面） ----------------
    async def read_text(self, element: Element | None = None, contains: str | None = None) -> ActionResult:
        async def go():
            if element is not None:
                text = element.text or element.name
            else:
                _, text = await DomReader().read(self.page)
            if contains:
                text = "\n".join(line for line in text.splitlines() if contains.lower() in line.lower())
            return ActionResult(True, False, data=text)
        return await self._run(go, measure=False)

    async def read_links(self, href_contains: str | None = None, href_excludes: str | None = None,
                         title_min_len: int = 0) -> ActionResult:
        async def go():
            els, _ = await DomReader().read(self.page)
            by_href: dict[str, str] = {}      # 同一地址只保留最长的标题（结果常有"站点名 + 标题"两个链接）
            for e in els:
                if not e.visible or e.role != "link" or not e.href:
                    continue
                h = e.href.lower()
                if href_contains and href_contains.lower() not in h:
                    continue
                if href_excludes and any(x.strip().lower() in h for x in href_excludes.split(",") if x.strip()):
                    continue
                title = e.name or e.text
                if len(title) > len(by_href.get(e.href, "")):
                    by_href[e.href] = title
            out = [{"title": t, "href": h} for h, t in by_href.items() if len(t) >= title_min_len]
            return ActionResult(True, False, data=out)
        return await self._run(go, measure=False)
