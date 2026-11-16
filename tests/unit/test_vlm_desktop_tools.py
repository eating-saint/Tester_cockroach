"""桌面专用工具说明只在桌面能力下出现；默认能力下提示词与之前逐字相同（C23.6）。"""
from types import SimpleNamespace

from hands.driver import DESKTOP_CAPS, Capabilities
from hands.vlm.decider import VlmDecider
from hands.vlm.prompts import PromptBook


def _decider():
    d = VlmDecider.__new__(VlmDecider)
    d.pb = PromptBook()
    return d


def test_desktop_tools_inserted_only_for_desktop(monkeypatch):
    d = _decider()
    base = d.pb.text("decide")
    monkeypatch.setattr(VlmDecider, "_render_decide_base", lambda self, *a: base)
    task = SimpleNamespace(child_summaries=[], goal="g")
    desc = SimpleNamespace(overlay=False)
    for caps in (None, Capabilities()):
        inp = SimpleNamespace(task=task, page=None, brief={}, last_error=None, caps=caps)
        assert d._render_decide(inp, None, desc) == base
    # 桌面能力但页面上没有网格 / 菜单、也没卡住：不放说明（避免把 4b 带偏）
    plain = SimpleNamespace(elements=[SimpleNamespace(source="uia", role="link")])
    inp = SimpleNamespace(task=SimpleNamespace(**vars(task), recent_steps=[]), page=plain, brief={},
                          last_error=None, caps=DESKTOP_CAPS)
    assert d._render_decide(inp, None, desc) == base
    # 页面上有网格：放说明，位置在工具清单末尾
    grid_page = SimpleNamespace(elements=[SimpleNamespace(source="grid", role="grid_cell")])
    inp = SimpleNamespace(task=SimpleNamespace(**vars(task), recent_steps=[]), page=grid_page, brief={},
                          last_error=None, caps=DESKTOP_CAPS)
    out = d._render_decide(inp, None, desc)
    assert "right_click" in out and "show_grid" in out
    assert out.index("show_grid") < out.index("做法提示")
    # 连续两步没有效果：放说明（可能需要网格）
    stuck = [{"tool": "click", "effective": False}, {"tool": "click", "effective": False}]
    inp = SimpleNamespace(task=SimpleNamespace(**vars(task), recent_steps=stuck), page=plain, brief={},
                          last_error=None, caps=DESKTOP_CAPS)
    assert "show_grid" in d._render_decide(inp, None, desc)
