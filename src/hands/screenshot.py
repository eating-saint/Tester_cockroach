"""M6 截图：可视区 / 整页截图；离线用 PIL 把元素框画到截图上供人工核对。"""
from __future__ import annotations

import base64
import io

from PIL import Image, ImageDraw

from .geometry import css_to_image


async def capture(page, full_page: bool = False) -> bytes:
    params: dict = {"format": "png"}
    if full_page:
        m = await page.session.send("Page.getLayoutMetrics")
        cs = m.get("cssContentSize") or m.get("contentSize")
        params.update({"captureBeyondViewport": True,
                       "clip": {"x": 0, "y": 0, "width": cs["width"], "height": cs["height"], "scale": 1}})
    r = await page.session.send("Page.captureScreenshot", params)
    return base64.b64decode(r["data"])


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
