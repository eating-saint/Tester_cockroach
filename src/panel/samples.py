"""轨迹样本库（服务端）：复核作废规则、去重、导入导出、进度格。"""
from __future__ import annotations

import json
from pathlib import Path

from hands.trajlib import VERSION, Library, Sample

MAX_MS = 5000
MIN_POINTS = 8


# ---------------------------------------------------------------- 真实网站采集：注入的只读监听
WORLD = "panel_sample"
BINDING = "__panelSample"
KEEP_MS = 7000

# 运行在 CDP 隔离环境（isolated world）里：页面自己的脚本看不到这些变量和函数。
# 只读：capture + passive 监听，不调用 preventDefault，不改 DOM。
# 平时只在页内保留最近 7 秒的指针移动；只有用户按下指针时，才把这一段点位回传给面板（不含任何页面文字）。
LISTENER_JS = r"""
(() => {
  if (window.__psOn) return;
  window.__psOn = 1;
  const doc = Math.random().toString(36).slice(2);
  const buf = [];
  const send = (m) => {
    if (window.__psDead) return;
    try { m.doc = doc; m.dpr = devicePixelRatio; m.host = location.hostname;
          %(binding)s(JSON.stringify(m)); } catch (e) {}
  };
  const o = { capture: true, passive: true };
  addEventListener("pointermove", (e) => {
    if (window.__psDead) return;
    const evs = e.getCoalescedEvents ? e.getCoalescedEvents() : [];
    for (const v of (evs.length ? evs : [e])) buf.push([v.clientX, v.clientY, v.timeStamp]);
    const cut = e.timeStamp - %(keep)d;
    let i = 0; while (i < buf.length && buf[i][2] < cut) i++;
    if (i) buf.splice(0, i);
  }, o);
  addEventListener("pointerdown", (e) => {
    send({ k: "down", x: e.clientX, y: e.clientY, t: e.timeStamp, pts: buf.slice() });
  }, o);
  addEventListener("mouseout", (e) => { if (!e.relatedTarget) send({ k: "leave", t: e.timeStamp }); }, o);
})();
""" % {"binding": BINDING, "keep": KEEP_MS}


class RealSampleInjector:
    """在一个页面会话上装 / 卸只读监听。装：绑定 → 新文档自动注入 → 当前文档建隔离环境并执行。"""

    def __init__(self, on_message):
        self.on_message = on_message          # 收到页面回传（dict）时调用
        self.session = None
        self.script_id = None
        self.context_id = None
        self.contexts: set = set()
        self.active = False

    def _ctx(self, params):
        c = params.get("context") or {}
        if c.get("name") == WORLD:
            self.contexts.add(c.get("id"))

    def _cb(self, params):
        if not self.active or params.get("name") != BINDING:
            return
        try:
            self.on_message(json.loads(params.get("payload") or "{}"))
        except ValueError:
            pass

    async def install(self, session):
        self.session = session
        self.active = True
        session.on("Runtime.bindingCalled", self._cb)
        session.on("Runtime.executionContextCreated", self._ctx)
        await session.send("Runtime.enable")
        await session.send("Runtime.addBinding", {"name": BINDING, "executionContextName": WORLD})
        r = await session.send("Page.addScriptToEvaluateOnNewDocument", {"source": LISTENER_JS, "worldName": WORLD})
        self.script_id = r.get("identifier")
        tree = await session.send("Page.getFrameTree")
        w = await session.send("Page.createIsolatedWorld", {"frameId": tree["frameTree"]["frame"]["id"],
                                                           "worldName": WORLD})
        self.context_id = w.get("executionContextId")
        self.contexts.add(self.context_id)
        await session.send("Runtime.evaluate", {"expression": LISTENER_JS, "contextId": self.context_id})
        try:
            await session.send("DOM.enable")
            await session.send("Overlay.enable")
        except Exception:
            pass

    async def highlight(self, el) -> None:
        s = self.session
        color = {"r": 249, "g": 115, "b": 22}
        ref = getattr(el, "_ref", None)
        try:
            if ref is not None and ref.session_id in (None, getattr(s, "session_id", None)):
                await s.send("Overlay.highlightNode", {
                    "backendNodeId": ref.backend_node_id,
                    "highlightConfig": {"showInfo": False, "contentColor": {**color, "a": 0.25},
                                        "borderColor": {**color, "a": 1.0}}})
                return
        except Exception:
            pass
        b = el.box
        await s.send("Overlay.highlightRect", {"x": int(b.x), "y": int(b.y), "width": int(b.width),
                                               "height": int(b.height), "color": {**color, "a": 0.25},
                                               "outlineColor": {**color, "a": 1.0}})

    async def uninstall(self):
        """撤除：之后收到的回传一律丢弃；页面里的监听在当前文档内静默，换页后不再注入。"""
        self.active = False
        s = self.session
        if s is None:
            return
        s.off("Runtime.bindingCalled", self._cb)
        s.off("Runtime.executionContextCreated", self._ctx)
        calls = [("Overlay.hideHighlight", {}),
                 ("Page.removeScriptToEvaluateOnNewDocument", {"identifier": self.script_id} if self.script_id else None),
                 ("Runtime.removeBinding", {"name": BINDING})]
        calls += [("Runtime.evaluate", {"expression": "window.__psDead = 1", "contextId": c}) for c in self.contexts]
        for method, params in calls:
            if params is None:
                continue
            try:
                await s.send(method, params)
            except Exception:
                pass
        self.session = None


class SampleStore:
    def __init__(self, path):
        self.path = Path(path)
        self.library = Library.load(self.path)

    def reload(self):
        self.library = Library.load(self.path)

    def _save(self):
        self.library.save(self.path)

    @staticmethod
    def check(raw: dict) -> str | None:
        """按顺序返回第一个作废原因；通过返回 None。"""
        pts = raw.get("points") or []
        t = raw.get("target") or {}
        end = raw.get("end") or (pts[-1][:2] if pts else None)
        if end is None:
            return "too_few"
        x, y = float(end[0]), float(end[1])
        tx, ty, tw, th = (float(t.get(k, 0)) for k in ("x", "y", "width", "height"))
        if not (tx <= x <= tx + tw and ty <= y <= ty + th):
            return "miss"
        if raw.get("left_area"):
            return "left_area"
        if pts and int(pts[-1][2]) > MAX_MS:
            return "too_slow"
        if len(pts) < MIN_POINTS:
            return "too_few"
        return None

    def add(self, raw: dict) -> dict:
        reason = self.check(raw)
        if reason:
            return {"ok": False, "reason": reason}
        s = Sample.from_raw(raw)
        if s.id in self.library.ids():
            return {"ok": False, "reason": "duplicate", "id": s.id}
        self.library.samples.append(s)
        self._save()
        return {"ok": True, "id": s.id, "direction": s.direction}

    def delete(self, sid: str) -> bool:
        n = len(self.library.samples)
        self.library.samples = [s for s in self.library.samples if s.id != sid]
        if len(self.library.samples) != n:
            self._save()
            return True
        return False

    def list(self, device: str | None = None, source: str | None = None) -> list[dict]:
        out = []
        for s in self.library.samples:
            if device and s.device != device:
                continue
            if source and s.source != source:
                continue
            out.append({"id": s.id, "device": s.device, "source": s.source, "domain": s.domain,
                        "direction": s.direction, "duration_ms": s.duration_ms, "n_points": len(s.points),
                        "created": s.created})
        return out

    def get(self, sid: str) -> dict | None:
        for s in self.library.samples:
            if s.id == sid:
                return s.to_dict()
        return None

    def grid(self) -> dict:
        g = {dev: {d: {"local": 0, "real": 0} for d in range(8)} for dev in ("mouse", "touchpad")}
        for s in self.library.samples:
            cell = g.setdefault(s.device, {d: {"local": 0, "real": 0} for d in range(8)})[s.direction]
            cell[s.source if s.source in ("local", "real") else "local"] += 1
        return g

    def export(self) -> bytes:
        return self.library.dumps().encode("utf-8")

    def import_(self, data: bytes, mode: str = "merge") -> dict:
        try:
            other = Library.from_dict(json.loads(data.decode("utf-8")))
        except (ValueError, KeyError, TypeError) as e:
            return {"ok": False, "error": str(e)}
        if mode == "replace":
            self.library = Library(VERSION, other.samples)
            added = len(other.samples)
        else:
            have = self.library.ids()
            new = [s for s in other.samples if s.id not in have]
            self.library.samples.extend(new)
            added = len(new)
        self._save()
        return {"ok": True, "added": added, "total": len(self.library.samples)}
