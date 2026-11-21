"""任务书文件（tasks/*.yaml，format: taskbook/1）：解析、序列化、开放参数、模板替换、保存（备份与版本冲突）。"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

from .blocks import FORMAT

REWRITE_MARK = "【需要用积木改写】"          # 旧格式转换来的任务书：备注以此开头
DEFAULT_LIMITS = {"budget": 120, "keep_tabs": 30, "trial_budget": 5}
PARAM_TYPES = ("text", "int", "float", "bool")
NAME_RE = re.compile(r"^[\w\-]+$")
VAR_RE = re.compile(r"\{([^{}\s]*)\}")


class BookError(ValueError):
    """任务书读不懂（格式版本、结构）。"""


class BookConflict(PermissionError):
    """保存冲突：已被另一端修改 / 已存在同名任务书。"""


@dataclass
class TaskBook:
    format: str = FORMAT
    name: str = ""
    title: str = ""
    note: str = ""
    params: list[dict] = field(default_factory=list)
    groups: list[tuple[str, list[str]]] = field(default_factory=list)
    start: str = ""
    limits: dict = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    flow: list[dict] = field(default_factory=list)
    output: dict = field(default_factory=lambda: {"type": "leaves"})
    values: dict = field(default_factory=dict)      # 本次运行的参数值
    rev: str = ""                                   # 文件内容摘要
    status: str = "ok"                              # ok / draft / needs_rewrite
    text: str = ""                                  # 原文

    def to_dict(self) -> dict:
        """任务书内容（不含运行时字段），键顺序固定。"""
        return {"format": self.format, "name": self.name, "title": self.title, "note": self.note,
                "params": self.params, "groups": [{k: list(vs)} for k, vs in self.groups], "start": self.start,
                "limits": self.limits, "flow": self.flow, "output": self.output}

    def json(self) -> dict:
        return {**self.to_dict(), "values": self.values, "rev": self.rev, "status": self.status}


def rev_of(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _groups(raw) -> list[tuple[str, list[str]]]:
    if raw is None:
        return []
    items = list(raw.items()) if isinstance(raw, dict) else []
    if isinstance(raw, list):
        for g in raw:
            if not isinstance(g, dict) or len(g) != 1:
                raise BookError("groups 每一项要写成「键: [值, 值]」")
            items.extend(g.items())
    elif not isinstance(raw, dict):
        raise BookError("groups 要写成列表，每项「键: [值, 值]」")
    out = []
    for k, vs in items:
        if vs is None:
            vs = []
        if not isinstance(vs, list):
            raise BookError(f"组「{k}」的值要写成列表 [值, 值]")
        out.append((str(k), [str(v) for v in vs]))
    return out


def parse(text: str, name: str | None = None) -> TaskBook:
    try:
        data = yaml.safe_load(text or "") or {}
    except yaml.YAMLError as e:
        raise BookError(f"YAML 写法有误：{e}") from e
    if not isinstance(data, dict):
        raise BookError("任务书顶层要是「字段: 值」的形式")
    fmt = data.get("format")
    if fmt != FORMAT:
        raise BookError(f"不认识的格式版本 {fmt!r}，当前支持 {FORMAT}")
    flow = data.get("flow") or []
    if not isinstance(flow, list):
        raise BookError("flow 要写成积木列表")
    limits = {**DEFAULT_LIMITS, **(data.get("limits") or {})}
    params = data.get("params") or []
    if not isinstance(params, list) or not all(isinstance(p, dict) for p in params):
        raise BookError("params 要写成列表，每项 {name, type, default, label}")
    output = data.get("output") or {"type": "leaves"}
    if not isinstance(output, dict):
        raise BookError("output 要写成 {type: leaves} 或 {type: ranked, sort: [...], n: 3}")
    return TaskBook(
        format=fmt, name=str(data.get("name") or name or ""), title=str(data.get("title") or ""),
        note=str(data.get("note") or ""), params=params, groups=_groups(data.get("groups")),
        start=str(data.get("start") or ""), limits=limits, flow=flow, output=output,
        rev=rev_of(text), text=text)


def dump(book: TaskBook) -> str:
    return yaml.safe_dump(book.to_dict(), allow_unicode=True, sort_keys=False, default_flow_style=None, width=120)


def load(path) -> TaskBook:
    p = Path(path)
    return parse(p.read_text(encoding="utf-8"), p.stem)


# ---------------- 开放参数与模板 ----------------
def _cast(spec: dict, v):
    t = spec.get("type") or "text"
    name = spec.get("name")
    try:
        if t == "int":
            v = int(v)
        elif t == "float":
            v = float(v)
        elif t == "bool":
            v = v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on", "是")
        else:
            v = "" if v is None else str(v)
    except (TypeError, ValueError) as e:
        raise BookError(f"参数 {name} 要填 {t}：{v!r}") from e
    if t in ("int", "float"):
        if spec.get("min") is not None and v < spec["min"]:
            raise BookError(f"参数 {name} 不能小于 {spec['min']}")
        if spec.get("max") is not None and v > spec["max"]:
            raise BookError(f"参数 {name} 不能大于 {spec['max']}")
    return v


def bind(book: TaskBook, values: dict | None) -> TaskBook:
    """填开放参数：按类型转换，缺省用 default。未声明的参数名报错。"""
    values = dict(values or {})
    declared = {p.get("name"): p for p in book.params}
    unknown = [k for k in values if k not in declared]
    if unknown:
        raise BookError(f"任务书没有这些参数：{', '.join(unknown)}")
    out = {}
    for name, spec in declared.items():
        out[name] = _cast(spec, values[name] if name in values else spec.get("default"))
    return replace(book, values=out)


def render(template, word: str = "", key: str = "", values: dict | None = None, keep: tuple = ()) -> str:
    """替换 {word}、{key}、{参数名}；keep 里的变量原样保留（如 search 的 {q}）。未知变量报错。"""
    if template is None:
        return ""
    vars_ = {"word": word or "", "key": key or "", **{k: "" if v is None else str(v) for k, v in (values or {}).items()}}

    def sub(m):
        name = m.group(1)
        if name in keep:
            return m.group(0)
        if name not in vars_:
            raise BookError(f"模板变量 {{{name}}} 没有来源")
        return vars_[name]
    return VAR_RE.sub(sub, str(template))


def template_vars(template) -> list[str]:
    return VAR_RE.findall(str(template or ""))


# ---------------- 保存 ----------------
def save(tasks_dir, name: str, text: str, rev: str | None, save_as: bool = False) -> str:
    """覆盖保存（rev 要与磁盘一致，覆盖前备份到 .bak/）或另存为（不能同名）。返回新 rev。"""
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise BookError("名称只能用字母、数字、汉字、下划线和连字符")
    d = Path(tasks_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.yaml"
    if path.exists():
        if save_as:
            raise BookConflict("已存在同名任务书")
        cur = path.read_text(encoding="utf-8")
        if rev is not None and rev_of(cur) != rev:
            raise BookConflict("已被另一端修改，请刷新")
        bak = d / ".bak"
        bak.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = bak / f"{name}.{stamp}.yaml"
        i = 1
        while target.exists():
            i += 1
            target = bak / f"{name}.{stamp}-{i}.yaml"
        shutil.copy2(path, target)
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return rev_of(text)
