import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from tb_fakes import FakeDoc, FakeSite, make_ctx  # noqa: E402

from hands.taskbook import engine  # noqa: E402
from hands.taskbook.engine import Item, run_node  # noqa: E402

SITE = FakeSite(lambda url: FakeDoc("空", []))


def fake_handlers(monkeypatch, calls, boom=()):
    real = engine._handlers()

    def make(bid):
        async def h(ctx, p, lists, rec, run, step):
            calls.append(step)
            if step in boom:
                raise RuntimeError("坏了")
            if bid == "pick_list":
                return [Item(x) for x in p.get("region", "").split(",") if x]
            return await real[bid](ctx, p, lists, rec, run, step)
        return h

    def handlers():
        out = dict(real)
        for bid in ("search", "scroll_all", "pick_list", "open", "page", "read_fields"):
            out[bid] = make(bid)
        return out
    monkeypatch.setattr(engine, "_handlers", handlers)


async def test_order_error_continue_and_lists(tmp_path, monkeypatch):
    calls = []
    fake_handlers(monkeypatch, calls, boom={"flow[1]"})
    flow = [{"search": {"url": "u?q={q}"}},
            {"pick_list": {"region": "a,b", "as": "坏"}},
            {"pick_list": {"region": "megatron,shockwave,megatron", "as": "相关"}},
            {"filter": {"in": "相关", "only_known": True, "as": "已知的"}},
            {"filter": {"in": "坏", "as": "空的"}}]
    ctx = make_ctx(tmp_path, SITE, flow)
    run = await run_node(ctx)
    assert calls == ["flow[0]", "flow[1]", "flow[2]"]
    assert [b["step"] for b in run.blocks] == [f"flow[{i}]" for i in range(5)]
    assert "坏了" in run.blocks[1]["error"] and run.lists["坏"] == []
    assert [i.text for i in run.lists["已知的"]] == ["megatron"]
    assert run.blocks[3]["dropped"][0]["reason"] in ("不在已知词", "重复")
    assert run.blocks[4]["inputs"] == {"in": 0} and run.lists["空的"] == []
    assert run.cost == 1 and ctx.tree.used == 1          # 只有 search 消耗
    assert run.stop is None


async def test_budget_stops_before_costly_block(tmp_path, monkeypatch):
    calls = []
    fake_handlers(monkeypatch, calls)
    flow = [{"search": {"url": "u?q={q}"}}, {"scroll_all": {}}, {"search": {"url": "u?q={q}"}}, {"scroll_all": {}}]
    ctx = make_ctx(tmp_path, SITE, flow, budget=1)
    run = await run_node(ctx)
    assert calls == ["flow[0]", "flow[1]"]
    assert run.stop == "budget" and run.blocks[-1]["skipped"] == "次数用满"


async def test_should_stop(tmp_path, monkeypatch):
    calls = []
    fake_handlers(monkeypatch, calls)
    ctx = make_ctx(tmp_path, SITE, [{"scroll_all": {}}, {"scroll_all": {}}])
    ctx.should_stop = lambda: len(calls) >= 1
    run = await run_node(ctx)
    assert calls == ["flow[0]"] and run.stop == "cancel"


async def test_tree_ops_recorded_not_applied(tmp_path, monkeypatch):
    fake_handlers(monkeypatch, [])
    flow = [{"pick_list": {"region": "megatron,cybertron", "as": "相关"}},
            {"tree": {"use": "相关", "op": "add_to_current"}},
            {"tree": {"use": "当前词", "op": "terminal"}},
            {"pick_list": {"region": "", "as": "空"}},
            {"tree": {"use": "空", "op": "delete"}}]
    ctx = make_ctx(tmp_path, SITE, flow)
    before = dict(ctx.tree.mapping)
    run = await run_node(ctx)
    assert [(o.op, [w.text for w in o.words], o.step) for o in run.ops] == [
        ("add_to_current", ["megatron", "cybertron"], "flow[1]"), ("terminal", ["optimus"], "flow[2]")]
    assert ctx.tree.mapping == before                   # 改树延后到节点跑完
    assert "为空" in run.blocks[4]["note"]


async def test_stop_block(tmp_path, monkeypatch):
    calls = []
    fake_handlers(monkeypatch, calls)
    flow = [{"pick_list": {"region": "", "as": "空"}}, {"stop": {"when": "空"}},
            {"pick_list": {"region": "x", "as": "有"}}, {"stop": {"when": "有", "action": "handoff"}},
            {"scroll_all": {}}]
    run = await run_node(make_ctx(tmp_path, SITE, flow))
    assert calls == ["flow[0]", "flow[2]"]
    assert run.stop == "handoff" and run.blocks[3]["evidence"] == ["x"]
    assert len(run.blocks) == 4


async def test_builtin_lists_live(tmp_path, monkeypatch):
    fake_handlers(monkeypatch, [])
    flow = [{"ratio": {"in": "结果", "words": "已知词", "as": "高频"}}]
    ctx = make_ctx(tmp_path, SITE, [{"pick_list": {"region": "megatron x,megatron y,about starscream", "as": "结果"}},
                                    {"ratio": {"in": "结果", "words": "已知词", "at_least": 0.6, "as": "高频"}},
                                    {"compare": {"in": "结果", "field": "价位", "op": "<=", "value": "$$",
                                                 "as": "便宜", "unknown_as": "未知"}},
                                    {"tree": {"use": "未知", "op": "add_to_current", "new_terminal": True}}])
    run = await run_node(ctx)
    assert [i.text for i in run.lists["高频"]] == ["megatron"]
    assert len(run.lists["未知"]) == 3 and run.ops[0].new_terminal
    lt = engine.ListTable(ctx)
    assert [i.text for i in lt.get_list("当前词")] == ["optimus"] and lt.get_list("所在键") == []
    assert [i.text for i in lt.get_list("已知键")] == ["optimus"]
    ctx.tree.mapping["新键"] = ["新值"]
    assert "新值" in [i.text for i in lt.get_list("已知词")]
    del flow


async def test_model_unavailable_aborts(tmp_path, monkeypatch):
    from hands.vlm.client import ModelUnavailable

    async def boom(*a):
        raise ModelUnavailable("模型服务没有启动")
    monkeypatch.setattr(engine, "_handlers", lambda: {**{k: boom for k in ("pick_list",)}})
    with pytest.raises(ModelUnavailable):
        await run_node(make_ctx(tmp_path, SITE, [{"pick_list": {"region": "x", "as": "a"}}]))
