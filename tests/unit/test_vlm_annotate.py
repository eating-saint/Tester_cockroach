"""编号标注图：密集元素下编号牌互不重叠、都在图内；缩放后框位置换算正确。"""
import io
import os
import tempfile

from PIL import Image

from hands.geometry import Box
from hands.screenshot import annotate_numbered


def _png(w, h):
    out = io.BytesIO()
    Image.new("RGB", (w, h), (255, 255, 255)).save(out, format="PNG")
    return out.getvalue()


def _overlap_ratio(labels):
    bad = set()
    for i, (_, a) in enumerate(labels):
        for _, b in labels[i + 1:]:
            if a.intersects(b):
                bad.add(i)
    return len(bad) / len(labels)


def test_dense_grid_no_overlap():
    items, no = [], 0
    for r in range(6):
        for c in range(10):
            no += 1
            items.append((no, Box(30 + c * 24, 30 + r * 24, 20, 20)))
    png, labels = annotate_numbered(_png(320, 200), items)
    assert len(labels) == 60
    assert _overlap_ratio(labels) <= 0.05
    img = Image.open(io.BytesIO(png))
    for _, b in labels:
        assert b.x >= 0 and b.y >= 0 and b.x + b.width <= img.width and b.y + b.height <= img.height
    path = os.path.join(tempfile.gettempdir(), "hands_annotate_dense.png")
    Image.open(io.BytesIO(png)).save(path)          # 供人工目视


def test_scaled_output():
    png, labels = annotate_numbered(_png(2000, 1000), [(1, Box(100, 100, 50, 20))], dpr=2.0, scale=0.64)
    img = Image.open(io.BytesIO(png))
    assert img.size == (640, 320)                     # 2000×1000 物理像素 → CSS 1000×500 → ×0.64
    _, lab = labels[0]
    assert abs(lab.x - 64) < 1 and lab.y < 64         # 框左上 (100,100)×0.64 = (64,64)；编号牌放在框外上方
