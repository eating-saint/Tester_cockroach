"""提示词：独立的 .md 文件，用 {{名字}} 占位。改提示词不用改代码（spec ver1 N7）。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PH = re.compile(r"\{\{(\w+)\}\}")


def builtin_dir() -> Path:
    base = getattr(sys, "_MEIPASS", None)               # 打包后资源在 _internal 下
    if base:
        p = Path(base) / "hands" / "vlm" / "prompts"
        if p.exists():
            return p
    return Path(__file__).resolve().parent / "prompts"


class PromptBook:
    def __init__(self, directory=None):
        self.dir = Path(directory) if directory else builtin_dir()

    def text(self, name: str) -> str:
        return (self.dir / f"{name}.md").read_text(encoding="utf-8")

    def render(self, name: str, **values) -> str:
        tpl = self.text(name)

        def sub(m):
            key = m.group(1)
            if key not in values:
                raise KeyError(f"提示词 {name}.md 缺少占位值：{key}")
            return str(values[key])
        return _PH.sub(sub, tpl)

    def placeholders(self, name: str) -> set[str]:
        return set(_PH.findall(self.text(name)))
