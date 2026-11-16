"""展开报告页：不依赖浏览器，用手造的树生成（T45）。"""
from types import SimpleNamespace

from hands.expand.run import ExpandRunner
from hands.expand.spec import Condition, TaskType
from hands.expand.tree import ExpandTree


def test_report_html(tmp_path):
    tt = TaskType("t", "测试", "http://s/?q={q}", "{value}", [("a", ["b", "c"])], "a", [], [Condition("c1", "x")])
    r = ExpandRunner.__new__(ExpandRunner)
    r.tt, r.tree = tt, ExpandTree(tt.groups, "a")
    for _ in range(3):
        n = r.tree.next_node()
        r.tree.mark_searched(n.id)
        if n.state != "key":
            r.tree.apply(n.id, [{"op": "terminal", "reason": "无新连接"}])
    r.tree.delete(next(x.id for x in r.tree.nodes.values() if x.word == "c"))
    r.state, r.result = "done", {"state": "done", "leaves": [{"word": "b"}]}
    r.tree.next_node()
    html = r.report_html()
    assert "展开过程：测试" in html and "无新连接" in html and "delete" in html and "末端叶子" in html
