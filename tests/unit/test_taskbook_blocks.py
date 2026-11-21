import json

from hands.taskbook.blocks import BUILTIN_LISTS, PARAM_TYPES, REGISTRY, registry_json

IDS = ["search", "scroll_all", "pick_list", "open", "read_fields", "ratio", "rank", "filter", "tree", "stop",
       "page", "compare"]


def test_twelve_blocks():
    assert list(REGISTRY) == IDS
    assert BUILTIN_LISTS == ("当前词", "所在键", "已知词", "已知键")


def test_produces_has_as_and_cost():
    for b in REGISTRY.values():
        names = [p.name for p in b.params]
        assert b.produces == ("as" in names), b.id
        assert len(names) == len(set(names)), b.id
        assert all(p.type in PARAM_TYPES for p in b.params), b.id
        assert b.label and b.help and b.actor in ("program", "4b")
    assert {b.id for b in REGISTRY.values() if b.cost} == {"search", "open", "page"}
    assert {b.id for b in REGISTRY.values() if b.actor == "4b"} == {"pick_list", "read_fields"}


def test_guide_matches_registry_and_examples_validate():
    """AC8：指导提示词里的积木清单与程序一致，两份示例截出来导入后校验通过。"""
    import re

    from hands.taskbook.guide import build
    from hands.taskbook.validate import check_text
    text = build()
    assert "{{" not in text
    for d in registry_json():
        assert f"`{d['id']}`（{d['label']}）" in text
        for p in d["params"]:
            assert f"| `{p['name']}` |" in text, (d["id"], p["name"])
    examples = re.findall(r"```yaml\n(.*?)```", text, re.S)
    examples = [e for e in examples if e.startswith("# 任务书")]
    assert len(examples) == 2
    for e in examples:
        b, probs = check_text(e)
        assert b is not None and probs == [], probs


def test_registry_json_serializable():
    data = registry_json()
    assert [d["id"] for d in data] == IDS
    json.dumps(data, ensure_ascii=False)
    assert data[2]["params"][2]["default"] == 10
