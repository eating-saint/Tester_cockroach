"""面板设置：可调项表、范围校验、"下一个动作生效"、写回 config.yaml（保留注释）。"""
from __future__ import annotations

import copy
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from hands.config import Config
from hands.humanize import HumanizeConfig


@dataclass
class SettingSpec:
    path: str
    kind: str                    # bool | int | float | enum | seed
    label: str
    min: float | None = None
    max: float | None = None
    choices: list | None = None
    disabled_choices: list | None = None
    group: str = ""

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("path", "kind", "label", "min", "max", "choices",
                                               "disabled_choices", "group")}


M, T, J = "humanize.mouse.", "humanize.think.", "humanize.scroll_jitter."
SPECS: list[SettingSpec] = [
    SettingSpec("decision", "enum", "决策方式", choices=["rule", "vlm"], disabled_choices=["vlm"], group="决策"),
    SettingSpec("humanize.seed", "seed", "种子（留空 = 每次随机）", 0, 2 ** 32 - 1, group="种子"),
    SettingSpec(M + "enabled", "bool", "鼠标轨迹", group="鼠标轨迹"),
    SettingSpec(M + "source", "enum", "轨迹来源", choices=["template", "library"], group="鼠标轨迹"),
    SettingSpec(M + "device", "enum", "轨迹库设备", choices=["mouse", "touchpad", "mixed"], group="鼠标轨迹"),
    SettingSpec(M + "step_ms", "int", "轨迹点间隔 ms", 8, 50, group="鼠标轨迹"),
    SettingSpec(M + "min_duration_ms", "int", "最短时长 ms", 50, 2000, group="鼠标轨迹"),
    SettingSpec(M + "max_duration_ms", "int", "最长时长 ms", 100, 5000, group="鼠标轨迹"),
    SettingSpec(M + "overshoot_prob", "float", "过冲概率", 0, 1, group="鼠标轨迹"),
    SettingSpec(M + "land_margin", "float", "落点边距比例", 0, 0.45, group="鼠标轨迹"),
    SettingSpec(T + "enabled", "bool", "思考时间", group="思考时间"),
    SettingSpec(T + "prob", "float", "停顿概率", 0, 1, group="思考时间"),
    SettingSpec(T + "min_ms", "int", "最短 ms", 0, 10000, group="思考时间"),
    SettingSpec(T + "max_ms", "int", "最长 ms", 0, 10000, group="思考时间"),
    SettingSpec(J + "enabled", "bool", "滚轮乱动", group="滚轮乱动"),
    SettingSpec(J + "prob", "float", "发生概率", 0, 1, group="滚轮乱动"),
    SettingSpec(J + "min_px", "int", "最小距离 px", 1, 2000, group="滚轮乱动"),
    SettingSpec(J + "max_px", "int", "最大距离 px", 1, 2000, group="滚轮乱动"),
    SettingSpec(J + "max_ticks", "int", "最多分几下", 1, 10, group="滚轮乱动"),
    SettingSpec(J + "pause_min_ms", "int", "停顿最短 ms", 0, 5000, group="滚轮乱动"),
    SettingSpec(J + "pause_max_ms", "int", "停顿最长 ms", 0, 5000, group="滚轮乱动"),
    SettingSpec("quiet_ms", "int", "网络安静时长 ms", 100, 5000, group="等待"),
    SettingSpec("nav_timeout_s", "float", "导航超时 s", 1, 300, group="等待"),
]
SPEC_BY_PATH = {s.path: s for s in SPECS}
PAIRS = [(M + "min_duration_ms", M + "max_duration_ms"), (T + "min_ms", T + "max_ms"),
         (J + "min_px", J + "max_px"), (J + "pause_min_ms", J + "pause_max_ms")]

ABOUT = ("用途说明：鼠标轨迹和干扰项只用于已授权的测试对象（自己的网站、朋友授权的网站、本地夹具页），"
         "不用于规避网站的人机识别。遇到验证码一律转人工，由你亲手完成，程序不做任何自动破解。")


def defaults() -> dict:
    """代码里的默认值（与 config.yaml 无关）。"""
    h = HumanizeConfig().to_dict()
    c = Config()
    out = {"decision": "rule", "humanize.seed": None, "quiet_ms": c.quiet_ms, "nav_timeout_s": c.nav_timeout_s}
    for s in SPECS:
        if s.path.startswith("humanize.") and s.path != "humanize.seed":
            _, sec, key = s.path.split(".")
            out[s.path] = h[sec][key]
    return out


def from_config(cfg) -> dict:
    hd = HumanizeConfig.from_dict(getattr(cfg, "humanize", None)).to_dict()
    out = {"decision": "rule", "humanize.seed": hd.get("seed"), "quiet_ms": cfg.quiet_ms,
           "nav_timeout_s": cfg.nav_timeout_s}
    for s in SPECS:
        if s.path.startswith("humanize.") and s.path != "humanize.seed":
            _, sec, key = s.path.split(".")
            out[s.path] = hd[sec][key]
    return out


def coerce(spec: SettingSpec, v):
    """返回 (值, 错误)。"""
    if spec.kind == "bool":
        if isinstance(v, bool):
            return v, None
        return None, "应为开或关"
    if spec.kind == "enum":
        if v not in spec.choices:
            return None, f"只能是 {spec.choices}"
        if spec.disabled_choices and v in spec.disabled_choices:
            return None, "该选项未接入"
        return v, None
    if spec.kind == "seed":
        if v is None or v == "":
            return None, None
        try:
            iv = int(v)
        except (TypeError, ValueError):
            return None, "应为整数或留空"
        if not (spec.min <= iv <= spec.max):
            return None, f"范围 {int(spec.min)}–{int(spec.max)}"
        return iv, None
    if isinstance(v, bool):
        return None, "应为数字"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None, "应为数字"
    if spec.kind == "int":
        if f != int(f):
            return None, "应为整数"
        num = int(f)
    else:
        num = f
    if spec.min is not None and num < spec.min or spec.max is not None and num > spec.max:
        return None, f"范围 {spec.min}–{spec.max}"
    return num, None


class LiveSettings:
    def __init__(self, cfg):
        self.current = from_config(cfg)
        self.pending: dict = {}
        self.version = 0                 # 每次真正生效 +1（记录到动作里，便于核对"下一个动作生效"）

    def effective(self) -> dict:
        return {**self.current, **self.pending}

    def update(self, patch: dict) -> dict:
        """全部通过才进 pending；否则返回 {path: 错误} 且什么都不改。"""
        errors: dict = {}
        clean: dict = {}
        for k, v in patch.items():
            spec = SPEC_BY_PATH.get(k)
            if spec is None:
                errors[k] = "未知设置项"
                continue
            val, err = coerce(spec, v)
            if err:
                errors[k] = err
            else:
                clean[k] = val
        merged = {**self.effective(), **clean}
        for lo, hi in PAIRS:
            if merged[lo] > merged[hi]:
                errors[hi if hi in clean else lo] = "最小值不能大于最大值"
        if errors:
            return errors
        for k, v in clean.items():
            if self.current.get(k) == v:
                self.pending.pop(k, None)
            else:
                self.pending[k] = v
        return {}

    def apply_now(self, cfg, humanizer=None) -> bool:
        """把 pending 合进 current，写进 Config 和正在用的 Humanizer。在每个动作开始前调用。"""
        if not self.pending:
            return False
        self.current.update(self.pending)
        self.pending = {}
        self.version += 1
        self.write_to(cfg)
        if humanizer is not None:
            seed = humanizer.cfg.seed
            humanizer.cfg = HumanizeConfig.from_dict(cfg.humanize)
            humanizer.cfg.seed = seed
        return True

    def write_to(self, cfg):
        h = copy.deepcopy(cfg.humanize or {})
        for path, v in self.current.items():
            if path == "humanize.seed":
                h["seed"] = v
            elif path.startswith("humanize."):
                _, sec, key = path.split(".")
                sub = h.get(sec)
                if not isinstance(sub, dict):
                    sub = {"enabled": bool(sub)} if isinstance(sub, bool) else {}
                sub[key] = v
                h[sec] = sub
        cfg.humanize = h
        cfg.quiet_ms = int(self.current["quiet_ms"])
        cfg.nav_timeout_s = float(self.current["nav_timeout_s"])

    def restore(self) -> dict:
        d = defaults()
        self.pending = {k: v for k, v in d.items() if self.current.get(k) != v}
        return self.effective()

    def save_default(self, path) -> Path:
        values = {k: v for k, v in self.effective().items() if k != "decision"}
        return save_yaml_values(path, values)


# ---------------------------------------------------------------- 逐行写回 YAML（保留注释）
_LINE = re.compile(r"^(?P<ind>\s*)(?P<key>[A-Za-z_][\w\-]*):(?P<sp>\s*)(?P<val>[^#]*?)(?P<tail>\s*(#.*)?)$")


def _fmt(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        r = repr(v)
        return r
    return str(v)


def save_yaml_values(path, values: dict) -> Path:
    """values: {"a.b.c": 值}。命中的标量原地替换（保留行尾注释）；缺失的键追加到所属段末尾。"""
    p = Path(path)
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    if p.exists():
        shutil.copyfile(p, p.with_name(p.name + ".bak"))
    stack: list[tuple[int, str]] = []
    section_end: dict[str, int] = {}          # 段路径 -> 该段最后一行的下标
    section_indent: dict[str, int] = {}
    done: set[str] = set()
    for i, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#") or line.lstrip().startswith("- "):
            # 列表项和注释属于当前段
            if stack and line.strip():
                for j in range(len(stack)):
                    section_end[".".join(k for _, k in stack[: j + 1])] = i
            continue
        m = _LINE.match(line)
        if not m:
            continue
        ind = len(m.group("ind"))
        while stack and stack[-1][0] >= ind:
            stack.pop()
        key = m.group("key")
        full = ".".join([k for _, k in stack] + [key])
        for j in range(len(stack)):
            section_end[".".join(k for _, k in stack[: j + 1])] = i
        if m.group("val").strip() == "":
            stack.append((ind, key))
            section_end[full] = i
            section_indent[full] = ind
            continue
        if full in values:
            sp = m.group("sp") or " "
            val_old = m.group("val")
            new_val = _fmt(values[full])
            # 保持注释列对齐：旧值比新值长时补空格
            pad = max(len(val_old) - len(new_val), 0) if m.group("tail").strip() else 0
            tail = m.group("tail")
            if tail.strip() and pad == 0 and not tail.startswith(" "):
                tail = " " + tail
            lines[i] = f"{m.group('ind')}{key}:{sp}{new_val}{' ' * pad}{tail}"
            done.add(full)
    missing = [k for k in values if k not in done]
    inserts: dict[int, list[str]] = {}
    tail_lines: list[str] = []
    for k in missing:
        parts = k.split(".")
        parent = ".".join(parts[:-1])
        if parent and parent in section_end:
            ind = section_indent[parent] + 2
            inserts.setdefault(section_end[parent], []).append(f"{' ' * ind}{parts[-1]}: {_fmt(values[k])}")
        elif not parent:
            tail_lines.append(f"{parts[0]}: {_fmt(values[k])}")
        else:
            # 父段不存在：在文件末尾补出整条路径
            for depth, name in enumerate(parts[:-1]):
                sub = ".".join(parts[: depth + 1])
                if sub not in section_end:
                    tail_lines.append(f"{' ' * (2 * depth)}{name}:")
                    section_end[sub] = -1
                    section_indent[sub] = 2 * depth
            tail_lines.append(f"{' ' * (2 * (len(parts) - 1))}{parts[-1]}: {_fmt(values[k])}")
    out: list[str] = []
    for i, line in enumerate(lines):
        out.append(line)
        out.extend(inserts.get(i, []))
    out.extend(tail_lines)
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    return p


def spec_list() -> list[dict]:
    return [s.to_dict() for s in SPECS]


__all__ = ["SPECS", "LiveSettings", "save_yaml_values", "defaults", "from_config", "spec_list", "ABOUT"]
