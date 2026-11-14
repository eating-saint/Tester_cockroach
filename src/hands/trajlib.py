"""真实轨迹库：样本结构、库读写、按方向挑样本、相似变换到当前起点/落点。

纯函数，所有随机都来自调用方传入的 rng：同一 rng 状态给出同样的结果（N6）。
文件格式：{"version": 1, "samples": [...]}，UTF-8，人可读。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .geometry import Box

VERSION = 1


def _direction_of(dx: float, dy: float) -> int:
    from .humanize import direction_of
    return direction_of(dx, dy)


@dataclass
class Sample:
    id: str
    device: str                                   # "mouse" | "touchpad"
    source: str                                   # "local" | "real"
    direction: int                                # 0..7（与 humanize.direction_of 相同）
    start: tuple[float, float]
    end: tuple[float, float]
    target: Box
    points: list[tuple[float, float, int]]        # (x, y, ms)，CSS 像素，ms 从起点点击算起
    dpr: float = 1.0
    domain: str | None = None
    created: str = ""

    @property
    def duration_ms(self) -> int:
        return int(self.points[-1][2]) if self.points else 0

    @staticmethod
    def make_id(device: str, start, end, points) -> str:
        canon = json.dumps({"device": device, "start": [round(float(v), 2) for v in start],
                            "end": [round(float(v), 2) for v in end],
                            "points": [[round(float(x), 2), round(float(y), 2), int(t)] for x, y, t in points]},
                           sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(canon.encode("utf-8")).hexdigest()[:12]

    @classmethod
    def from_raw(cls, d: dict) -> "Sample":
        start = (float(d["start"][0]), float(d["start"][1]))
        end = (float(d["end"][0]), float(d["end"][1]))
        pts = [(float(p[0]), float(p[1]), int(p[2])) for p in d["points"]]
        t = d.get("target") or {}
        target = Box(float(t.get("x", 0)), float(t.get("y", 0)), float(t.get("width", 0)), float(t.get("height", 0)))
        device = d.get("device", "mouse")
        return cls(id=d.get("id") or cls.make_id(device, start, end, pts), device=device,
                   source=d.get("source", "local"), direction=_direction_of(end[0] - start[0], end[1] - start[1]),
                   start=start, end=end, target=target, points=pts, dpr=float(d.get("dpr", 1.0)),
                   domain=d.get("domain"), created=d.get("created") or time.strftime("%Y-%m-%dT%H:%M:%S"))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start"] = list(self.start)
        d["end"] = list(self.end)
        d["target"] = self.target.to_dict()
        d["points"] = [list(p) for p in self.points]
        return d


@dataclass
class Library:
    version: int = VERSION
    samples: list[Sample] = field(default_factory=list)

    @classmethod
    def load(cls, path) -> "Library":
        p = Path(path)
        if not p.exists():
            return cls()
        data = json.loads(p.read_text(encoding="utf-8") or "{}")
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "Library":
        v = data.get("version", VERSION)
        if v != VERSION:
            raise ValueError(f"unsupported trajectory library version: {v}")
        return cls(VERSION, [Sample.from_raw(s) for s in data.get("samples", [])])

    def to_dict(self) -> dict:
        return {"version": self.version, "samples": [s.to_dict() for s in self.samples]}

    def dumps(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=1)

    def save(self, path):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(self.dumps(), encoding="utf-8")
        os.replace(tmp, p)

    def ids(self) -> set[str]:
        return {s.id for s in self.samples}

    def pick(self, direction: int, device: str, rng: random.Random) -> Sample | None:
        cands = sorted((s for s in self.samples
                        if s.direction == direction and (device == "mixed" or s.device == device)
                        and len(s.points) >= 2), key=lambda s: s.id)
        if not cands:
            return None
        return rng.choice(cands)


def transform(sample: Sample, start: tuple[float, float], landing: tuple[float, float], rng: random.Random,
              min_ms: int = 180, max_ms: int = 1100) -> list[tuple[float, float, int]]:
    """把样本的 起点→终点 向量用相似变换（缩放 + 旋转 + 平移）对到 start→landing；
    时间按距离比例缩放并限制在 [min_ms, max_ms]；首点 = start，末点 = landing。"""
    sx, sy = sample.start
    vx, vy = sample.end[0] - sx, sample.end[1] - sy
    tx, ty = landing[0] - start[0], landing[1] - start[1]
    d_src = math.hypot(vx, vy)
    d_dst = math.hypot(tx, ty)
    if d_src < 1e-6:
        k, rot = 1.0, 0.0
    else:
        k = d_dst / d_src
        rot = math.atan2(ty, tx) - math.atan2(vy, vx)
    c, s = math.cos(rot), math.sin(rot)
    dur = max(sample.duration_ms, 1)
    scaled = dur * (d_dst / d_src) if d_src > 1e-6 else dur
    # 轻微随机速度系数，避免同一样本每次时长完全相同
    scaled *= rng.uniform(0.92, 1.08)
    new_dur = min(max(scaled, min_ms), max_ms)
    tk = new_dur / dur
    out: list[tuple[float, float, int]] = []
    last_t = 0
    for x, y, t in sample.points:
        rx, ry = x - sx, y - sy
        nx = start[0] + k * (c * rx - s * ry)
        ny = start[1] + k * (s * rx + c * ry)
        nt = max(int(round(t * tk)), last_t)
        last_t = nt
        out.append((nx, ny, nt))
    if out:
        out[0] = (start[0], start[1], 0)
        out[-1] = (landing[0], landing[1], max(out[-1][2], int(round(min_ms))))
        # 末点时间不小于前一点
        if len(out) >= 2 and out[-1][2] < out[-2][2]:
            out[-1] = (landing[0], landing[1], out[-2][2])
    return out
