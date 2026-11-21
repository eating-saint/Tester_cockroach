from hands.expand.tree import ExpandTree

GROUPS = [("optimus", ["galvatron", "megatron", "starscream"]),
          ("megatron", ["starscream", "shockwave", "soundwave"]),
          ("movie", ["fallen", "decepticon", "autobot"])]


def run(tree, judge=lambda n, t: [{"op": "terminal", "reason": "无新连接"}], limit=100):
    order = []
    for _ in range(limit):
        n = tree.next_node()
        if n is None:
            break
        tree.mark_searched(n.id)
        key = tree.key_of(n.id)
        order.append(f"{key}/{n.word}" if key else n.word)
        if n.state != "key":
            tree.apply(n.id, judge(n, tree))
    return order


def test_transformers_order_and_rules():
    t = ExpandTree(GROUPS, "optimus")

    def judge(n, tree):
        if n.word == "decepticon":
            return [{"op": "new_key", "word": "megatron", "reason": "第一页 80% 提到 megatron"}]
        return [{"op": "terminal", "reason": "无"}]
    order = run(t, judge)
    assert order[:7] == ["optimus", "optimus/galvatron", "optimus/megatron", "megatron/starscream",
                         "megatron/shockwave", "megatron/soundwave", "optimus/starscream"]
    # megatron 已在 optimus 下展开过，不再单独作为根；接着是 movie
    assert order[7:11] == ["movie", "movie/fallen", "movie/decepticon", "movie/autobot"]
    # decepticon 新键在 movie 之后处理：megatron 在它下面再展开一次（每次出现都复查）
    assert order[11:] == ["decepticon/megatron", "megatron/starscream", "megatron/shockwave", "megatron/soundwave"]
    assert t.mapping["decepticon"] == ["megatron"]
    assert t.finished and t.used == 15
    starscreams = [n for n in t.nodes.values() if n.word == "starscream"]
    assert len(starscreams) == 3 and all(n.searches == 1 for n in starscreams)
    assert all(n.state in ("key", "terminal") for n in t.nodes.values())


def test_cycle_becomes_terminal():
    t = ExpandTree([("a", ["b"]), ("b", ["a"])], "a")
    order = run(t)
    assert order == ["a", "a/b", "b/a"]
    a2 = [n for n in t.nodes.values() if n.word == "a" and n.parent is not None][0]
    assert a2.state == "terminal" and any(r["reason"] == "循环" for r in a2.reasons)


def test_add_value_and_tentative_finalize():
    t = ExpandTree([("k", ["v1", "v2"])], "k")
    n = t.next_node(); t.mark_searched(n.id)
    v1 = t.next_node(); t.mark_searched(v1.id)
    t.apply(v1.id, [{"op": "add_value", "key": "k", "word": "v3", "reason": "相关"}])
    assert [t.nodes[c].word for c in t.nodes[n.id].children] == ["v1", "v2", "v3"]
    assert v1.state == "tentative"
    rej = t.apply(v1.id, [{"op": "rename"}, {"op": "add_value", "key": "zzz", "word": "q"}])
    assert len(rej) == 2
    run(t, judge=lambda n, tr: [])          # 判定什么都不给：暂定叶子在根结束时转为末端叶子
    assert v1.state == "terminal" and t.finished


def test_user_edits_and_budget():
    t = ExpandTree(GROUPS, "optimus", budget=3)
    run(t, limit=3)
    assert t.exhausted
    t.grant(5)
    assert not t.exhausted and t.budget == 8
    gal = next(n for n in t.nodes.values() if n.word == "galvatron")
    t.set_pending(gal.id)
    assert t.next_node().id == gal.id         # 改回待查的被重新选中
    meg = next(n for n in t.nodes.values() if n.word == "megatron" and n.state == "key")
    t.delete(meg.id)
    assert all(t.nodes[c].state == "deleted" for c in meg.children)
    t.add_group("cybertron", ["iacon"])
    order = run(t, limit=50)
    assert "megatron/shockwave" not in order and "cybertron" in order and "cybertron/iacon" in order
    assert t.finished
    ops = [e.op for e in t.events if e.actor == "user" and e.op != "create"]
    assert ops == ["grant", "set_pending", "delete", "add_group"]


def test_new_keys_keep_order():
    t = ExpandTree([("晚餐", ["韩式", "寿司", "炒菜"])], "晚餐")

    def judge(n, tr):
        if n.word in ("韩式", "寿司", "炒菜"):
            return [{"op": "new_key", "word": n.word + "店A"}, {"op": "add_value", "key": n.word, "word": n.word + "店B"}]
        return [{"op": "terminal"}]
    order = run(t, judge)
    assert order == ["晚餐", "晚餐/韩式", "晚餐/寿司", "晚餐/炒菜",
                     "韩式/韩式店A", "韩式/韩式店B", "寿司/寿司店A", "寿司/寿司店B", "炒菜/炒菜店A", "炒菜/炒菜店B"]
    assert len(t.leaves()) == 6 and t.finished


# ---------------- 任务书：rev、apply_rule、new_terminal ----------------
def run_rule(tree, rule=lambda n, t: [], limit=100):
    """任务书式运行：mark_searched 不计次，每个节点 consume(1)，改树走 apply_rule。"""
    order = []
    for _ in range(limit):
        n = tree.next_node()
        if n is None:
            break
        tree.mark_searched(n.id, count=False)
        tree.consume(1)
        key = tree.key_of(n.id)
        order.append(f"{key}/{n.word}" if key else n.word)
        for op in rule(n, tree):
            tree.apply_rule(n.id, **op)
    return order


def test_rev_increases_on_every_change():
    t = ExpandTree(GROUPS, "optimus")
    assert t.rev >= 1 and t.snapshot()["rev"] == t.rev
    seen = [t.rev]
    n = t.next_node(); t.mark_searched(n.id); seen.append(t.rev)
    t.consume(1); seen.append(t.rev)
    t.grant(2); seen.append(t.rev)
    t.add_group("cybertron", ["iacon"]); seen.append(t.rev)
    gal = next(x for x in t.nodes.values() if x.word == "galvatron")
    t.apply_rule(gal.id, "terminal", []); seen.append(t.rev)
    t.delete(gal.id); seen.append(t.rev)
    assert all(b > a for a, b in zip(seen, seen[1:]))
    before = t.rev
    t.counts(); t.snapshot(); t.leaves(); t.summary_text()
    assert t.rev == before


def test_mark_searched_without_count():
    t = ExpandTree(GROUPS, "optimus")
    n = t.next_node(); t.mark_searched(n.id, count=False)
    assert t.used == 0
    t.consume(2)
    assert t.used == 2


def test_rule_order_same_as_judge():
    """add_to_current 与上一章 new_key / add_value 的展开顺序一致。"""
    t = ExpandTree(GROUPS, "optimus")

    def rule(n, tree):
        if n.word == "decepticon":
            return [{"op": "add_to_current", "words": ["megatron"]}]
        return []
    order = run_rule(t, rule)
    assert order[11:] == ["decepticon/megatron", "megatron/starscream", "megatron/shockwave", "megatron/soundwave"]
    assert t.origin_of[("decepticon", "megatron")] == "rule"
    assert t.finished and t.used == 15


def test_rule_rejects_and_sequence():
    t = ExpandTree([("k", ["v1", "v2"])], "k")
    n = t.next_node(); t.mark_searched(n.id, count=False)
    v1 = t.next_node(); t.mark_searched(v1.id, count=False)
    assert t.apply_rule(v1.id, "add_to_key", ["x"], key="zzz") == [{"word": "x", "rejected": "「zzz」不是已知的键"}]
    rej = t.apply_rule(v1.id, "add_to_key", ["v2", "v3"], key="k")
    assert rej == [{"word": "v2", "rejected": "这个值已经在该键下"}]
    assert [t.nodes[c].word for c in n.children] == ["v1", "v2", "v3"]
    # 连续两条 add_to_current：第一条让 v1 成为键，第二条看到它已是键，不重复插入根
    roots_before = len(t.roots)
    t.apply_rule(v1.id, "add_to_current", ["a"], why={"step": "flow[5]"})
    t.apply_rule(v1.id, "add_to_current", ["b", "a"])
    assert len(t.roots) == roots_before + 1
    assert t.mapping["v1"] == ["a", "b"]
    assert v1.state == "key"
    assert any(r.get("step") == "flow[5]" and r.get("word") == "a" for r in v1.reasons)
    assert t.apply_rule(v1.id, "rename", []) [0]["rejected"].startswith("不认识")


def test_new_terminal_values_are_leaves():
    t = ExpandTree([("晚餐", ["川菜"])], "晚餐", budget=10)

    def rule(n, tree):
        if n.word == "川菜":
            return [{"op": "add_to_current", "words": ["店A", "店B"], "new_terminal": True}]
        return []
    order = run_rule(t, rule)
    assert order == ["晚餐", "晚餐/川菜"]           # 店A、店B 不再搜索
    leaves = {n.word for n in t.leaves()}
    assert {"店A", "店B"} <= leaves
    assert t.used == 2 and t.finished
    shop = next(n for n in t.nodes.values() if n.word == "店A")
    assert shop.reasons[0]["actor"] == "rule"
