"""页面摘要：把截图和 DOM 合成模型能看懂的输入——编号、标注图、元素清单、正文摘要（spec ver1 F2）。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..screenshot import annotate_numbered, scale_png

ROLE_ZH = {
    "button": "按钮", "link": "链接", "textbox": "输入框", "searchbox": "搜索框", "combobox": "组合框",
    "checkbox": "复选框", "radio": "单选", "listbox": "列表框", "menuitem": "菜单项", "tab": "标签页",
    "switch": "开关", "slider": "滑块", "spinbutton": "数字框",
    "grid_cell": "格子",               # 桌面驱动网格兜底
}


def _clip(s: str | None, n: int) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def clue_words(brief: dict | None, goal: str = "") -> list[str]:
    """线索词：任务书 hints 优先，再加目标里长度 2–20 的词。"""
    words = [str(h).strip() for h in (brief or {}).get("hints") or [] if str(h).strip()]
    for tok in re.split(r"[\s,，。；;：:、“”\"'（）()！!？?]+", goal or ""):
        if 2 <= len(tok) <= 20 and tok not in words:
            words.append(tok)
    return words


def _hits(e, words: list[str]) -> int:
    hay = " ".join([e.name or "", e.text or "", e.context or ""]).lower()
    return sum(hay.count(w.lower()) for w in words if w)


@dataclass
class NumberedElement:
    no: int
    element_id: str
    in_viewport: bool
    occluded: bool
    line: str


@dataclass
class PageDigest:
    numbered: list[NumberedElement]
    offscreen_omitted: int
    body_summary: str
    clean_png: bytes = field(repr=False)
    annotated_png: bytes = field(repr=False)
    scale: float
    labels: list = field(default_factory=list, repr=False)   # [(编号, 编号牌矩形)]，标注图像素坐标
    _state: object = field(default=None, repr=False)

    def to_id(self, no) -> str | None:
        try:
            n = int(no)
        except (TypeError, ValueError):
            return None
        for ne in self.numbered:
            if ne.no == n:
                return ne.element_id
        return None

    def element_lines(self) -> str:
        lines = [ne.line for ne in self.numbered]
        if self.offscreen_omitted:
            lines.append(f"（另有 {self.offscreen_omitted} 个视口外元素未列出）")
        return "\n".join(lines) if lines else "（没有可交互元素）"

    def stable_features(self, element_id: str) -> dict | None:
        return stable_features(self._state, element_id)

    def to_dict(self) -> dict:
        return {
            "scale": self.scale, "offscreen_omitted": self.offscreen_omitted,
            "numbered": [{"no": n.no, "element_id": n.element_id, "in_viewport": n.in_viewport,
                          "occluded": n.occluded, "line": n.line,
                          "features": stable_features(self._state, n.element_id)} for n in self.numbered],
            "labels": [{"no": no, "box": b.to_dict()} for no, b in self.labels],
        }

    @classmethod
    def build(cls, state, png: bytes, brief: dict | None, goal: str, settings: dict) -> "PageDigest":
        words = clue_words(brief, goal)
        occluded = set(getattr(state, "occluded", None) or [])
        vh = state.viewport.height or 1
        vis = [e for e in state.elements if e.visible]
        inside = _reading_order([e for e in vis if e.in_viewport])

        def dist(e) -> float:
            if e.box.y >= vh:
                return e.box.y - vh
            return max(0.0, -(e.box.y + e.box.height))
        outside = sorted((e for e in vis if not e.in_viewport), key=lambda e: (-_hits(e, words), dist(e)))
        k = int(settings.get("offscreen_top_k", 40))
        chosen = inside + outside[:k]
        numbered = []
        for i, e in enumerate(chosen, 1):
            numbered.append(NumberedElement(i, e.id, e.in_viewport, e.id in occluded,
                                            _line(i, e, e.id in occluded)))
        dpr = state.device_pixel_ratio or 1.0
        css_w, css_h = state.viewport.width or 1, state.viewport.height or 1
        scale = min(dpr, float(settings.get("max_side", 1280)) / max(css_w, css_h))
        clean = scale_png(png, scale / dpr)
        items = [(n.no, e.box) for n, e in zip(numbered, chosen) if n.in_viewport]
        annotated, labels = annotate_numbered(png, items, dpr, scale)
        body = body_summary(state.body_text, words, int(settings.get("body_chars", 1500)))
        return cls(numbered, max(0, len(outside) - k), body, clean, annotated, scale, labels, state)


def _reading_order(els: list, band: float = 8) -> list:
    """从上到下、从左到右：与本行第一个元素纵向相差不超过 band 像素的算同一行。"""
    rows: list[list] = []
    for e in sorted(els, key=lambda e: e.box.y):
        if rows and e.box.y - rows[-1][0].box.y <= band:
            rows[-1].append(e)
        else:
            rows.append([e])
    return [e for row in rows for e in sorted(row, key=lambda e: e.box.x)]


def _line(no: int, e, occluded: bool) -> str:
    parts = [str(no), ROLE_ZH.get(e.role, e.role or "元素")]
    label = _clip(e.name or e.text, 60) or "（无名称）"
    parts.append(label)
    ctx = _clip(e.context, 80)
    if ctx and ctx != label and label not in ("（无名称）",) and len(ctx) > len(label) + 4:
        parts.append("所在块：" + ctx)
    if not e.enabled:
        parts.append("不可用")
    if e.href:
        parts.append("链接 " + _clip(e.href, 60))
    if e.value:
        parts.append("当前值 " + _clip(e.value, 40))
    if not e.in_viewport:
        parts.append("视口外")
    if occluded:
        parts.append("被遮挡")
    return " | ".join(parts)


def stable_features(state, element_id: str) -> dict | None:
    """被操作元素的稳定特征：编号每步都会变，这些特征供以后把流程固化成脚本回放（F8）。"""
    if state is None:
        return None
    e = state.element(element_id)
    if e is None:
        return None
    vw, vh = state.viewport.width or 1, state.viewport.height or 1
    return {"role": e.role, "name": _clip(e.name, 120), "text": _clip(e.text, 120), "href": e.href,
            "rel_box": {"x": round(e.box.x / vw, 3), "y": round(e.box.y / vh, 3),
                        "w": round(e.box.width / vw, 3), "h": round(e.box.height / vh, 3)},
            "frame_path": list(e.frame_path)}


PROSE_MIN = 60          # 一行至少这么长才算正文句子（导航、目录、标签云都是短行）
PROSE_RUN = 2           # 连续这么多行长句，才认为正文从这里开始


def prose_start(text: str) -> int | None:
    """正文大致从哪里开始：第一处连续 PROSE_RUN 行长句的位置；找不到返回 None。"""
    pos, run_start, runs = 0, None, 0
    for ln in text.split("\n"):
        if len(ln) >= PROSE_MIN:
            if run_start is None:
                run_start, runs = pos, 0
            runs += 1
            if runs >= PROSE_RUN:
                return run_start
        else:
            run_start, runs = None, 0
        pos += len(ln) + 1
    return None


def _with_prose(text: str, picked: str, limit: int) -> str:
    """命中片段撑满预算时，匀一半给正文开头——否则页头的导航、目录会把正文挤掉。"""
    p = prose_start(text)
    if p is None:
        return picked[:limit]
    keep = limit // 2
    if text[p:p + 40] in picked[:keep]:        # 正文已经在保留的部分里，不重复拼
        return picked[:limit]
    return picked[:keep].rstrip() + "……" + text[p:p + limit - keep - 2]


def body_summary(text: str, words: list[str], limit: int) -> str:
    text = re.sub(r"[ \t]+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    low = text.lower()
    spans: list[list[int]] = []
    for w in words:
        wl = w.lower()
        start = 0
        while wl and (i := low.find(wl, start)) >= 0:
            spans.append([max(0, i - 200), min(len(text), i + len(wl) + 200)])
            start = i + len(wl)
    if not spans:
        return _with_prose(text, text[:limit], limit)
    spans.sort()
    merged = [spans[0]]
    for a, b in spans[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out = "……".join(text[a:b] for a, b in merged)
    return out if len(out) <= limit else _with_prose(text, out, limit)


def text_lines(text: str | None, limit: int = 120, width: int = 80) -> list[str]:
    """正文切成文本行供 4b 按编号（T1…Tn）选：去首尾空白、去空行、按规范化文字去重，每行截到 width 字。"""
    import unicodedata
    out, seen = [], set()
    for raw in (text or "").splitlines():
        line = raw.strip().strip("　").strip()
        if not line:
            continue
        key = re.sub(r"\s+", "", unicodedata.normalize("NFKC", line)).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(line[:width])
        if len(out) >= limit:
            break
    return out
