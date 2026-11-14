import math
import random

import pytest

from hands.geometry import Box
from hands.humanize import HumanizeConfig, MouseConfig, plan_library_move, plan_move
from hands.trajlib import Library, Sample, transform

VP = (1280, 800)
DIRS = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]


def make_raw(dx, dy, device="mouse", n=20, dur=500, start=(400, 400), bend=12.0):
    sx, sy = start
    ex, ey = sx + dx, sy + dy
    pts = []
    L = math.hypot(dx, dy) or 1
    px, py = -dy / L, dx / L
    for i in range(1, n + 1):
        s = i / n
        b = bend * math.sin(math.pi * s)
        pts.append([sx + dx * s + px * b, sy + dy * s + py * b, int(dur * s)])
    pts[-1] = [ex, ey, dur]
    return {"device": device, "source": "local", "start": [sx, sy], "end": [ex, ey],
            "target": {"x": ex - 20, "y": ey - 10, "width": 40, "height": 20}, "points": pts, "dpr": 1.0}


def full_library():
    lib = Library()
    for k, (ux, uy) in enumerate(DIRS):
        for j in range(2):
            lib.samples.append(Sample.from_raw(make_raw(ux * (200 + 30 * j), uy * (200 + 30 * j), bend=10 + j)))
    return lib


def test_from_raw_id_stable_and_direction():
    a = Sample.from_raw(make_raw(300, 0))
    b = Sample.from_raw(make_raw(300, 0))
    assert a.id == b.id and len(a.id) == 12
    for k, (ux, uy) in enumerate(DIRS):
        assert Sample.from_raw(make_raw(ux * 250, uy * 250)).direction == k


def test_save_load_roundtrip(tmp_path):
    lib = full_library()
    p = tmp_path / "trajlib" / "samples.json"
    lib.save(p)
    back = Library.load(p)
    assert [s.to_dict() for s in back.samples] == [s.to_dict() for s in lib.samples]
    assert Library.load(tmp_path / "none.json").samples == []


def test_bad_version(tmp_path):
    p = tmp_path / "s.json"
    p.write_text('{"version": 2, "samples": []}', encoding="utf-8")
    with pytest.raises(ValueError):
        Library.load(p)


def test_pick_by_direction_device_and_deterministic():
    lib = full_library()
    lib.samples.append(Sample.from_raw(make_raw(300, 0, device="touchpad")))
    r1, r2 = random.Random(5), random.Random(5)
    a = lib.pick(0, "mouse", r1)
    b = lib.pick(0, "mouse", r2)
    assert a.id == b.id and a.device == "mouse" and a.direction == 0
    assert lib.pick(0, "touchpad", random.Random(1)).device == "touchpad"
    assert Library().pick(0, "mouse", random.Random(1)) is None


def test_transform_endpoints_and_duration():
    lib = full_library()
    rng = random.Random(42)
    for _ in range(500):
        s = rng.choice(lib.samples)
        start = (rng.uniform(0, 1200), rng.uniform(0, 780))
        landing = (rng.uniform(0, 1200), rng.uniform(0, 780))
        pts = transform(s, start, landing, rng)
        assert pts[0][:2] == start
        assert pts[-1][:2] == landing
        assert 180 <= pts[-1][2] <= 1100
        assert all(pts[i][2] <= pts[i + 1][2] for i in range(len(pts) - 1))


def test_transform_same_seed_same_result():
    s = full_library().samples[3]
    a = transform(s, (100, 100), (500, 400), random.Random(9))
    b = transform(s, (100, 100), (500, 400), random.Random(9))
    assert a == b


def test_plan_library_move_uses_sample_and_fallback():
    cfg = MouseConfig(enabled=True, source="library")
    lib = full_library()
    target = Box(800, 380, 60, 30)
    mv = plan_library_move((300, 400), target, random.Random(3), cfg, VP, lib)
    assert mv.source == "library" and mv.sample_id and not mv.fallback
    assert mv.points[-1][:2] == mv.landing and target.contains(*mv.landing)
    mv2 = plan_library_move((300, 400), target, random.Random(3), cfg, VP, Library())
    assert mv2.fallback and mv2.source == "template"
    a = plan_library_move((300, 400), target, random.Random(3), cfg, VP, lib)
    assert a.points == mv.points


def test_library_config_parsed():
    c = HumanizeConfig.from_dict({"mouse": {"enabled": True, "source": "library", "device": "touchpad"}})
    assert c.mouse.source == "library" and c.mouse.device == "touchpad"
    assert c.mouse.library_path == "trajlib/samples.json"
    # 默认仍是模板，行为与以前相同
    assert HumanizeConfig.from_dict({}).mouse.source == "template"
    a = plan_move((100, 100), Box(500, 500, 40, 20), random.Random(1), MouseConfig(enabled=True), VP)
    assert a.source == "template" and a.sample_id is None
