"""坐标换算：屏幕物理像素 ↔ 网页内容区（视口）CSS 像素。纯函数，可单测。"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from ..geometry import Box


@dataclass(frozen=True)
class Frame:
    content_left: int        # 网页内容区左上角，屏幕物理像素
    content_top: int
    content_w: int           # 内容区宽高，物理像素
    content_h: int
    scale: float             # 物理像素 / CSS 像素（显示缩放；浏览器缩放保持 100%）

    @classmethod
    def from_rect(cls, rect: tuple[int, int, int, int], scale: float) -> "Frame":
        l, t, r, b = rect
        return cls(int(l), int(t), int(r - l), int(b - t), float(scale) or 1.0)

    @property
    def css_size(self) -> tuple[float, float]:
        return self.content_w / self.scale, self.content_h / self.scale

    def to_css(self, sx: float, sy: float) -> tuple[float, float]:
        return (sx - self.content_left) / self.scale, (sy - self.content_top) / self.scale

    def to_screen(self, cx: float, cy: float) -> tuple[int, int]:
        return (int(round(self.content_left + cx * self.scale)),
                int(round(self.content_top + cy * self.scale)))

    def box_to_css(self, rect: tuple[float, float, float, float]) -> Box:
        """rect = (left, top, right, bottom) 屏幕物理像素。"""
        l, t, r, b = rect
        x, y = self.to_css(l, t)
        return Box(x, y, max(r - l, 0) / self.scale, max(b - t, 0) / self.scale)

    def contains_screen(self, sx: float, sy: float) -> bool:
        return (self.content_left <= sx < self.content_left + self.content_w
                and self.content_top <= sy < self.content_top + self.content_h)

    def to_dict(self) -> dict:
        return asdict(self)
