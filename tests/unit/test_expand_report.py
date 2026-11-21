"""任务书运行的结果与报告页：不依赖浏览器，用手造的树生成。"""
import json

from hands.expand.tree import ExpandTree
from hands.taskbook.book import TaskBook
from hands.taskbook.report import build_result, ranked, report_html


def _tree():
    t = ExpandTree([("a", ["b", "c"])], "a")
    for _ in range(3):
        n = t.next_node()
        t.mark_searched(n.id)
        if n.state != "key":
            t.apply_rule(n.id, "terminal", [], why={"step": "flow[2]", "reason": "无新连接"})
    t.delete(next(x.id for x in t.nodes.values() if x.word == "c"))
    t.next_node()
    return t


def test_report_html(tmp_path):
    t = _tree()
    book = TaskBook(name="t", title="测试")
    node_dir = tmp_path / "nodes" / "0002_b"
    node_dir.mkdir(parents=True)
    (node_dir / "blocks.json").write_text(json.dumps({"cost": 1, "blocks": [
        {"step": "flow[0]", "block": "filter", "label": "过滤", "out": [{"text": "x"}],
         "dropped": [{"text": "擎天柱", "reason": "别名归一后是当前词"}], "shots": ["s01.png"]}]},
        ensure_ascii=False), encoding="utf-8")
    res = build_result(t, book, {("a", "b"): {"step": "flow[1]", "text": "b", "url": "http://s/b"}})
    html = report_html(t, book, res, tmp_path)
    assert "任务书运行：测试" in html and "无新连接" in html and "delete" in html and "末端叶子" in html
    assert "擎天柱（别名归一后是当前词）" in html and "nodes/0002_b/s01.png" in html
    lf = res["leaves"][0]
    assert lf["word"] == "b" and lf["key"] == "a" and lf["url"] == "http://s/b" and lf["step"] == "flow[2]"


def leaf(word, key, **fields):
    return {"word": word, "key": key, "fields": fields, "url": ""}


def test_ranked_unknown_last_and_exclude():
    leaves = [leaf("A", "韩", 价位="价位：$$", 评分="评分：4.7"), leaf("B", "韩", 价位="价位：$", 评分="评分：4.5"),
              leaf("C", "韩", 价位="价位：未标注", 评分="评分：4.9"), leaf("D", "韩", 价位="价位：人均 ¥45", 评分="评分：4.2"),
              leaf("E", "日", 价位="价位：$", 评分="评分：4.1")]
    o = {"type": "ranked", "sort": ["价位:asc", "评分:desc"], "n": 4, "group_by": "key", "unknown": "last"}
    r = ranked(leaves, o)
    assert [x["word"] for x in r["top"]["韩"]] == ["B", "D", "A", "C"]
    assert [x["word"] for x in r["unknown"]] == ["C"]
    r = ranked(leaves, {**o, "unknown": "exclude", "n": 2})
    assert [x["word"] for x in r["top"]["韩"]] == ["B", "D"] and "C" not in str(r["top"])
    r = ranked(leaves, {**o, "group_by": None, "n": 3})
    assert [x["word"] for x in r["top"][""]] == ["B", "D", "E"]          # 同为 1 档按评分从高到低
