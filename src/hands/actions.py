"""M7 动作：点击/输入/按键/滚动/后退/读取；每个动作前后取指纹判断是否生效。"""
from __future__ import annotations

import inspect
import time
from dataclasses import dataclass, field
from typing import Any

from .cdp import BrowserCrashed, CDPError
from .status import STATUS
from .dom import DomReader, Element
from .geometry import Box, quad_to_box
from .page import WaitResult


@dataclass
class ActionResult:
    ok: bool
    effective: bool
    changes: list[str] = field(default_factory=list)
    wait: WaitResult | None = None
    data: Any = None
    error: str | None = None
    trajectory: dict | None = None                         # 鼠标轨迹（开启 humanize.mouse 时）
    disturbances: list[dict] = field(default_factory=list)  # 动作前发生的干扰项（思考时间、滚轮乱动）
    trajectory_png: bytes | None = field(default=None, repr=False)  # 鼠标到位时的截图，用来画轨迹回放；不写进 JSON

    def to_dict(self) -> dict:
        d = {"ok": self.ok, "effective": self.effective, "changes": self.changes,
             "wait": self.wait.to_dict() if self.wait else None, "data": self.data, "error": self.error}
        if self.trajectory is not None:
            d["trajectory"] = self.trajectory
        if self.disturbances:
            d["disturbances"] = self.disturbances
        return d


KEYS = {
    "Enter": ("Enter", 13, "\r"), "Tab": ("Tab", 9, None), "Escape": ("Escape", 27, None),
    "Backspace": ("Backspace", 8, None), "ArrowDown": ("ArrowDown", 40, None),
    "ArrowUp": ("ArrowUp", 38, None), "PageDown": ("PageDown", 34, None), "End": ("End", 35, None),
}


class ActionExecutor:
    def __init__(self, page, humanizer=None, capture_trajectory: bool = False, before_action=None):
        self.page = page
        self.humanizer = humanizer                  # None = 和 ver0 完全一样的直接点击、无干扰
        self.capture_trajectory = capture_trajectory
        self.before_action = before_action          # 可选钩子：每个动作开始前调用（面板用来让新设置生效）

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

    async def _run(self, fn, *, measure: bool = True, tool: str = "") -> ActionResult:
        t0 = time.monotonic()
        if self.before_action is not None:
            r = self.before_action(self)
            if inspect.isawaitable(r):
                await r
        res = await self._run_inner(fn, measure)
        STATUS.action_done({"tool": tool, "ok": res.ok, "effective": res.effective, "changes": list(res.changes),
                            "error": res.error, "trajectory": res.trajectory,
                            "elapsed_ms": int((time.monotonic() - t0) * 1000)})
        return res

    async def _run_inner(self, fn, measure: bool) -> ActionResult:
        try:
            disturbances = await self._disturb() if measure else []
            before = await self._fingerprint() if measure else None
            res: ActionResult = await fn()
            res.disturbances = disturbances + res.disturbances
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
        return (await self._locate_box(el)).center()

    async def _node_value(self, el: Element) -> str | None:
        """读节点当前的 value（input / textarea）或可编辑元素的文字；读不到返回 None。只读，不改页面。"""
        if el._ref is None:
            return None
        try:
            fs = self.page.frame_of(el._ref.session_id)
            obj = await fs.session.send("DOM.resolveNode", {"backendNodeId": el._ref.backend_node_id})
            r = await fs.session.send("Runtime.callFunctionOn", {
                "objectId": obj["object"]["objectId"], "returnByValue": True,
                "functionDeclaration": "function(){return 'value' in this ? this.value : "
                                       "(this.isContentEditable ? this.innerText : null)}"})
            v = r.get("result", {}).get("value")
            return None if v is None else str(v)
        except Exception:
            return None

    async def _locate_box(self, el: Element) -> Box:
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
        return quad_to_box(q["quads"][0]).offset(*fs.owner_offset)

    async def _viewport(self) -> tuple[float, float]:
        vp = (await self.page.session.send("Page.getLayoutMetrics")).get("cssLayoutViewport", {})
        return float(vp.get("clientWidth", 800)), float(vp.get("clientHeight", 600))

    async def _mouse_to(self, box: Box) -> tuple[float, float, dict | None, bytes | None]:
        """把鼠标移到目标框：开启轨迹时沿轨迹移动到框内随机落点，否则直接跳到中心。"""
        h = self.humanizer
        if h is None or not h.mouse_on:
            x, y = box.center()
            await self.page.session.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
            if h is not None:
                h.jump_to(x, y)
            return x, y, None, None
        mv = await h.move_to(self.page.session, box, await self._viewport())
        png = None
        if self.capture_trajectory:
            from .screenshot import capture
            png = await capture(self.page)
        return mv.landing[0], mv.landing[1], mv.to_dict(), png

    async def _mouse_click(self, box: Box) -> tuple[float, float, dict | None, bytes | None]:
        x, y, traj, png = await self._mouse_to(box)
        s = self.page.session
        await s.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
        await s.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
        return x, y, traj, png

    # ---------------- 干扰项（可选） ----------------
    async def _disturb(self) -> list[dict]:
        h = self.humanizer
        if h is None:
            return []
        out = []
        t = await h.think()
        if t:
            out.append(t)
        j = await self._scroll_jitter()
        if j:
            out.append(j)
        return out

    async def _wheel(self, x: float, y: float, dy: float) -> bool:
        """在 (x, y) 处发一次滚轮，等滚动位置变化（到头时不变，最多等 1.5 秒）；返回是否滚动了。"""
        s = self.page.session
        before = await self._scroll_y()
        await s.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        await s.send("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": x, "y": y, "deltaX": 0, "deltaY": dy})
        await self.page.wait_for(lambda: self._scroll_y(), lambda v: v != before, timeout=1.5)
        return (await self._scroll_y()) != before

    async def _scroll_jitter(self) -> dict | None:
        """随机滚几下，停一会儿，再滚回原来的位置。"""
        h = self.humanizer
        parts = h.plan_jitter()
        if parts is None:
            return None
        vw, vh = await self._viewport()
        x, y = h.cursor if h.cursor else (vw / 2, vh / 2)
        x, y = min(max(x, 1), vw - 2), min(max(y, 1), vh - 2)
        y0 = await self._scroll_y()
        # 只往有空间的方向乱动：在顶部就往下、在底部就往上，两边都滚不动就跳过（避免白等滚动超时）
        m = await self.page.session.send("Page.getLayoutMetrics")
        content_h = (m.get("cssContentSize") or m.get("contentSize") or {}).get("height", 0)
        room_down, room_up = max(content_h - vh - y0, 0), y0
        total = sum(parts)
        if (total > 0 and room_down < 1) or (total < 0 and room_up < 1):
            parts = [-p for p in parts]
            total = -total
        room = room_down if total > 0 else room_up
        if room < 1:
            return {"kind": "scroll_jitter", "skipped": "page not scrollable", "residual_px": 0.0}
        if abs(total) > room:                                # 空间不够就按比例缩小
            k = room / abs(total)
            parts = [int(p * k) for p in parts if int(p * k)] or [int(room) if total > 0 else -int(room)]
        pauses = []
        for i, dy in enumerate(parts):
            await self._wheel(x, y, dy)
            if i < len(parts) - 1:
                pauses.append(await h.pause())
        reached = await self._scroll_y()
        pauses.append(await h.pause())
        for _ in range(4):                                  # 回滚：按实际偏移量反向滚，直到误差 ≤1 像素
            off = (await self._scroll_y()) - y0
            if abs(off) <= 1:
                break
            await self._wheel(x, y, -off)
        await self._settle()
        restored = await self._scroll_y()
        return {"kind": "scroll_jitter", "parts": parts, "from_y": y0, "reached_y": reached,
                "restored_y": restored, "residual_px": round(restored - y0, 1), "pauses_ms": pauses}

    async def _settle(self) -> WaitResult:
        return await self.page.wait_settled(False)

    # ---------------- 动作 ----------------
    async def click(self, el: Element) -> ActionResult:
        async def go():
            box = await self._locate_box(el)
            x, y, traj, png = await self._mouse_click(box)
            return ActionResult(True, False, wait=await self._settle(), data={"x": x, "y": y},
                                trajectory=traj, trajectory_png=png)
        return await self._run(go, tool="click")

    async def type(self, el: Element, text: str) -> ActionResult:
        async def go():
            box = await self._locate_box(el)
            _, _, traj, png = await self._mouse_click(box)
            s = self.page.session
            await s.send("Input.dispatchKeyEvent", {"type": "rawKeyDown", "key": "a", "code": "KeyA",
                                                    "windowsVirtualKeyCode": 65, "modifiers": 2, "commands": ["selectAll"]})
            await s.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "code": "KeyA",
                                                    "windowsVirtualKeyCode": 65, "modifiers": 2})
            await s.send("Input.insertText", {"text": text})
            wait = await self._settle()
            # 直接向节点要当前值：DOM 快照只给 <input> 带值，<textarea>（如 Google 搜索框）和可编辑元素拿不到
            got = await self._node_value(el)
            if got is None:
                els, _ = await DomReader().read(self.page)
                now = next((e for e in els if e._ref == el._ref), None)
                got = None if now is None else now.value
            if (got or "") != text:
                return ActionResult(False, False, wait=wait, error=f"value mismatch: {got!r}")
            return ActionResult(True, False, wait=wait, trajectory=traj, trajectory_png=png)
        return await self._run(go, tool="type")

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
        return await self._run(go, tool="press")

    async def _scroll_y(self) -> float:
        m = await self.page.session.send("Page.getLayoutMetrics")
        return m.get("cssVisualViewport", {}).get("pageY", 0)

    async def scroll(self, direction: str | None = None, pixels: float | None = None,
                     to_bottom: bool = False) -> ActionResult:
        async def go():
            vw, vh = await self._viewport()
            traj = None
            if self.humanizer is not None and self.humanizer.mouse_on:
                # 开启轨迹时，先沿轨迹把鼠标移到视口中部，再在落点处滚动
                cx, cy, traj, _ = await self._mouse_to(Box(vw * 0.3, vh * 0.3, vw * 0.4, vh * 0.4))
            else:
                cx, cy = vw / 2, vh / 2

            async def wheel(dy):
                # 先移动鼠标确定滚轮命中目标，否则无头模式下滚轮事件可能不生效；
                # 滚动由合成器异步完成，_wheel 会等到滚动位置变化
                await self._wheel(cx, cy, dy)
                return await self._settle()

            start_y = await self._scroll_y()
            if to_bottom:
                wait = None
                last = start_y
                for _ in range(50):
                    wait = await wheel(vh)
                    y = await self._scroll_y()
                    if y <= last:
                        break
                    last = y
            else:
                wait = await wheel(pixels if direction == "down" else -pixels)
            end_y = await self._scroll_y()
            return ActionResult(True, False, changes=["scroll"] if end_y != start_y else [], wait=wait,
                                data={"scroll_y": end_y}, trajectory=traj)
        return await self._run(go, tool="scroll")

    async def back(self) -> ActionResult:
        async def go():
            w = await self.page.back()
            return ActionResult(w.status == "ok", False, wait=w, error=None if w.status == "ok" else w.status)
        return await self._run(go, tool="back")

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
        return await self._run(go, measure=False, tool="read_text")

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
        return await self._run(go, measure=False, tool="read_links")
