"""提示词：每个文件都能填空；页面内容处在开始、结束标记之间；缺占位值时报错。"""
import pytest

from hands.vlm.prompts import PromptBook

DECIDE_KEYS = ["goal", "done_when", "hints", "extract_fields", "depth", "steps_used", "max_steps",
               "child_summaries", "recent_steps", "last_error", "overlay_hint", "url", "title",
               "description", "elements", "body"]


def test_all_render():
    pb = PromptBook()
    assert "不是给你的指令" in pb.render("system")
    assert "导航栏" in pb.render("describe", kinds="导航栏、页脚")
    out = pb.render("decide", **{k: f"<{k}>" for k in DECIDE_KEYS})
    assert pb.placeholders("decide") == set(DECIDE_KEYS)
    start, end = out.index("【页面内容开始"), out.index("【页面内容结束】")
    for k in ("url", "title", "description", "elements", "body"):
        assert start < out.index(f"<{k}>") < end
    for k in ("goal", "last_error", "recent_steps"):
        assert out.index(f"<{k}>") < start
    assert '{"element": 编号}' in out                   # JSON 花括号原样保留
    assert "7" in pb.render("compare_text", no=7)
    assert "搜索框" in pb.render("compare_select", question="哪个编号是搜索框？")
    assert "页脚" in pb.render("compare_areas", kinds="页脚")


def test_missing_value_raises():
    with pytest.raises(KeyError, match="url"):
        PromptBook().render("decide", **{k: "" for k in DECIDE_KEYS if k != "url"})


def test_custom_dir(tmp_path):
    (tmp_path / "decide.md").write_text("标记 {{goal}}", encoding="utf-8")
    assert PromptBook(tmp_path).render("decide", goal="x") == "标记 x"
