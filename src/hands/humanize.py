"""鼠标轨迹模块 + 可选干扰项（思考时间、滚轮抖动后回滚）。

设计要点
- 全部随机数来自一个带种子的 random.Random：同一个种子、同样的页面，产生完全相同的轨迹和干扰，可以重放。
- 默认全部关闭；在 config.yaml 的 humanize 段或命令行 --humanize 打开。
- 只负责"怎么动"，不负责"点哪里"：目标框由动作层给出，落点保证在目标框内。
- 坐标一律是 CSS 像素、相对主页面视口（和全系统一致）。

轨迹算法（技术方案细则 v1 第五节）
1. 方向：起点到目标中心的连线角度，按 45° 一档归入 8 个方向（0=右，顺时针，2=下，4=左，6=上）。
2. 模板：每个方向 5 套模板，每次随机抽一套。模板决定弧度、S 形、速度曲线、中途抖动、过冲大小、是否中途停顿。
3. 主段：三次贝塞尔曲线从起点走到"过冲点"，叠加首尾为 0 的轻微横向抖动。
4. 回拉：有过冲时，再用一小段曲线从过冲点拉回真正的落点。
5. 落点：目标框内随机（偏向中心的三角分布），留出边距。
6. 时长：按 Fitts 定律随距离和目标大小增长，再乘一个随机系数。
"""
from __future__ import annotations

import asyncio
import math
import random
import secrets
import zlib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .geometry import Box

DIRECTION_NAMES = ["右", "右下", "下", "左下", "左", "左上", "上", "右上"]
DIRECTION_CODES = ["E", "SE", "S", "SW", "W", "NW", "N", "NE"]   # 画图用（默认字体不含中文）


# ---------------------------------------------------------------- 配置
@dataclass
class MouseConfig:
    enabled: bool = False
    step_ms: int = 16                 # 相邻轨迹点的时间间隔（约 60Hz）
    min_duration_ms: int = 180
    max_duration_ms: int = 1100
    overshoot_prob: float = 0.7       # 模板允许过冲时，实际发生过冲的概率
    land_margin: float = 0.2          # 落点距目标框边缘至少留出的比例
    source: str = "template"          # "template" = 40 套模板；"library" = 真实轨迹库回放
    device: str = "mouse"             # 轨迹库里挑哪种设备的样本："mouse" | "touchpad" | "mixed"
    library_path: str = "trajlib/samples.json"   # 相对数据目录（DATA_ROOT）


@dataclass
class ThinkConfig:
    enabled: bool = False
    prob: float = 0.5                 # 每个动作前停顿的概率
    min_ms: int = 300
    max_ms: int = 1500


@dataclass
class ScrollJitterConfig:
    enabled: bool = False
    prob: float = 0.3                 # 每个动作前发生一次"滚轮乱动"的概率
    min_px: int = 60                  # 单次乱动的总滚动距离范围
    max_px: int = 320
    max_ticks: int = 3                # 分几下滚完
    pause_min_ms: int = 150           # 滚动中间、回滚之前的停顿
    pause_max_ms: int = 600


@dataclass
class HumanizeConfig:
    seed: int | None = None           # None：每次运行随机生成一个种子，并写进报告以便重放
    mouse: MouseConfig = field(default_factory=MouseConfig)
    think: ThinkConfig = field(default_factory=ThinkConfig)
    scroll_jitter: ScrollJitterConfig = field(default_factory=ScrollJitterConfig)

    @property
    def any_enabled(self) -> bool:
        return self.mouse.enabled or self.think.enabled or self.scroll_jitter.enabled

    @classmethod
    def from_dict(cls, d: dict | None) -> "HumanizeConfig":
        d = d or {}

        def sub(klass, key):
            raw = d.get(key)
            if isinstance(raw, bool):
                return klass(enabled=raw)
            known = {f for f in klass.__dataclass_fields__}
            return klass(**{k: v for k, v in (raw or {}).items() if k in known})

        return cls(seed=d.get("seed"), mouse=sub(MouseConfig, "mouse"), think=sub(ThinkConfig, "think"),
                   scroll_jitter=sub(ScrollJitterConfig, "scroll_jitter"))

    def to_dict(self) -> dict:
        return asdict(self)


def apply_cli(humanize: dict | None, enable: bool = False, seed: int | None = None,
              only: str | None = None) -> dict:
    """命令行开关：--humanize 打开全部三项（或 --humanize-only mouse,think 只开部分）；--seed 固定种子。"""
    d = {k: (dict(v) if isinstance(v, dict) else ({"enabled": v} if isinstance(v, bool) else v))
         for k, v in (humanize or {}).items()}
    names = [x.strip() for x in only.split(",") if x.strip()] if only else (
        ["mouse", "think", "scroll_jitter"] if enable else [])
    for n in names:
        if n not in ("mouse", "think", "scroll_jitter"):
            raise ValueError(f"unknown humanize item: {n}")
        d[n] = {**(d.get(n) or {}), "enabled": True}
    if seed is not None:
        d["seed"] = seed
    return d


def derive_seed(base: int, *parts) -> int:
    """由运行种子和用例名、重跑次数派生子种子：用例顺序变化不影响各自的随机序列。"""
    key = "|".join([str(base)] + [str(p) for p in parts]).encode("utf-8")
    return zlib.crc32(key)


def new_seed() -> int:
    return secrets.randbits(32)


# ---------------------------------------------------------------- 模板
@dataclass(frozen=True)
class Template:
    name: str
    curve1: float          # 第一个控制点的横向偏移（占距离的比例，带符号：正 = 向行进方向左侧弯）
    curve2: float          # 第二个控制点的横向偏移；与 curve1 异号即 S 形
    wobble: float          # 中途横向抖动幅度（占距离的比例）
    wobble_cycles: float   # 抖动周期数
    overshoot: float       # 过冲距离（占距离的比例），0 = 不过冲
    ease: str              # 速度曲线：in_out / out / in_out_strong
    pause_at: float | None  # 在路径的这个位置停顿一下（0–1），None = 不停


BASE_TEMPLATES = [
    Template("平滑弧线", 0.12, 0.10, 0.010, 1.0, 0.06, "in_out", None),
    Template("快速直线", 0.03, 0.02, 0.015, 1.5, 0.10, "out", None),
    Template("大弧线", 0.24, 0.18, 0.008, 1.0, 0.04, "in_out", None),
    Template("S 形", 0.14, -0.12, 0.012, 2.0, 0.05, "in_out_strong", None),
    Template("中途犹豫", 0.08, 0.06, 0.010, 1.0, 0.03, "in_out", 0.6),
]


def _templates_for(direction: int) -> list[Template]:
    """8 个方向各 5 套：手往右、往下动时弧线习惯向一侧，往左、往上时向另一侧；
    竖直方向弧度略小，水平方向略大。"""
    side = 1.0 if direction in (0, 1, 2, 7) else -1.0
    scale = {0: 1.1, 4: 1.1, 2: 0.8, 6: 0.8}.get(direction, 1.0)
    out = []
    for t in BASE_TEMPLATES:
        out.append(Template(f"{DIRECTION_NAMES[direction]}·{t.name}", t.curve1 * side * scale,
                            t.curve2 * side * scale, t.wobble, t.wobble_cycles, t.overshoot, t.ease, t.pause_at))
    return out


TEMPLATES: list[list[Template]] = [_templates_for(d) for d in range(8)]


def direction_of(dx: float, dy: float) -> int:
    """屏幕坐标（y 向下）。0=右，1=右下，2=下，3=左下，4=左，5=左上，6=上，7=右上。"""
    ang = math.atan2(dy, dx)
    return int(round(ang / (math.pi / 4))) % 8


# ---------------------------------------------------------------- 几何
def _ease(s: float, kind: str) -> float:
    if kind == "out":
        return 1 - (1 - s) ** 3
    if kind == "in_out_strong":
        return s * s * s * (s * (6 * s - 15) + 10)  # smootherstep：起步和收尾更慢
    return s * s * (3 - 2 * s)  # smoothstep


def _bezier(p0, p1, p2, p3, t: float):
    u = 1 - t
    a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
    return (a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1])


def pick_landing(box: Box, rng: random.Random, margin: float) -> tuple[float, float]:
    """目标框内随机落点，偏向中心；框太小时直接取中心。"""
    if box.width < 4 or box.height < 4:
        return box.center()
    m = min(max(margin, 0.0), 0.45)
    lo_x, hi_x = box.x + box.width * m, box.x + box.width * (1 - m)
    lo_y, hi_y = box.y + box.height * m, box.y + box.height * (1 - m)
    cx, cy = box.center()
    return rng.triangular(lo_x, hi_x, cx), rng.triangular(lo_y, hi_y, cy)


@dataclass
class Move:
    points: list[tuple[float, float, int]]   # (x, y, 距起点的毫秒数)
    start: tuple[float, float]
    landing: tuple[float, float]
    target: Box
    direction: int
    template: str
    overshoot_point: tuple[float, float] | None
    duration_ms: int
    template_idx: int = 0
    actual_ms: int | None = None          # 实际发送完所有点用的时间（事件返回慢时会比计划长）
    source: str = "template"              # "template" | "library"
    sample_id: str | None = None          # 轨迹库样本编号（source=library 时）
    fallback: bool = False                # 想用轨迹库但该方向没有样本，退回模板

    def to_dict(self) -> dict:
        r = lambda p: [round(p[0], 1), round(p[1], 1)]  # noqa: E731
        return {"start": r(self.start), "landing": r(self.landing), "target": self.target.to_dict(),
                "direction": DIRECTION_NAMES[self.direction], "direction_code": DIRECTION_CODES[self.direction],
                "template": self.template, "template_idx": self.template_idx,
                "source": self.source, "sample_id": self.sample_id, "fallback": self.fallback,
                "overshoot_point": r(self.overshoot_point) if self.overshoot_point else None,
                "duration_ms": self.duration_ms, "actual_ms": self.actual_ms, "n_points": len(self.points),
                "points": [[round(x, 1), round(y, 1), t] for x, y, t in self.points]}


def plan_move(start: tuple[float, float], target: Box, rng: random.Random, cfg: MouseConfig,
              viewport: tuple[float, float]) -> Move:
    """生成一条从 start 到目标框内随机落点的轨迹。纯函数：同样的 rng 状态给出同样的结果。"""
    vw, vh = viewport
    landing = pick_landing(target, rng, cfg.land_margin)
    sx, sy = start
    lx, ly = landing
    dx, dy = lx - sx, ly - sy
    dist = math.hypot(dx, dy)
    direction = direction_of(dx, dy) if dist > 0 else 0
    tpl_idx = rng.randrange(5)
    tpl = TEMPLATES[direction][tpl_idx]

    def clamp(p):
        return (min(max(p[0], 0.0), vw - 1), min(max(p[1], 0.0), vh - 1))

    if dist < 3:
        return Move([(lx, ly, 0)], start, landing, target, direction, tpl.name, None, 0, tpl_idx)

    ux, uy = dx / dist, dy / dist          # 行进方向
    px, py = -uy, ux                       # 左侧法线（屏幕坐标下）

    # 时长：Fitts 定律 a + b·log2(1 + D/W)，再乘随机系数
    width = max(min(target.width, target.height), 1.0)
    duration = 140 + 110 * math.log2(1 + dist / width)
    duration *= rng.uniform(0.85, 1.2)
    duration = min(max(duration, cfg.min_duration_ms), cfg.max_duration_ms)

    # 过冲点：沿行进方向冲过落点一小段，再加一点横向偏差；距离限制在 4–30 像素
    over = None
    if tpl.overshoot > 0 and rng.random() < cfg.overshoot_prob and dist > 40:
        o = min(max(tpl.overshoot * dist * rng.uniform(0.7, 1.3), 4.0), 30.0)
        side = rng.uniform(-0.35, 0.35) * o
        over = clamp((lx + ux * o + px * side, ly + uy * o + py * side))
    end = over or landing

    # 主段控制点：沿路径 30% / 70% 处，横向偏移由模板决定，带随机缩放
    jit = rng.uniform(0.75, 1.25)
    c1 = (sx + dx * 0.3 + px * tpl.curve1 * dist * jit, sy + dy * 0.3 + py * tpl.curve1 * dist * jit)
    c2 = (sx + dx * 0.7 + px * tpl.curve2 * dist * jit, sy + dy * 0.7 + py * tpl.curve2 * dist * jit)

    main_ms = duration * (0.82 if over else 1.0)
    n = max(math.ceil(main_ms / cfg.step_ms), 4)
    phase = rng.uniform(0, 2 * math.pi)
    wob = tpl.wobble * dist * rng.uniform(0.6, 1.4)
    pts: list[tuple[float, float, int]] = []
    pause_ms = rng.randint(80, 220) if tpl.pause_at is not None else 0
    paused = False
    t_ms = 0.0
    for i in range(1, n + 1):
        s = i / n
        t_ms += cfg.step_ms
        if tpl.pause_at is not None and not paused and s >= tpl.pause_at:
            t_ms += pause_ms
            paused = True
        bx, by = _bezier(start, c1, c2, end, _ease(s, tpl.ease))
        w = wob * math.sin(2 * math.pi * tpl.wobble_cycles * s + phase) * math.sin(math.pi * s)  # 首尾为 0
        w += rng.gauss(0, 0.25) * math.sin(math.pi * s)
        pts.append((*clamp((bx + px * w, by + py * w)), int(t_ms)))

    # 回拉段：从过冲点拉回落点
    if over:
        back_ms = duration - main_ms + rng.uniform(40, 100)
        m = max(math.ceil(back_ms / cfg.step_ms), 3)
        mid = ((over[0] + lx) / 2 + px * rng.uniform(-2, 2), (over[1] + ly) / 2 + py * rng.uniform(-2, 2))
        for i in range(1, m + 1):
            s = _ease(i / m, "out")
            bx, by = _bezier(over, mid, mid, landing, s)
            t_ms += cfg.step_ms
            pts.append((*clamp((bx, by)), int(t_ms)))

    pts[-1] = (lx, ly, pts[-1][2])          # 最后一个点精确落在落点上
    return Move(pts, start, landing, target, direction, tpl.name, over, int(t_ms), tpl_idx)


def plan_library_move(start: tuple[float, float], target: Box, rng: random.Random, cfg: MouseConfig,
                      viewport: tuple[float, float], library) -> Move:
    """真实轨迹库回放：按方向+设备挑一条样本，相似变换到当前起点和落点；没有样本则退回模板。"""
    from .trajlib import transform

    landing = pick_landing(target, rng, cfg.land_margin)
    dx, dy = landing[0] - start[0], landing[1] - start[1]
    dist = math.hypot(dx, dy)
    direction = direction_of(dx, dy) if dist > 0 else 0
    sample = library.pick(direction, cfg.device, rng) if (library is not None and dist >= 3) else None
    if sample is None:
        mv = plan_move(start, target, rng, cfg, viewport)
        mv.fallback = True
        return mv
    vw, vh = viewport
    pts = transform(sample, start, landing, rng, cfg.min_duration_ms, cfg.max_duration_ms)
    pts = [(min(max(x, 0.0), vw - 1), min(max(y, 0.0), vh - 1), t) for x, y, t in pts[1:]] or [(*landing, 0)]
    pts[-1] = (landing[0], landing[1], pts[-1][2])
    return Move(pts, start, landing, target, direction, "library", None, int(pts[-1][2]), -1,
                source="library", sample_id=sample.id)


# ---------------------------------------------------------------- 运行时
class Humanizer:
    """一条用例一个实例：持有随机数状态和当前光标位置。"""

    def __init__(self, cfg: HumanizeConfig, seed: int, library=None):
        self.cfg = cfg
        self.seed = seed
        self.rng = random.Random(seed)
        self.cursor: tuple[float, float] | None = None
        self.log: list[dict] = []
        self.library = library               # trajlib.Library；None 且 source=library 时按 library_path 加载

    def _library(self):
        if self.library is None:
            from .config import DATA_ROOT
            from .trajlib import Library
            p = Path(self.cfg.mouse.library_path)
            self.library = Library.load(p if p.is_absolute() else DATA_ROOT / p)
        return self.library

    @property
    def mouse_on(self) -> bool:
        return self.cfg.mouse.enabled

    def start_point(self, viewport: tuple[float, float]) -> tuple[float, float]:
        if self.cursor is None:   # 第一次移动：从视口内一个随机位置出发
            self.cursor = (self.rng.uniform(0.3, 0.7) * viewport[0], self.rng.uniform(0.3, 0.7) * viewport[1])
        return self.cursor

    async def move_to(self, session, target: Box, viewport: tuple[float, float]) -> Move:
        """沿轨迹逐点发送 mouseMoved，按轨迹的时间戳控制节奏。"""
        if self.cfg.mouse.source == "library":
            mv = plan_library_move(self.start_point(viewport), target, self.rng, self.cfg.mouse, viewport,
                                   self._library())
        else:
            mv = plan_move(self.start_point(viewport), target, self.rng, self.cfg.mouse, viewport)
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        for x, y, t in mv.points:
            # 按绝对时间表发送：事件返回慢时不再额外叠加间隔，实际时长尽量贴近计划
            delay = t0 + t / 1000 - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)  # humanize-pacing
            await session.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        mv.actual_ms = int((loop.time() - t0) * 1000)
        self.cursor = mv.landing
        return mv

    def jump_to(self, x: float, y: float):
        """没有走轨迹的鼠标事件（例如关闭轨迹时）也要更新光标位置。"""
        self.cursor = (x, y)

    async def think(self) -> dict | None:
        c = self.cfg.think
        if not c.enabled or self.rng.random() >= c.prob:
            return None
        ms = self.rng.randint(c.min_ms, c.max_ms)
        from .status import STATUS
        STATUS.set_wait("think", ms / 1000)
        try:
            await asyncio.sleep(ms / 1000)  # humanize-pacing
        finally:
            STATUS.clear_wait()
        return {"kind": "think", "ms": ms}

    def plan_jitter(self) -> list[int] | None:
        """返回本次滚轮乱动的各段滚动量（像素，正 = 向下）；不发生则返回 None。"""
        c = self.cfg.scroll_jitter
        if not c.enabled or self.rng.random() >= c.prob:
            return None
        total = self.rng.randint(c.min_px, c.max_px) * self.rng.choice((1, -1))
        k = self.rng.randint(1, max(c.max_ticks, 1))
        cuts = sorted(self.rng.random() for _ in range(k - 1))
        parts, prev = [], 0.0
        for cut in cuts + [1.0]:
            parts.append(int(round(total * (cut - prev))))
            prev = cut
        return [p for p in parts if p] or [total]

    async def pause(self) -> int:
        c = self.cfg.scroll_jitter
        ms = self.rng.randint(c.pause_min_ms, c.pause_max_ms)
        await asyncio.sleep(ms / 1000)  # humanize-pacing
        return ms
