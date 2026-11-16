"""M6 截图：可视区 / 整页截图；离线用 PIL 把元素框画到截图上供人工核对。"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw

from .geometry import Box, css_to_image


async def capture(page, full_page: bool = False) -> bytes:
    return await page.screenshot(full_page)


async def device_pixel_ratio(page) -> float:
    own = getattr(page, "device_pixel_ratio", None)       # 桌面驱动：显示缩放，不执行脚本
    if callable(own):
        return float(await own() or 1)
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


_FONT_CACHE: dict = {}


def _font(size: int):
    from PIL import ImageFont
    if size not in _FONT_CACHE:
        f = None
        for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
            try:
                f = ImageFont.truetype(name, size)
                break
            except OSError:
                continue
        if f is None:
            try:
                f = ImageFont.load_default(size=size)
            except TypeError:                      # 旧版 Pillow 没有 size 参数
                f = ImageFont.load_default()
        _FONT_CACHE[size] = f
    return _FONT_CACHE[size]


def scale_png(png: bytes, factor: float) -> bytes:
    """按比例缩放截图；factor >= 1 时原样返回。"""
    if factor >= 0.999:
        return png
    img = Image.open(io.BytesIO(png)).convert("RGB")
    img = img.resize((max(1, round(img.width * factor)), max(1, round(img.height * factor))), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def annotate_numbered(png: bytes, items, dpr: float = 1.0, scale: float | None = None):
    """ver1 编号标注图：红框 + 黄底黑字编号牌，编号牌互不遮挡。

    items：[(编号, Box)]，Box 为视口 CSS 像素。scale：输出图每 CSS 像素对应几个像素（None = dpr，即不缩放）。
    返回 (PNG 字节, [(编号, 编号牌矩形)])，矩形为输出图像素坐标。
    """
    img = Image.open(io.BytesIO(png)).convert("RGB")
    scale = dpr if scale is None else scale
    factor = scale / dpr
    if abs(factor - 1) > 1e-3:
        img = img.resize((max(1, round(img.width * factor)), max(1, round(img.height * factor))), Image.LANCZOS)
    draw = ImageDraw.Draw(img)
    W, H = img.size
    boxes = [(no, Box(b.x * scale, b.y * scale, b.width * scale, b.height * scale)) for no, b in items]
    for _, b in boxes:
        draw.rectangle([b.x, b.y, b.x + b.width, b.y + b.height], outline=(230, 0, 0), width=2)
    placed: list[tuple[int, Box]] = []

    def fits(r: Box, own: int, strict: bool) -> bool:
        if r.x < 0 or r.y < 0 or r.x + r.width > W or r.y + r.height > H:
            return False
        if any(r.intersects(p) for _, p in placed):
            return False
        # strict：也不压别的元素框——编号牌压进相邻元素的框里，看图时容易配错（比对实测：8 号读成相邻的 7 号）
        return not strict or not any(r.intersects(ob) for i, (_, ob) in enumerate(boxes) if i != own)

    for idx, (no, b) in enumerate(boxes):
        label = str(no)
        chosen = None
        for strict in (True, False):
            for size in (14, 12, 10):
                f = _font(size)
                l, t, r, btm = draw.textbbox((0, 0), label, font=f)
                lw, lh = (r - l) + 4, (btm - t) + 4
                cands = [(b.x, b.y - lh), (b.x + b.width - lw, b.y - lh), (b.x, b.y + b.height),
                         (b.x + b.width - lw, b.y + b.height), (b.x, b.y), (b.x - lw, b.y), (b.x + b.width, b.y)]
                for cx, cy in cands:
                    rect = Box(cx, cy, lw, lh)
                    if fits(rect, idx, strict):
                        chosen = (rect, f, l, t)
                        break
                if chosen:
                    break
            if chosen:
                break
        if chosen is None:                          # 实在放不下：框内左上，最小字号，尽量留在图内
            f = _font(10)
            l, t, r, btm = draw.textbbox((0, 0), label, font=f)
            lw, lh = (r - l) + 4, (btm - t) + 4
            chosen = (Box(min(max(b.x, 0), W - lw), min(max(b.y, 0), H - lh), lw, lh), f, l, t)
        rect, f, l, t = chosen
        draw.rectangle([rect.x, rect.y, rect.x + rect.width, rect.y + rect.height], fill=(255, 230, 0),
                       outline=(0, 0, 0), width=1)
        draw.text((rect.x + 2 - l, rect.y + 2 - t), label, fill=(0, 0, 0), font=f)
        placed.append((no, rect))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue(), placed


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
