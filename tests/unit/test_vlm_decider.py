"""VlmDecider（替身模型）：编号换 id、编号不存在、同页复用描述、重问带原因、记录文件齐全、抽取映射。"""
import asyncio
import io
import json

from PIL import Image

from hands.config import Config
from hands.decision import DecisionInput, DecisionInvalid, decide_with_retry, validate_decision
from hands.dom import Element
from hands.geometry import Box
from hands.pagestate import PageState
from hands.tasktree import TaskTree
from hands.vlm.client import ScriptedClient
from hands.vlm.decider import VlmDecider

DESC = json.dumps({"regions": [{"position": "顶部", "kind": "导航栏", "note": "首页"}], "overlay": False})


def _state(tmp_path, url="http://x/"):
    els = [Element("e5", "searchbox", "搜索", "", "input", None, None, Box(10, 10, 200, 30), True, True, True),
           Element("e8", "button", "搜索", "搜索", "button", None, None, Box(220, 10, 60, 30), True, True, True)]
    buf = io.BytesIO()
    Image.new("RGB", (1280, 800), (255, 255, 255)).save(buf, format="PNG")
    (tmp_path / "screenshot.png").write_bytes(buf.getvalue())
    return PageState(url, "首页", Box(0, 0, 1280, 800), 1.0, els, "欢迎光临", screenshot="screenshot.png")


def _cfg():
    return Config(chrome_path="x")


def _decide(obj):
    return json.dumps(obj, ensure_ascii=False)


def test_number_mapped_to_id(tmp_path):
    c = ScriptedClient([DESC, _decide({"reason": "点按钮", "tool": "click", "arguments": {"element": 2}})])
    d = VlmDecider(_cfg(), c)
    raw = asyncio.run(d.decide(DecisionInput(_state(tmp_path), TaskTree("搜索").view(), step_dir=tmp_path)))
    dec = validate_decision(raw, _state(tmp_path))
    assert dec.tool == "click" and dec.arguments["element"] == "e8"      # 视口内按从左到右：1=e5，2=e8
    for f in ("clean.png", "annotated.png", "digest.json", "describe.json", "request.json", "response.json"):
        assert (tmp_path / "vlm" / f).exists(), f
    assert c.calls[0]["images"] == 1 and c.calls[1]["schema"]["required"] == ["reason", "tool", "arguments"]


def test_unknown_number_then_retry_with_reason(tmp_path):
    c = ScriptedClient([DESC,
                        _decide({"reason": "r", "tool": "click", "arguments": {"element": 99}}),
                        _decide({"reason": "r", "tool": "click", "arguments": {"element": "#1"}})])
    d = VlmDecider(_cfg(), c)
    st = _state(tmp_path)
    dec, raws, tries = asyncio.run(decide_with_retry(d, DecisionInput(st, TaskTree("g").view(), step_dir=tmp_path)))
    assert '"#99"' in raws[0] and tries == 2 and dec.arguments["element"] == "e5"
    assert "编号 99 不在本页的元素清单里" in c.calls[2]["user"]          # 第二次决策调用带了原因
    assert len(c.calls) == 3                                             # 描述只调用一次
    assert (tmp_path / "vlm" / "request_try2.json").exists()


def test_same_page_reuses_description(tmp_path):
    c = ScriptedClient([DESC, _decide({"reason": "r", "tool": "scroll", "arguments": {"direction": "down", "pixels": 300}}),
                        _decide({"reason": "r", "tool": "back", "arguments": {}})])
    d = VlmDecider(_cfg(), c)
    s1, s2 = tmp_path / "s1", tmp_path / "s2"
    s1.mkdir(); s2.mkdir()
    asyncio.run(d.decide(DecisionInput(_state(s1), TaskTree("g").view(), step_dir=s1)))
    asyncio.run(d.decide(DecisionInput(_state(s2), TaskTree("g").view(), step_dir=s2)))
    assert len(c.calls) == 3
    assert json.loads((s2 / "vlm" / "describe.json").read_text(encoding="utf-8"))["reused"] is True


def test_overlay_hint_and_description_in_prompt(tmp_path):
    desc = json.dumps({"regions": [{"position": "覆盖全页", "kind": "弹窗遮罩", "note": "订阅"}], "overlay": True})
    c = ScriptedClient([desc, _decide({"reason": "r", "tool": "fail", "arguments": {"reason": "x"}})])
    asyncio.run(VlmDecider(_cfg(), c).decide(DecisionInput(_state(tmp_path), TaskTree("g").view(), step_dir=tmp_path)))
    user = c.calls[1]["user"]
    assert "页面有弹窗遮挡" in user and "覆盖全页｜弹窗遮罩" in user and "1 | 搜索框 | 搜索" in user


def test_describe_retry_then_empty(tmp_path):
    c = ScriptedClient(["不是 JSON", "也不是", "还不是", _decide({"reason": "r", "tool": "back", "arguments": {}})])
    raw = asyncio.run(VlmDecider(_cfg(), c).decide(DecisionInput(_state(tmp_path), TaskTree("g").view(), step_dir=tmp_path)))
    assert json.loads(raw)["tool"] == "back"
    d = json.loads((tmp_path / "vlm" / "describe.json").read_text(encoding="utf-8"))
    assert d["reason"].startswith("描述失败") and len(d["calls"]) == 3


def test_extract_mapping(tmp_path):
    items = [{"field": "标题", "value": "搜索", "element": 2}, {"field": "价格", "value": 12.99, "element": "正文"}]
    c = ScriptedClient([DESC, _decide({"reason": "r", "tool": "extract", "arguments": {"items": items}})])
    raw = asyncio.run(VlmDecider(_cfg(), c).decide(DecisionInput(_state(tmp_path), TaskTree("g").view(), step_dir=tmp_path)))
    dec = validate_decision(raw, _state(tmp_path))
    got = dec.arguments["items"]
    assert got[0]["element"] == "e8" and "element" not in got[1] and got[1]["value"] == "12.99"


def test_recent_steps_use_label(tmp_path):
    tree = TaskTree("g")
    tree.record_step({"tool": "click", "arguments": {"element": "e3"}, "ok": False, "effective": False,
                      "error": "guard_blocked: 命中「place order」(name)", "target_label": "按钮「Place order」"})
    c = ScriptedClient([DESC, _decide({"reason": "r", "tool": "back", "arguments": {}})])
    asyncio.run(VlmDecider(_cfg(), c).decide(DecisionInput(_state(tmp_path), tree.view(), step_dir=tmp_path)))
    assert "click 按钮「Place order」 → 失败（guard_blocked" in c.calls[1]["user"]
