"""M7 动作：点击/输入/按键/滚动/后退/读取；每个动作前后取指纹判断是否生效。"""
from __future__ import annotations

import inspect
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .cdp import BrowserCrashed, CDPError
from .status import STATUS
from .dom import DomReader, Element
from .geometry import Box
from .guard import Guard
from .page import KEYS, WaitResult
from .pause import Interrupted


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




class ActionExecutor:
    def __init__(self, page, humanizer=None, capture_trajectory: bool = False, before_action=None, guard=None,
                 confirm=None, confirm_fn=None, gate=None):
        self.page = page
        self.guard = guard if guard is not None else Guard()   # 兜底拦截：始终启用，只能换清单，不能关闭
        self.humanizer = humanizer                  # None = 和 ver0 完全一样的直接点击、无干扰
        self.capture_trajectory = capture_trajectory
        self.before_action = before_action          # 可选钩子：每个动作开始前调用（面板用来让新设置生效）
        # 需确认（spec F9）：confirm 为 None 或未开启时行为与之前完全一致
        self.confirm = confirm
        self.confirm_fn = confirm_fn
        self.gate = gate
        self.confirms: list[dict] = []
        self._last_typed: Element | None = None     # 回车时的焦点元素（桌面驱动读不到 activeElement）

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
        vh = getattr(self.page, "visual_hash", None)    # 桌面驱动：截图指纹（无障碍树看不到的变化，如 canvas）
        visual = await vh() if vh is not None else None
        return fp[0], fp[1], focus, visual

    @staticmethod
    def _diff(before, after) -> list[str]:
        out = []
        if before[0] != after[0]:
            out.append("url")
        if before[1] != after[1]:
            out.append("dom")
        if before[2] != after[2]:
            out.append("focus")
        if len(before) > 3 and before[3] != after[3]:
            out.append("visual")
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
        except Interrupted as e:
            # 人一碰鼠标键盘 / 面板暂停：动作中途停下，下一步恢复后重新观察
            return ActionResult(False, False, error="interrupted", data={"interrupted": True, "reason": str(e)})
        except (CDPError, LookupError, ValueError) as e:
            return ActionResult(False, False, error=f"{type(e).__name__}: {e}")

    # ---------------- 定位 ----------------
    async def _locate(self, el: Element) -> tuple[float, float]:
        return (await self._locate_box(el)).center()

    async def _node_value(self, el: Element) -> str | None:
        return await self.page.node_value(el)

    async def _locate_box(self, el: Element) -> Box:
        return await self.page.locate_box(el)

    async def _viewport(self) -> tuple[float, float]:
        lay = await self.page.layout()
        return lay["vw"], lay["vh"]

    async def _mouse_to(self, box: Box) -> tuple[float, float, dict | None, bytes | None]:
        """把鼠标移到目标框：开启轨迹时沿轨迹移动到框内随机落点，否则直接跳到中心。"""
        h = self.humanizer
        cur = getattr(self.page, "cursor_css", None)
        if h is not None and h.mouse_on and cur is not None:
            h.jump_to(*cur())                  # 桌面驱动：轨迹从真实光标当前位置出发
        if h is None or not h.mouse_on:
            x, y = box.center()
            await self.page.mouse_move(x, y)
            if h is not None:
                h.jump_to(x, y)
            return x, y, None, None
        mv = await h.move_to(self.page.mouse_move, box, await self._viewport())
        png = None
        if self.capture_trajectory:
            from .screenshot import capture
            png = await capture(self.page)
        return mv.landing[0], mv.landing[1], mv.to_dict(), png

    async def _mouse_click(self, box: Box) -> tuple[float, float, dict | None, bytes | None]:
        x, y, traj, png = await self._mouse_to(box)
        await self.page.mouse_click(x, y)
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
        before = await self._scroll_y()
        await self.page.mouse_wheel(x, y, dy)
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
        content_h = (await self.page.layout())["content_h"]
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
    # ---------------- 兜底拦截（ver1 F6） ----------------
    def _blocked(self, hit, tool: str) -> ActionResult:
        res = ActionResult(False, False, error=f"guard_blocked: {hit.describe()}", data={"guard": asdict(hit)})
        STATUS.action_done({"tool": tool, "ok": False, "effective": False, "changes": [], "error": res.error,
                            "trajectory": None, "elapsed_ms": 0})
        return res

    async def _guard(self, el: Element, tool: str) -> ActionResult | None:
        fa = getattr(self.page, "form_action", None)
        form_action = await fa(el) if fa is not None else None
        hit = self.guard.check(el, form_action)
        return self._blocked(hit, tool) if hit else None

    # ---------------- 需确认（spec F9） ----------------
    async def _ask(self, req, tool: str) -> ActionResult | None:
        if req is None:
            return None
        allow = bool(await self.confirm_fn(req)) if self.confirm_fn is not None else False
        self.confirms.append({"t": time.time(), **req.to_dict(), "allowed": allow})
        if allow:
            return None
        res = ActionResult(False, False, error=f"用户拒绝：{req.describe()}", data={"confirm": req.to_dict()})
        STATUS.action_done({"tool": tool, "ok": False, "effective": False, "changes": [], "error": res.error,
                            "trajectory": None, "elapsed_ms": 0})
        return res

    async def _check_target(self, el: Element, tool: str) -> ActionResult | None:
        """兜底拦截（不可放行）→ 网格落点核对 → 需确认。"""
        target = el
        if getattr(el, "source", "dom") == "grid":
            at = getattr(self.page, "element_at", None)
            x, y = el.box.center()
            hit = await at(x, y) if at is not None else None
            if hit is None:
                if self.confirm is not None and self.confirm.enabled:
                    return await self._ask(self.confirm.unverifiable(tool, x, y), tool)
                return None
            target = hit
        blocked = await self._guard(target, tool)
        if blocked:
            return blocked
        if self.confirm is not None and self.confirm.enabled:
            return await self._ask(self.confirm.check_click(target, tool), tool)
        return None

    async def click(self, el: Element) -> ActionResult:
        blocked = await self._check_target(el, "click")
        if blocked:
            return blocked

        async def go():
            box = await self._locate_box(el)
            x, y, traj, png = await self._mouse_click(box)
            return ActionResult(True, False, wait=await self._settle(), data={"x": x, "y": y},
                                trajectory=traj, trajectory_png=png)
        return await self._run(go, tool="click")

    async def open_tab(self, el: Element, switch: bool = False) -> ActionResult:
        """Ctrl+点击：在新标签页打开链接，原标签页不关。switch=True 时切到新标签页继续（原页仍保留），
        否则留在原页（新标签页在后台）。"""
        blocked = await self._check_target(el, "open_tab")
        if blocked:
            return blocked

        async def go():
            box = await self._locate_box(el)
            x, y, traj, png = await self._mouse_to(box)
            self.page.clear_spawned_tabs()
            await self.page.mouse_click(x, y, ctrl=True)

            async def take():
                return self.page.take_spawned_tab()
            tab = await self.page.wait_for(take, lambda v: v is not None, timeout=5)
            if tab is None:
                return ActionResult(False, False, error="没有打开新标签页", data={"x": x, "y": y},
                                    trajectory=traj, trajectory_png=png)
            if switch:
                wait = await self.page.adopt_new_tab(tab)
                # Ctrl+点击打开的是后台标签页；有界面时 Chrome 不绘制后台标签页，不切到前台截图会一直等不到
                await self.page.bring_to_front()
                return ActionResult(True, False, changes=["new_tab"], wait=wait,
                                    data={"x": x, "y": y, "switched": True}, trajectory=traj, trajectory_png=png)
            return ActionResult(True, False, changes=["new_tab_background"], wait=await self._settle(),
                                data={"x": x, "y": y, "switched": False}, trajectory=traj, trajectory_png=png)
        return await self._run(go, tool="open_tab")

    async def right_click(self, el: Element) -> ActionResult:
        """右键弹出菜单（桌面驱动）；菜单项在下一步的元素清单里。"""
        blocked = await self._check_target(el, "right_click")
        if blocked:
            return blocked

        async def go():
            box = await self._locate_box(el)
            x, y, traj, png = await self._mouse_to(box)
            await self.page.mouse_click(x, y, button="right")
            wait = await self._settle()
            return ActionResult(True, False, wait=wait, data={"x": x, "y": y}, trajectory=traj, trajectory_png=png)
        return await self._run(go, tool="right_click")

    async def type(self, el: Element, text: str) -> ActionResult:
        if getattr(el, "is_password", False) and self.confirm is not None and self.confirm.enabled:
            # 永远不往密码框里输入：转人工（runner 看到 data.handoff 后走人工介入）
            return ActionResult(False, False, error="不往密码框输入，已转人工", data={"handoff": "login"})
        self._last_typed = el

        async def go():
            box = await self._locate_box(el)
            _, _, traj, png = await self._mouse_click(box)
            await self.page.select_all()
            await self.page.insert_text(text)
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
        if key == "Enter":                         # 回车可能提交表单：查焦点元素所在表单的提交地址
            try:
                fa = await self.page.evaluate(
                    "(()=>{const a=document.activeElement;if(!a)return null;"
                    "const f=a.form||(a.closest&&a.closest('form'));return f?f.action:null})()")
            except Exception:
                fa = None
            hit = self.guard.path_hit(fa if isinstance(fa, str) else None, "form_action")
            if hit:
                return self._blocked(hit, "press")
            if self.confirm is not None and self.confirm.enabled:
                rejected = await self._ask(self.confirm.check_enter(self._last_typed), "press")
                if rejected:
                    return rejected

        async def go():
            if key not in KEYS:
                raise ValueError(f"unsupported key: {key}")
            await self.page.key_press(key)
            return ActionResult(True, False, wait=await self._settle())
        return await self._run(go, tool="press")

    async def _scroll_y(self) -> float:
        return (await self.page.layout())["scroll_y"]

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
