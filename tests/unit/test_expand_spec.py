from pathlib import Path

import pytest

from hands.expand.spec import TaskTypeInvalid, load_task_type

LEGACY = Path(__file__).resolve().parent / "data" / "legacy"     # 上一章旧格式原件（tasks/ 已改写为任务书）


def test_fixture_types_load():
    t = load_task_type(LEGACY / "fixture_transformers.yaml", {"BASE": "http://x"})
    assert t.start == "optimus" and t.groups[0] == ("optimus", ["galvatron", "megatron", "starscream"])
    assert t.search_url == "http://x/f22_search/search.html?q={q}"
    assert "80%" in t.conditions[0].text and t.budget == 120
    d = load_task_type(LEGACY / "fixture_dinner.yaml", {"BASE": "http://x", "n": 3})
    assert d.output["type"] == "ranked" and d.output["n"] == 3 and d.query == "便宜 {key} {value} "
    assert "2 档" in d.conditions[0].text


def _write(tmp_path, body: str) -> Path:
    p = tmp_path / "t.yaml"
    p.write_text(body, encoding="utf-8")
    return p


BASE = """kind: expand
search_url: "http://s/?q={q}"
query: "{value}"
start: a
groups:
  - a: [b, c]
conditions:
  - {text: "提到 {ratio}", params: {ratio: 0.8}}
"""


def test_minimal_ok(tmp_path):
    t = load_task_type(_write(tmp_path, BASE))
    assert t.output == {"type": "leaves"} and t.conditions[0].text == "提到 80%"


@pytest.mark.parametrize("body, msg", [
    (BASE.replace("start: a", "start: z"), "start（z）不在"),
    (BASE.replace("start: a\n", ""), "缺少 start"),
    (BASE.replace("ratio: 0.8", "ratio: 1.5"), "ratio=1.5"),
    (BASE.replace("[b, c]", "[]"), "groups[0]（a）的值为空"),
    (BASE.replace('"{value}"', '"x"'), "query 里要有 {value}"),
    (BASE + "budget: 0\n", "budget=0"),
    (BASE + "output: {type: ranked}\n", "fields 和 n"),
])
def test_invalid(tmp_path, body, msg):
    with pytest.raises(TaskTypeInvalid, match=msg.replace("{", r"\{").replace("}", r"\}")
                       .replace("(", r"\(").replace(")", r"\)").replace("[", r"\[").replace("]", r"\]")):
        load_task_type(_write(tmp_path, body))
