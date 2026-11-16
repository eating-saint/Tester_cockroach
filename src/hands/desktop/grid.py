"""网格兜底（spec F5）：无障碍树读不到可用元素的区域，叠一层带编号的格子让模型选。"""
from __future__ import annotations

from dataclasses import dataclass

from ..dom import Element
from ..geometry import Box


@dataclass(frozen=True)
class GridRef:
    cell: int
    box: Box


def _overlap(a: Box, b: Box) -> float:
    w = min(a.x + a.width, b.x + b.width) - max(a.x, b.x)
    h = min(a.y + a.height, b.y + b.height) - max(a.y, b.y)
    return max(w, 0) * max(h, 0)


def make_grid(vw: float, vh: float, cols: int, rows: int, occupied: list[Box] | None = None,
              start_id: int = 1, max_overlap: float = 0.3) -> list[Element]:
    """格子按行编号（格 1 在左上）；与已有元素框重叠超过 max_overlap 的格子跳过。
    元素 id 从 e{start_id} 起接着现有元素编号。"""
    cw, ch = vw / cols, vh / rows
    occupied = [b for b in (occupied or []) if b.width > 0 and b.height > 0]
    out: list[Element] = []
    n = 0
    for r in range(rows):
        for c in range(cols):
            n += 1
            box = Box(c * cw, r * ch, cw, ch)
            area = cw * ch
            if any(_overlap(box, o) / area > max_overlap for o in occupied):
                continue
            out.append(Element(id=f"e{start_id + len(out)}", role="grid_cell", name=f"格 {n}", text=f"格 {n}",
                               tag="", href=None, value=None, box=box, visible=True, in_viewport=True,
                               enabled=True, _ref=GridRef(n, box), source="grid"))
    return out
