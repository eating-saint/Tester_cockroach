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
        self._thumb: asyncio.Task | None = None   # 桌面驱动的定时截图预览
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
            sess = None
            if page is not None and self.want_on:
                try:
                    sess = await page.panel_session()     # CDP 驱动：标签页会话；Playwright 驱动：它的 CDP 会话
                except Exception:
                    sess = None
            if self.want_on and sess is None and page is not None and not getattr(
                    getattr(b, "capabilities", None), "screencast", True):
                # 桌面驱动：没有 CDP 推流，改为定时截代理窗口做缩略预览（spec F18）
                if self._thumb is None or self._thumb.done():
                    await self._stop_locked()
                    self._thumb = asyncio.create_task(self._thumb_loop())
                    self.active_session = "thumb"
                return
            if not self.want_on or sess is None:
                await self._stop_locked()
                return
            if self.active_session is sess:
                return
            await self._stop_locked()
            cb = self._on_frame
            sess.on("Page.screencastFrame", cb)
            self._cb = (sess, cb)
            try:
                await sess.send("Page.startScreencast", {"format": "jpeg", "quality": self.quality,
                                                         "maxWidth": self.max_w, "maxHeight": self.max_h,
                                                         "everyNthFrame": 1})
                self.active_session = sess
            except Exception:
                sess.off("Page.screencastFrame", cb)
                self._cb = None

    async def _thumb_loop(self):
        import io
        from PIL import Image
        interval = 1.0
        try:
            from hands.config import desktop_settings
            interval = float(desktop_settings(self.service.config)["thumb_interval_s"])
        except Exception:
            pass
        while self.want_on:
            b = self.service.browser
            page = b.page if (b is not None and b.is_alive()) else None
            if page is not None:
                try:
                    png = await page.screenshot()
                    im = Image.open(io.BytesIO(png)).convert("RGB")
                    w, h = im.size
                    im.thumbnail((self.max_w // 2, self.max_h // 2))
                    out = io.BytesIO()
                    im.save(out, "JPEG", quality=self.quality)
                    self._publish(out.getvalue(), {"deviceWidth": w, "deviceHeight": h, "timestamp": time.time()})
                except Exception:
                    pass                      # 窗口关闭、切换中：下一轮再截
            await asyncio.sleep(interval)

    async def _stop_locked(self):
        if self._thumb is not None:
            self._thumb.cancel()
            self._thumb = None
            if self.active_session == "thumb":
                self.active_session = None
        if self._cb is not None:
            sess, cb = self._cb
            sess.off("Page.screencastFrame", cb)
            self._cb = None
        if self.active_session is not None and self.active_session != "thumb":
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
        if sess is not None and sess != "thumb":
            t = asyncio.ensure_future(sess.send("Page.screencastFrameAck", {"sessionId": p["sessionId"]}))
            t.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
        self._publish(base64.b64decode(p["data"]), p.get("metadata", {}))

    def _publish(self, jpeg: bytes, md: dict):
        now = time.time()
        self._times.append(time.monotonic())
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
        self.latest = struct.pack(">I", len(hb)) + hb + jpeg
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
