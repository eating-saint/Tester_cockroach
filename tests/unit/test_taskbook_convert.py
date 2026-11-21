from pathlib import Path

import pytest
import yaml

from hands.taskbook.book import parse
from hands.taskbook.convert import REWRITE_MARK, detect, from_legacy, is_needs_rewrite
from hands.taskbook.validate import check, check_text

LEGACY = Path(__file__).parent / "data" / "legacy"
FILES = sorted(LEGACY.glob("*.yaml"))


def test_five_legacy_files():
    assert [f.stem for f in FILES] == ["dinner", "dinner_yelp", "fixture_dinner", "fixture_transformers",
                                       "transformers"]


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_convert_each(path):
    text = path.read_text(encoding="utf-8")
    assert detect(text) == "legacy"
    new, probs = from_legacy(text)
    assert detect(new) == "taskbook"
    b = parse(new)
    old = yaml.safe_load(text)
    assert b.name == old["name"] and b.start == old["start"]
    assert [k for k, _ in b.groups] == [next(iter(g)) for g in old["groups"]]
    assert b.limits["budget"] == int(old["budget"]) and b.limits["keep_tabs"] == int(old["keep_tabs"])
    assert is_needs_rewrite(b) and b.note.startswith(REWRITE_MARK)
    for c in old["conditions"]:
        assert c["id"] in b.note
    assert list(b.flow[0]) == ["search"] and len(b.flow) == 1
    assert "{q}" in b.flow[0]["search"]["url"] and "${" not in new
    assert "{value}" not in b.flow[0]["search"]["query"]
    assert probs and "补全" in probs[0].why
    # 结构部分本身能通过校验，只差节点流程的改写；补全前校验有且只有这一条问题，不能运行
    ps = check(b)
    assert len(ps) == 1 and "补全" in ps[0].why
    assert check_text(new)[0].status == "needs_rewrite"
    # 补上积木后不再算"需要改写"
    b.flow.append({"scroll_all": {}})
    assert check(b) == [] and not is_needs_rewrite(b)


def test_ranked_output_and_params():
    new, _ = from_legacy((LEGACY / "fixture_dinner.yaml").read_text(encoding="utf-8"))
    b = parse(new)
    assert b.output == {"type": "ranked", "sort": ["价位:asc", "评分:desc"], "n": 2, "group_by": "key",
                        "unknown": "last"}
    assert {p["name"]: p["type"] for p in b.params} == {"BASE": "text", "location": "text", "n": "int",
                                                        "max_tier": "int"}
    assert b.flow[0]["search"] == {"url": "{BASE}/f23_dinner/search.html?q={q}",
                                   "query": "便宜 {key} {word} {location}"}
    assert "不高于 2 档" in b.note


def test_detect_unknown():
    assert detect("hello: 1") == "unknown" and detect("[1, 2]") == "unknown" and detect(":::") == "unknown"
