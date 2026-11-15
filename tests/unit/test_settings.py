import shutil

import pytest

from hands.config import PROJECT_ROOT, Config, load_config
from hands.humanize import HumanizeConfig, Humanizer

settings = pytest.importorskip("panel.settings")


def cfg():
    c = Config(chrome_path="x")
    c.humanize = {"mouse": {"enabled": True, "overshoot_prob": 0.7}}
    return c


def test_out_of_range_rejected_and_unchanged():
    c = cfg()
    ls = settings.LiveSettings(c)
    before = dict(ls.current)
    errs = ls.update({"humanize.mouse.overshoot_prob": 2, "quiet_ms": 300})
    assert "humanize.mouse.overshoot_prob" in errs
    assert ls.pending == {} and ls.current == before
    assert ls.update({"humanize.think.min_ms": 900, "humanize.think.max_ms": 100})
    assert ls.update({"decision": "gpt"})                       # 不在可选项里
    assert ls.update({"decision": "vlm"}) == {}                 # ver1：视觉大模型已接入，可以选
    assert ls.update({"humanize.seed": "abc"})


def test_apply_now_updates_config_and_humanizer():
    c = cfg()
    ls = settings.LiveSettings(c)
    h = Humanizer(HumanizeConfig.from_dict(c.humanize), 7)
    assert ls.update({"humanize.mouse.overshoot_prob": 0, "quiet_ms": 800, "humanize.seed": 42}) == {}
    assert h.cfg.mouse.overshoot_prob == 0.7                    # 还没到下一个动作
    assert ls.apply_now(c, h)
    assert h.cfg.mouse.overshoot_prob == 0 and c.quiet_ms == 800
    assert c.humanize["seed"] == 42 and ls.version == 1
    assert not ls.apply_now(c, h)                               # 没有新修改


def test_restore_defaults():
    c = cfg()
    ls = settings.LiveSettings(c)
    ls.restore()
    ls.apply_now(c)
    assert ls.current["humanize.mouse.enabled"] is False
    assert ls.current["humanize.mouse.overshoot_prob"] == 0.7


def test_save_default_keeps_comments(tmp_path):
    p = tmp_path / "config.yaml"
    shutil.copyfile(PROJECT_ROOT / "config.yaml", p)
    orig = p.read_text(encoding="utf-8")
    n_comments = sum(1 for line in orig.splitlines() if "#" in line)
    settings.save_yaml_values(p, {"quiet_ms": 700, "humanize.mouse.overshoot_prob": 0.25,
                                  "humanize.think.enabled": True, "humanize.seed": 99})
    text = p.read_text(encoding="utf-8")
    assert sum(1 for line in text.splitlines() if "#" in line) == n_comments
    assert (tmp_path / "config.yaml.bak").read_text(encoding="utf-8") == orig
    c = load_config(p, chrome_path="x")
    assert c.quiet_ms == 700
    hc = HumanizeConfig.from_dict(c.humanize)
    assert hc.mouse.overshoot_prob == 0.25 and hc.think.enabled is True and hc.seed == 99
    assert hc.scroll_jitter.max_px == 320                       # 其他值不变


def test_save_missing_key_appended(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("a: 1\nhumanize:\n  mouse:\n    enabled: false   # 注释\n", encoding="utf-8")
    settings.save_yaml_values(p, {"humanize.mouse.source": "library", "humanize.think.prob": 0.1, "b": 2})
    import yaml
    d = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert d["humanize"]["mouse"] == {"enabled": False, "source": "library"}
    assert d["humanize"]["think"]["prob"] == 0.1 and d["b"] == 2 and d["a"] == 1
    assert "# 注释" in p.read_text(encoding="utf-8")
