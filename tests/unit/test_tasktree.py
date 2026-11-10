from hands.tasktree import TaskTree


def test_child_complete_returns_summary_and_clears_context():
    t = TaskTree("root goal", max_depth=2, max_steps=10)
    t.record_step({"tool": "read_text"})
    r = t.create_child("读取评论", "读完")
    assert r["ok"]
    child_id = r["node_id"]
    t.record_step({"tool": "click", "page": "BIG"})
    t.complete("五条评论")
    assert t.active_id == "root"
    assert t.nodes[child_id].context is None
    assert t.root.child_summaries == [{"goal": "读取评论", "status": "done", "summary": "五条评论"}]
    assert all("page" not in s for s in t.root.context)
    assert t.shape() == ["root", "root/读取评论"]


def test_max_depth():
    t = TaskTree("g", max_depth=1)
    assert t.create_child("a")["ok"]
    assert t.create_child("b") == {"ok": False, "error": "max_depth"}
    assert len(t.nodes) == 2


def test_step_limit_force_fail_returns_to_parent():
    t = TaskTree("g", max_depth=2, max_steps=3)
    t.create_child("c")
    for _ in range(3):
        t.tick_step()
    assert t.active_id == "root"
    assert t.events[-1].kind == "force_fail"
    assert t.root.child_summaries[0]["status"] == "failed"


def test_root_limit_finishes():
    t = TaskTree("g", max_steps=2)
    t.tick_step(); t.tick_step()
    assert t.finished and t.root.status == "failed" and t.root.summary == "step_limit"


def test_root_complete_finishes():
    t = TaskTree("g")
    t.complete("ok")
    assert t.finished and t.root.status == "done"


def test_only_active_receives_steps():
    t = TaskTree("g")
    t.create_child("c")
    t.record_step({"tool": "x"})
    assert t.root.context == []
    running = [n for n in t.nodes.values() if n.status == "running" and n.id == t.active_id]
    assert len(running) == 1
