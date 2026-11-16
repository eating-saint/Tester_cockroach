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
