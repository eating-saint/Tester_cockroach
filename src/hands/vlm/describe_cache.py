"""页面描述与"重新描述还是复用"的判断（spec ver1 F3）。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

REGION_KINDS = ["导航栏", "搜索区", "结果列表", "商品卡片", "详情区", "侧边栏", "弹窗遮罩", "表单",
                "广告推荐", "页脚", "其他"]
POSITIONS = ["顶部", "左侧", "中部", "右侧", "底部", "覆盖全页"]

DESCRIBE_SCHEMA = {
    "type": "object",
    "properties": {
        "regions": {"type": "array", "items": {
            "type": "object",
            "properties": {"position": {"type": "string", "enum": POSITIONS},
                           "kind": {"type": "string", "enum": REGION_KINDS},
                           "note": {"type": "string"}},
            "required": ["position", "kind", "note"]}},
        "overlay": {"type": "boolean"},
    },
    "required": ["regions", "overlay"],
}


@dataclass
class PageDescription:
    regions: list = field(default_factory=list)
    overlay: bool = False
    reused: bool = False
    raw: str = ""
    reason: str = ""          # 为什么重新描述 / 复用 / 失败

    def text(self) -> str:
        if not self.regions:
            return "（没有页面描述）"
        lines = [f"- {r.get('position', '')}｜{r.get('kind', '')}：{r.get('note', '')}" for r in self.regions]
        lines.append("- 有弹窗遮挡" if self.overlay else "- 没有弹窗遮挡")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return asdict(self)


def _element_set(state) -> set:
    return {(e.role, (e.name or e.text or "")[:40]) for e in state.elements if e.visible}


class DescribeCache:
    def __init__(self, settings: dict):
        self.scroll_screens = float(settings.get("redescribe_scroll", 1.0))
        self.min_similarity = float(settings.get("redescribe_change", 0.5))
        self.last: PageDescription | None = None
        self._url: str | None = None
        self._els: set = set()
        self._scroll_y: float = 0.0

    def need(self, state, last_step: dict | None) -> tuple[bool, str]:
        if self.last is None:
            return True, "首次描述"
        if state.url != self._url:
            return True, "网址变化"
        changes = (last_step or {}).get("changes") or []
        if "url" in changes or "new_tab" in changes:
            return True, "上一步打开了新页面"
        now = _element_set(state)
        union = now | self._els
        sim = (len(now & self._els) / len(union)) if union else 1.0
        if sim < self.min_similarity:
            return True, f"页面结构变化（相似度 {sim:.2f}）"
        vh = state.viewport.height or 1
        if abs(state.viewport.y - self._scroll_y) > self.scroll_screens * vh:
            return True, "滚动超过一屏"
        return False, "复用"

    def store(self, state, desc: PageDescription):
        self.last = desc
        self._url = state.url
        self._els = _element_set(state)
        self._scroll_y = state.viewport.y
