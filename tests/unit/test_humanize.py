import math
import random

import pytest

from hands.geometry import Box
from hands.humanize import (TEMPLATES, HumanizeConfig, Humanizer, MouseConfig, apply_cli, derive_seed,
                            direction_of, pick_landing, plan_move)

VP = (1280, 800)


def test_direction_sectors():
    assert direction_of(10, 0) == 0          # 右
    assert direction_of(10, 10) == 1         # 右下（屏幕 y 向下）
    assert direction_of(0, 10) == 2          # 下
    assert direction_of(-10, 10) == 3
    assert direction_of(-10, 0) == 4
    assert direction_of(-10, -10) == 5
    assert direction_of(0, -10) == 6
    assert direction_of(10, -10) == 7
    assert direction_of(10, 3) == 0          # 22.5° 以内归到右


def test_eight_directions_five_templates_each():
    assert len(TEMPLATES) == 8
    assert all(len(t) == 5 for t in TEMPLATES)
    names = {t.name for row in TEMPLATES for t in row}
    assert len(names) == 40


def _random_case(rng):
    w, h = rng.uniform(6, 300), rng.uniform(6, 120)
    box = Box(rng.uniform(0, VP[0] - w), rng.uniform(0, VP[1] - h), w, h)
    start = (rng.uniform(0, VP[0] - 1), rng.uniform(0, VP[1] - 1))
    return start, box


def test_landing_inside_target_and_path_properties():
    rng = random.Random(7)
    cfg = MouseConfig(enabled=True)
    overshoots = 0
    for _ in range(500):
        start, box = _random_case(rng)
        mv = plan_move(start, box, random.Random(rng.random()), cfg, VP)
        lx, ly = mv.landing
        assert box.contains(lx, ly)
        assert mv.points[-1][:2] == (lx, ly)                      # 终点精确等于落点
        ts = [t for _, _, t in mv.points]
        assert ts == sorted(ts)                                    # 时间单调不减
        assert all(0 <= x <= VP[0] - 1 and 0 <= y <= VP[1] - 1 for x, y, _ in mv.points[:-1])
        if math.dist(start, mv.landing) > 3:
            assert cfg.min_duration_ms <= mv.duration_ms <= cfg.max_duration_ms + 400
            assert len(mv.points) >= 5
        if mv.overshoot_point:
            overshoots += 1
            assert math.dist(mv.overshoot_point, mv.landing) <= 30 * 1.36 + 1   # 过冲距离上限（含横向偏差）
    assert 100 < overshoots < 450                                  # 有的过冲，有的不过冲


def test_small_box_lands_in_center():
    box = Box(100, 100, 3, 3)
    assert pick_landing(box, random.Random(1), 0.2) == box.center()


def test_same_seed_same_path_different_seed_differs():
    cfg = MouseConfig(enabled=True)
    box = Box(900, 500, 96, 40)
    a = plan_move((200, 200), box, random.Random(42), cfg, VP)
    b = plan_move((200, 200), box, random.Random(42), cfg, VP)
    c = plan_move((200, 200), box, random.Random(43), cfg, VP)
    assert a.points == b.points
    assert a.points != c.points


def test_path_is_not_a_straight_line():
    """仿人轨迹：中间点应偏离起点—落点连线（至少有一条明显弯曲）。"""
    cfg = MouseConfig(enabled=True)
    max_dev = 0.0
    for seed in range(20):
        mv = plan_move((100, 100), Box(900, 600, 96, 40), random.Random(seed), cfg, VP)
        (sx, sy), (lx, ly) = mv.start, mv.landing
        L = math.dist(mv.start, mv.landing)
        for x, y, _ in mv.points:
            max_dev = max(max_dev, abs((lx - sx) * (sy - y) - (sx - x) * (ly - sy)) / L)
    assert max_dev > 20


def test_jitter_plan_sums_and_respects_bounds():
    cfg = HumanizeConfig.from_dict({"scroll_jitter": {"enabled": True, "prob": 1, "min_px": 60, "max_px": 320,
                                                      "max_ticks": 3}})
    h = Humanizer(cfg, 5)
    for _ in range(200):
        parts = h.plan_jitter()
        assert parts and 1 <= len(parts) <= 3
        assert 55 <= abs(sum(parts)) <= 325
        assert len({p > 0 for p in parts}) == 1                   # 一次乱动只朝一个方向


def test_disabled_items_do_nothing():
    h = Humanizer(HumanizeConfig(), 1)
    assert h.plan_jitter() is None
    assert not h.mouse_on


def test_config_shortcuts_and_cli():
    c = HumanizeConfig.from_dict({"mouse": True, "think": {"enabled": True, "prob": 0.2}, "bogus": 1})
    assert c.mouse.enabled and c.think.enabled and c.think.prob == 0.2 and not c.scroll_jitter.enabled
    d = apply_cli({"think": {"prob": 0.9}}, enable=True, seed=7)
    assert d["seed"] == 7 and d["think"] == {"prob": 0.9, "enabled": True}
    assert d["mouse"]["enabled"] and d["scroll_jitter"]["enabled"]
    d = apply_cli({}, only="mouse")
    assert set(d) == {"mouse"}
    with pytest.raises(ValueError):
        apply_cli({}, only="teleport")


def test_case_seeds_independent_of_order():
    assert derive_seed(1, "a", 1) == derive_seed(1, "a", 1)
    assert derive_seed(1, "a", 1) != derive_seed(1, "b", 1)
    assert derive_seed(1, "a", 1) != derive_seed(1, "a", 2)
