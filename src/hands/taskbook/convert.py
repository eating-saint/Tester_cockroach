"""上一章旧格式（kind: expand）转成任务书（taskbook/1）。

结构部分照搬：名称、标题、开放参数、初始组、起始词、搜索方式、上限、输出方式。
自然语言的候选来源与关联条件写进备注，标"需要用积木改写"；节点流程只含一个搜索积木，补全前不能运行。
"""
from __future__ import annotations

import re

import yaml

from ..expand.spec import _groups as legacy_groups
from .blocks import FORMAT
from .book import REWRITE_MARK, TaskBook, dump
from .validate import Problem, needs_rewrite

_SORT_FIELDS = {"price": "价位", "rating": "评分"}


def detect(text: str) -> str:
    """taskbook / legacy / unknown"""
    try:
        d = yaml.safe_load(text or "")
    except yaml.YAMLError:
        return "unknown"
    if not isinstance(d, dict):
        return "unknown"
    if d.get("format") == FORMAT:
        return "taskbook"
    if d.get("kind") == "expand":
        return "legacy"
    return "unknown"


def _templ(s: str) -> str:
    """旧模板 → 新模板：${参数} → {参数}，{value} → {word}。"""
    s = re.sub(r"\$\{(\w+)\}", r"{\1}", str(s or ""))
    return s.replace("{value}", "{word}")


def _subst(v, defaults: dict):
    """非模板位置（如 output.n）里的 ${参数} 直接换成默认值。"""
    if isinstance(v, str):
        m = re.fullmatch(r"\$\{(\w+)\}", v.strip())
        if m and m.group(1) in defaults:
            return defaults[m.group(1)]
        return re.sub(r"\$\{(\w+)\}", lambda m: str(defaults.get(m.group(1), m.group(0))), v)
    if isinstance(v, list):
        return [_subst(x, defaults) for x in v]
    if isinstance(v, dict):
        return {k: _subst(x, defaults) for k, x in v.items()}
    return v


def _param(p: dict) -> dict:
    d = p.get("default", "")
    t = "int" if isinstance(d, int) and not isinstance(d, bool) else "float" if isinstance(d, float) else \
        "bool" if isinstance(d, bool) else "text"
    out = {"name": str(p.get("name")), "type": t, "default": d}
    if p.get("label"):
        out["label"] = str(p["label"])
    return out


def from_legacy(text: str) -> tuple[str, list[Problem]]:
    d = yaml.safe_load(text) or {}
    params = [_param(p) for p in d.get("params") or [] if isinstance(p, dict) and p.get("name")]
    defaults = {p["name"]: p["default"] for p in params}
    groups = legacy_groups(d.get("groups"))
    lines = [REWRITE_MARK + "由上一章旧格式转换：下面的候选来源与关联条件要用积木写进节点流程。"]
    sources = [str(s) for s in d.get("sources") or []]
    if sources:
        lines.append("候选来源：" + "；".join(_subst(s, defaults) for s in sources))
    for i, c in enumerate(d.get("conditions") or [], 1):
        if isinstance(c, dict) and c.get("text"):
            txt = _subst(str(c["text"]), defaults)
            for k, v in (c.get("params") or {}).items():
                txt = txt.replace("{" + str(k) + "}", f"{v:.0%}" if isinstance(v, float) and 0 < v <= 1 else str(v))
            lines.append(f"条件 {c.get('id') or f'c{i}'}：{txt}")
    out = dict(_subst(d.get("output") or {"type": "leaves"}, defaults))
    if out.get("type") == "ranked":
        out["sort"] = [":".join([_SORT_FIELDS.get(f, f), o or "asc"])
                       for f, _, o in (str(s).partition(":") for s in out.get("sort") or ["price:asc", "rating:desc"])]
        out.pop("fields", None)
        out.setdefault("unknown", "last")
        if "n" in out:
            out["n"] = int(out["n"])
    search = {"url": _templ(d.get("search_url", "")), "query": _templ(d.get("query", "{word}"))}
    book = TaskBook(
        name=str(d.get("name") or ""), title=str(d.get("title") or ""), note="\n".join(lines), params=params,
        groups=groups, start=str(d.get("start") or ""),
        limits={"budget": int(_subst(d.get("budget", 120), defaults)),
                "keep_tabs": int(_subst(d.get("keep_tabs", 30), defaults)), "trial_budget": 5},
        flow=[{"search": search}], output=out, status="needs_rewrite")
    probs = [Problem("flow", None, None, "由旧格式转换，需要用积木补全节点流程（候选来源与条件见备注）")]
    return dump(book), probs


def is_needs_rewrite(book: TaskBook) -> bool:
    return needs_rewrite(book)
