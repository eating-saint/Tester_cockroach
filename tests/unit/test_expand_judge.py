import asyncio
import json

from hands.config import Config
from hands.expand.judge import ExpandJudge, candidates_from, check_ops
from hands.expand.spec import Condition, TaskType
from hands.expand.tree import ExpandTree
from hands.vlm.client import FuncClient

TT = TaskType("t", "t", "http://s/?q={q}", "{value}", [("movie", ["decepticon"])], "movie", ["结果"],
              [Condition("c1", "≥80% 提到")])
EXTRACT = [{"field": "候选", "value": "megatron", "grounded": True, "where": "body", "element": None},
           {"field": "候选", "value": "unicron", "grounded": False, "where": None, "element": None},
           {"field": "店名", "value": "x", "grounded": True}]


def _tree():
    t = ExpandTree(TT.groups, "movie")
    r = t.next_node(); t.mark_searched(r.id)
    d = t.next_node(); t.mark_searched(d.id)
    return t, d


def test_candidates_and_checks():
    c = candidates_from(EXTRACT)
    assert [x["no"] for x in c] == [1, 2] and c[1]["grounded"] is False
    t, d = _tree()
    ok, bad = check_ops([{"op": "new_key", "from": 1, "reason": "80%"},
                         {"op": "new_key", "from": 2},
                         {"op": "add_value", "key": "nope", "from": 1},
                         {"op": "rename"},
                         {"op": "terminal"}], d, t, c)
    assert [o["op"] for o in ok] == ["new_key", "terminal"] and ok[0]["word"] == "megatron"
    assert [b["rejected"][:3] for b in bad] == ["无依据", "不允许", "不允许"]


def test_judge_retries_and_records(tmp_path):
    t, d = _tree()
    answers = iter(["不是json", json.dumps({"ops": [{"op": "new_key", "from": 1, "condition": "c1"}]})])
    client = FuncClient(lambda *a: next(answers))
    j = ExpandJudge(Config(chrome_path="x"), client)
    res = asyncio.run(j.judge(d, t, candidates_from(EXTRACT), TT, None, tmp_path))
    assert res["tries"] == 2 and res["ok"][0]["word"] == "megatron"
    assert "上一次的问题" in client.calls[1]["user"]
    assert (tmp_path / "checked.json").exists()
    fail = ExpandJudge(Config(chrome_path="x"), FuncClient(lambda *a: "x"))
    r2 = asyncio.run(fail.judge(d, t, [], TT, None, tmp_path / "f"))
    assert r2["failed"] and r2["ok"] == []
