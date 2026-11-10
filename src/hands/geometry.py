"""坐标工具：全系统使用 CSS 像素、相对主页面视口。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Box:
    x: float
    y: float
    width: float
    height: float

    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2, self.y + self.height / 2)

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x <= self.x + self.width and self.y <= y <= self.y + self.height

    def offset(self, dx: float, dy: float) -> "Box":
        return Box(self.x + dx, self.y + dy, self.width, self.height)

    def intersects(self, other: "Box") -> bool:
        return (self.x < other.x + other.width and other.x < self.x + self.width
                and self.y < other.y + other.height and other.y < self.y + self.height)

    def to_dict(self) -> dict:
        return {"x": round(self.x, 2), "y": round(self.y, 2),
                "width": round(self.width, 2), "height": round(self.height, 2)}


def quad_to_box(quad: list[float]) -> Box:
    xs = quad[0::2]
    ys = quad[1::2]
    return Box(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def css_to_image(box: Box, dpr: float) -> Box:
    return Box(box.x * dpr, box.y * dpr, box.width * dpr, box.height * dpr)


def accumulate_offset(offsets: list[tuple[float, float]]) -> tuple[float, float]:
    return (sum(o[0] for o in offsets), sum(o[1] for o in offsets))
