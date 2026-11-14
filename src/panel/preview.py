"""推流：Page.startScreencast；每帧立即 ack，只保留最新一帧；跟随换页；统计帧率与延迟。"""
from __future__ import annotations

import asyncio
import base64
import collections
import json
import statistics
import struct
import time

from hands.status import STATUS


class Screencaster:
    def __init__(self, service, quality: int = 70, max_w: int = 1280, max_h: int = 800):
        self.service = service
        self.quality, self.max_w, self.max_h = quality, max_w, max_h
        self.want_on = False            # 用户/默认希望开着
        self.active_session = None      # 正在推流的 CDPSession
        self.seq = 0
        self.latest: bytes | None = None    # 已打包好的二进制消息
        self.latest_meta: dict | None = None
        self._times: collections.deque = collections.deque(maxlen=600)
        self._frame_ts: dict[int, float] = {}
        self._lat: collections.deque = collections.deque(maxlen=120)
        self._listeners: list = []          # 有新帧时调用（WS 集线器）
        self._cb = None
        self._lock = asyncio.Lock()
        self._unsub = STATUS.subscribe(self._on_status)

    # ---------------- 开关 ----------------
    async def set_on(self, on: bool):
        self.want_on = on
        await self.sync()

    async def sync(self):
        """让实际推流状态与 want_on 一致（浏览器在、页面在才推）。"""
        async with self._lock:
            b = self.service.browser
            page = b.page if (b is not None and b.is_alive()) else None
            sess = page.session if page is not None else None
            if not self.want_on or sess is None:
                await self._stop_locked()
                return
            if self.active_session is sess:
                return
            await self._stop_locked()
            cb = self._on_frame
            b.conn.on("Page.screencastFrame", cb, sess.session_id)
            self._cb = (b.conn, cb, sess.session_id)
            try:
                await sess.send("Page.startScreencast", {"format": "jpeg", "quality": self.quality,
                                                         "maxWidth": self.max_w, "maxHeight": self.max_h,
                                                         "everyNthFrame": 1})
                self.active_session = sess
            except Exception:
                b.conn.off("Page.screencastFrame", cb, sess.session_id)
                self._cb = None

    async def _stop_locked(self):
        if self._cb is not None:
            conn, cb, sid = self._cb
            conn.off("Page.screencastFrame", cb, sid)
            self._cb = None
        if self.active_session is not None:
            try:
                await self.active_session.send("Page.stopScreencast", timeout=3)
            except Exception:
                pass
            self.active_session = None

    async def stop(self):
        async with self._lock:
            await self._stop_locked()

    def close(self):
        self._unsub()

    @property
    def on(self) -> bool:
        return self.active_session is not None

    def _on_status(self, name, payload):
        if name == "page_changed":
            try:
                asyncio.get_running_loop().create_task(self.sync())
            except RuntimeError:
                pass

    # ---------------- 帧 ----------------
    def _on_frame(self, p):
        sess = self.active_session
        if sess is not None:
            t = asyncio.ensure_future(sess.send("Page.screencastFrameAck", {"sessionId": p["sessionId"]}))
            t.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
        now = time.time()
        self._times.append(time.monotonic())
        md = p.get("metadata", {})
        self.seq += 1
        self._frame_ts[self.seq] = md.get("timestamp", now)
        if len(self._frame_ts) > 300:
            for k in list(self._frame_ts)[:100]:
                del self._frame_ts[k]
        url = ""
        try:
            url = self.service.page_info.get("url", "")
        except Exception:
            pass
        head = {"seq": self.seq, "ts": md.get("timestamp", now), "recv": now,
                "dw": md.get("deviceWidth"), "dh": md.get("deviceHeight"),
                "sx": md.get("scrollOffsetX", 0), "sy": md.get("scrollOffsetY", 0),
                "top": md.get("offsetTop", 0), "psf": md.get("pageScaleFactor", 1), "url": url,
                "epoch": STATUS.page_epoch}
        hb = json.dumps(head).encode("utf-8")
        self.latest = struct.pack(">I", len(hb)) + hb + base64.b64decode(p["data"])
        self.latest_meta = head
        for fn in list(self._listeners):
            try:
                fn()
            except Exception:
                pass

    def report_shown(self, seq: int, at: float):
        ts = self._frame_ts.get(seq)
        if ts is not None:
            self._lat.append(max(at - ts, 0.0))

    def fps(self) -> float:
        now = time.monotonic()
        n = sum(1 for t in self._times if now - t <= 2.0)
        return round(n / 2.0, 1)

    def latency_ms(self) -> float | None:
        if not self._lat:
            return None
        return round(statistics.median(self._lat) * 1000, 1)

    def state(self) -> dict:
        return {"on": self.on, "want_on": self.want_on, "fps": self.fps(), "latency_ms_p50": self.latency_ms(),
                "seq": self.seq, "frame": self.latest_meta}
