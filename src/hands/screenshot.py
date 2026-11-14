"""M6 截图：可视区 / 整页截图；离线用 PIL 把元素框画到截图上供人工核对。"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw

from .geometry import css_to_image


async def capture(page, full_page: bool = False) -> bytes:
    return await page.screenshot(full_page)


async def device_pixel_ratio(page) -> float:
    return float(await page.evaluate("window.devicePixelRatio") or 1)


def annotate(png: bytes, elements, dpr: float = 1.0, offset: tuple[float, float] = (0, 0)) -> bytes:
    """offset：整页截图时传入滚动位置（CSS 像素），把视口坐标换算到整页坐标。"""
    img = Image.open(io.BytesIO(png)).convert("RGB")
    draw = ImageDraw.Draw(img)
    for e in elements:
        if not e.visible:
            continue
        b = css_to_image(e.box.offset(*offset), dpr)
        color = (0, 170, 0) if e.enabled else (150, 150, 150)
        draw.rectangle([b.x, b.y, b.x + b.width, b.y + b.height], outline=color, width=2)
        draw.text((b.x + 2, max(0, b.y - 11)), e.id, fill=(220, 0, 0))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def draw_trajectory(png: bytes, traj: dict, dpr: float = 1.0) -> bytes:
    """轨迹回放图：蓝框 = 目标元素，线条由绿渐变到红 = 移动方向，
    绿圈 = 起点，橙叉 = 过冲点，红点 = 实际落点。"""
    img = Image.open(io.BytesIO(png)).convert("RGB")
    draw = ImageDraw.Draw(img)
    t = traj["target"]
    draw.rectangle([t["x"] * dpr, t["y"] * dpr, (t["x"] + t["width"]) * dpr, (t["y"] + t["height"]) * dpr],
                   outline=(30, 90, 230), width=2)
    pts = [(traj["start"][0] * dpr, traj["start"][1] * dpr)] + [(x * dpr, y * dpr) for x, y, _ in traj["points"]]
    n = max(len(pts) - 1, 1)
    for i in range(len(pts) - 1):
        k = i / n
        draw.line([pts[i], pts[i + 1]], fill=(int(40 + 215 * k), int(200 - 170 * k), 40), width=2)
    for x, y in pts[1:]:
        draw.ellipse([x - 1.5, y - 1.5, x + 1.5, y + 1.5], fill=(90, 90, 90))
    sx, sy = pts[0]
    draw.ellipse([sx - 6, sy - 6, sx + 6, sy + 6], outline=(0, 170, 0), width=2)
    if traj.get("overshoot_point"):
        ox, oy = traj["overshoot_point"][0] * dpr, traj["overshoot_point"][1] * dpr
        draw.line([ox - 5, oy - 5, ox + 5, oy + 5], fill=(255, 140, 0), width=2)
        draw.line([ox - 5, oy + 5, ox + 5, oy - 5], fill=(255, 140, 0), width=2)
    lx, ly = traj["landing"][0] * dpr, traj["landing"][1] * dpr
    draw.ellipse([lx - 4, ly - 4, lx + 4, ly + 4], fill=(220, 0, 0))
    label = f'{traj.get("direction_code", "")} tpl{traj.get("template_idx", "")} {traj["duration_ms"]}ms'
    draw.text((sx + 8, sy - 6), label, fill=(220, 0, 0))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()
