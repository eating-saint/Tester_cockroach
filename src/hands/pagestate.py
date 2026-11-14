"""M5 PageState：把 DOM 读取、状态监控、截图合成为一份结构化数据。"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .dom import Element
from .geometry import Box
from .monitor import MonitorState


@dataclass
class PageState:
    url: str
    title: str
    viewport: Box
    device_pixel_ratio: float
    elements: list[Element]
    body_text: str
    monitor: MonitorState = field(default_factory=MonitorState)
    screenshot: str | None = None
    captured_at: float = field(default_factory=time.time)

    def element(self, eid: str) -> Element | None:
        for e in self.elements:
            if e.id == eid:
                return e
        return None

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "viewport": self.viewport.to_dict(),
            "device_pixel_ratio": self.device_pixel_ratio,
            "elements": [e.to_dict() for e in self.elements],
            "body_text": self.body_text,
            "monitor": self.monitor.to_dict(),
            "screenshot": self.screenshot,
            "captured_at": self.captured_at,
        }

    def to_json(self, path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def fingerprint(self) -> tuple[str, str]:
        h = hashlib.sha1()
        for e in self.elements:
            h.update(f"{e.role}|{e.name}|{e.text}|{e.value}|{e.visible}\n".encode("utf-8"))
        h.update(self.body_text.encode("utf-8"))
        return (self.url, h.hexdigest())


async def build_page_state(page, save_dir=None, shot_name: str = "screenshot.png") -> PageState:
    from . import screenshot as shot
    from .dom import DomReader

    elements, body = await DomReader().read(page)
    url, title = await page.url(), await page.title()
    dpr = await shot.device_pixel_ratio(page)
    lay = await page.layout()
    viewport = Box(lay["scroll_x"], lay.get("layout_y", lay["scroll_y"]), lay["vw"], lay["vh"])
    shot_rel = None
    if save_dir is not None:
        d = Path(save_dir)
        d.mkdir(parents=True, exist_ok=True)
        png = await shot.capture(page)
        (d / shot_name).write_bytes(png)
        shot_rel = shot_name
    return PageState(url=url, title=title, viewport=viewport, device_pixel_ratio=dpr,
                     elements=elements, body_text=body, monitor=page.monitor.snapshot(),
                     screenshot=shot_rel)
