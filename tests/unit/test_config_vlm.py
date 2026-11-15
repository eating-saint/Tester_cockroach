"""ver1 配置：决策方式、大模型设置、兜底拦截清单。"""
import pytest

from hands.config import VLM_DEFAULTS, load_config, vlm_settings


def _cfg(tmp_path, text: str):
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return load_config(p, chrome_path="chrome.exe")


def test_default_decision_is_rule(tmp_path):
    cfg = _cfg(tmp_path, "headless: true\n")
    assert cfg.decision == "rule"
    assert cfg.vlm == {} and cfg.guard == {}


def test_decision_lowercased(tmp_path):
    assert _cfg(tmp_path, "decision: VLM\n").decision == "vlm"


def test_unknown_decision_rejected(tmp_path):
    with pytest.raises(ValueError, match="未知决策方式"):
        _cfg(tmp_path, "decision: gpt\n")


def test_override_beats_file(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("decision: vlm\n", encoding="utf-8")
    assert load_config(p, chrome_path="chrome.exe", decision="rule").decision == "rule"


def test_vlm_settings_merge(tmp_path):
    cfg = _cfg(tmp_path, "vlm:\n  model: qwen3.5:2b\n  num_ctx: 8192\n")
    s = vlm_settings(cfg)
    assert s["model"] == "qwen3.5:2b" and s["num_ctx"] == 8192
    assert s["base_url"] == VLM_DEFAULTS["base_url"]
    assert set(VLM_DEFAULTS) <= set(s)
