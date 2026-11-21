import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from tb_fakes import FakeExecutor, FakePage, install, stub_4b  # noqa: E402
from tb_sites import BASE, dinner_answer, dinner_fields, dinner_site, search_picker, search_site  # noqa: E402

from hands.pause import PauseGate  # noqa: E402
from hands.recorder import RunRecorder  # noqa: E402
from hands.taskbook import book as B  # noqa: E402
from hands.taskbook.engine import Item, NodeRun, TreeOp  # noqa: E402
from hands.taskbook.runner import TaskbookRunner  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
EXPECT = ["optimus", "optimus/galvatron", "optimus/megatron", "megatron/starscream", "megatron/shockwave",
          "megatron/soundwave", "optimus/starscream", "movie", "movie/fallen", "movie/decepticon", "movie/autobot",
          "decepticon/megatron", "megatron/starscream", "megatron/shockwave", "megatron/soundwave"]


class FakeBrowser:
    def __init__(self, site=None):
        self.page = FakePage(site) if site else FakePage(None)
        self.gate = PauseGate(watch_human=False)

    async def new_page(self):
        return self.page


class Cfg:
    min_interval_s = {}
    captcha_markers = ["captcha"]
    confirm = {}
    humanize = {}
    guard = {}
    vlm = {}
    desktop = {}
    driver = "fake"


def order_of(tree):
    out = []
    for e in tree.events:
        if e.op == "search":
            k = tree.key_of(e.node)
            out.append(f"{k}/{tree.nodes[e.node].word}" if k else tree.nodes[e.node].word)
    return out


def make_runner(tmp_path, book, engine=None, site=None, client=None, trial=False, budget=None, log=None):
    br = FakeBrowser(site)
    r = TaskbookRunner(br, Cfg(), RunRecorder(tmp_path, "t"), book, gate=br.gate, engine=engine, client=client,
                       trial=trial, budget=budget, log=(log.append if log is not None else (lambda m: None)))
    r.executor_factory = lambda p: FakeExecutor(p)
    return r


def simple_book(groups=(("optimus", ["galvatron", "megatron", "starscream"]),
                        ("megatron", ["starscream", "shockwave", "soundwave"]),
                        ("movie", ["fallen", "decepticon", "autobot"])), start="optimus", **lim):
    b = B.TaskBook(name="t", title="测试", groups=list(groups), start=start,
                   flow=[{"search": {"url": "http://s/?q={q}"}}], limits={**B.DEFAULT_LIMITS, **lim})
    b.text = B.dump(b)
    return b


def engine_from(rule):
    """假引擎：每个节点消耗 1 次，改树由 rule(node, tree) 给出 TreeOp 列表。"""
    async def eng(ctx):
        ctx.consume(1)
        return NodeRun(blocks=[{"step": "flow[0]", "block": "search", "cost": 1}], ops=list(rule(ctx.node, ctx.tree)),
                       cost=1)
    return eng


# ---------------- T33 主循环 ----------------
async def test_order_records_and_snapshots(tmp_path):
    def rule(n, t):
        if n.word == "decepticon":
            return [TreeOp("add_to_current", [Item("megatron", "e1", "http://s/?q=decepticon")], step="flow[5]")]
        return []
    r = make_runner(tmp_path, simple_book(), engine_from(rule))
    res = await r.run()
    assert order_of(r.tree) == EXPECT
    assert r.tree.mapping["decepticon"] == ["megatron"] and r.tree.used == 15
    d = r.dir
    assert (d / "taskbook.yaml").exists() and json.loads((d / "params.json").read_text(encoding="utf-8"))["budget"] == 120
    nodes = sorted((d / "nodes").iterdir())
    assert len(nodes) == 15 and all((n / "blocks.json").exists() for n in nodes)
    rec = json.loads((nodes[9] / "blocks.json").read_text(encoding="utf-8"))
    assert rec["word"] == "decepticon" and rec["applied"][0]["words"] == ["megatron"]
    assert len(list((d / "snapshots").glob("*.json"))) >= 15
    assert res["state"] == "done" and (d / "result.json").exists() and (d / "report.html").exists()
    assert r.evidence[("decepticon", "megatron")]["step"] == "flow[5]"


async def test_rejected_ops_recorded(tmp_path):
    def rule(n, t):
        return [TreeOp("add_to_key", [Item("x")], key="nokey", step="flow[1]")] if n.word == "optimus" else []
    r = make_runner(tmp_path, simple_book(), engine_from(rule), budget=2, trial=True)
    await r.run()
    rec = json.loads(sorted((r.dir / "nodes").iterdir())[0].joinpath("blocks.json").read_text(encoding="utf-8"))
    assert rec["rejected"][0]["rejected"].startswith("「nokey」不是已知的键")


async def test_discard_when_edited_during_node(tmp_path):
    holder = {}

    async def eng(ctx):
        ctx.consume(1)
        if ctx.node.word == "galvatron" and "done" not in holder:
            holder["done"] = True
            ctx.tree._touch(ctx.node.id)                    # 模拟执行期间被用户改过
            return NodeRun(ops=[TreeOp("add_to_current", [Item("zzz")], step="flow[0]")], cost=1)
        return NodeRun(cost=1)
    r = make_runner(tmp_path, simple_book(), eng, budget=3, trial=True)
    await r.run()
    assert "galvatron" not in r.tree.mapping
    gal = next(sorted((r.dir / "nodes").iterdir())[i] for i in range(3)
               if "galvatron" in sorted((r.dir / "nodes").iterdir())[i].name)
    assert json.loads((gal / "blocks.json").read_text(encoding="utf-8"))["discarded"] == "执行期间被修改"


# ---------------- T34 等待决策、停、试跑、改树 ----------------
async def test_budget_wait_grant_finish_and_edits(tmp_path):
    r = make_runner(tmp_path, simple_book(budget=3), engine_from(lambda n, t: []))
    task = asyncio.create_task(r.run())
    for _ in range(200):
        await asyncio.sleep(0.01)
        if r.state == "waiting":
            break
    assert r.state == "waiting" and r.tree.used == 3 and r.gate.paused.reason == "budget"
    with pytest.raises(PermissionError, match="另一端"):
        r.edit("grant", n=2, rev=r.tree.rev - 1, source="平板 · Safari")
    r.edit("grant", n=2, rev=r.tree.rev, source="平板 · Safari")
    for _ in range(200):
        await asyncio.sleep(0.01)
        if r.state == "waiting" and r.tree.used == 5:
            break
    assert r.tree.used == 5
    r.edit("finish", source="电脑")
    res = await asyncio.wait_for(task, 5)
    assert res["state"] == "done"
    edits = [json.loads(x) for x in (r.dir / "edits.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(e["op"], e["source"]) for e in edits] == [("grant", "平板 · Safari"), ("finish", "电脑")]
    assert [e["op"] for e in res["edits"]] == ["grant", "finish"]


async def test_edit_requires_pause(tmp_path):
    r = make_runner(tmp_path, simple_book(), engine_from(lambda n, t: []))
    r.state = "running"
    with pytest.raises(PermissionError, match="请先暂停"):
        r.edit("finish")


async def test_trial_stops_at_limit(tmp_path):
    r = make_runner(tmp_path, simple_book(trial_budget=4), engine_from(lambda n, t: []), trial=True)
    res = await asyncio.wait_for(r.run(), 5)
    assert r.tree.used == 4 and res["state"] == "done" and res["trial"] is True
    assert len(list((r.dir / "nodes").iterdir())) == 4


async def test_stop_finish_and_handoff(tmp_path):
    async def eng(ctx):
        ctx.consume(1)
        return NodeRun(cost=1, stop="finish" if ctx.node.word == "megatron" else None)
    r = make_runner(tmp_path, simple_book(), eng)
    res = await asyncio.wait_for(r.run(), 5)
    assert order_of(r.tree) == EXPECT[:3] and res["state"] == "done"

    async def eng2(ctx):
        ctx.consume(1)
        return NodeRun(cost=1, stop="handoff" if ctx.node.word == "galvatron" else None)
    r = make_runner(tmp_path, simple_book(), eng2)
    task = asyncio.create_task(r.run())
    for _ in range(200):
        await asyncio.sleep(0.01)
        if r.state == "waiting":
            break
    assert r.wait_reason == "decision" and r.gate.paused.reason == "decision"
    r.edit("finish")
    await asyncio.wait_for(task, 5)


async def test_node_budget_stop_returns_to_pending(tmp_path):
    async def eng(ctx):
        return NodeRun(cost=0, stop="budget")
    r = make_runner(tmp_path, simple_book(budget=1), eng, trial=True, budget=1)

    async def eng_first(ctx):                 # 第一个节点用掉唯一的 1 次，第二个节点次数不够
        if ctx.node.word == "optimus":
            ctx.consume(1)
            return NodeRun(cost=1)
        return await eng(ctx)
    r.engine = eng_first
    await asyncio.wait_for(r.run(), 5)
    gal = next(n for n in r.tree.nodes.values() if n.word == "galvatron")
    assert gal.state == "pending"


# ---------------- T35 夹具任务书全流程（假网站 + 替身 4b + 真引擎） ----------------
async def test_fixture_transformers_full(tmp_path, monkeypatch):
    install(monkeypatch)
    b = B.bind(B.load(ROOT / "tasks" / "fixture_transformers.yaml"), {"BASE": BASE})
    r = make_runner(tmp_path, b, site=search_site(), client=stub_4b(search_picker))
    res = await asyncio.wait_for(r.run(), 30)
    assert order_of(r.tree) == EXPECT
    assert r.tree.mapping.get("decepticon") == ["megatron"]
    assert "擎天柱" not in json.dumps(r.tree.mapping, ensure_ascii=False)
    assert res["state"] == "done" and r.tree.used == 15
    first = json.loads(sorted((r.dir / "nodes").iterdir())[0].joinpath("blocks.json").read_text(encoding="utf-8"))
    flt = next(x for x in first["blocks"] if x["block"] == "filter")
    reasons = {d["text"]: d["reason"] for d in flt["dropped"]}
    assert reasons.get("擎天柱") == "别名归一后是当前词" and reasons.get("Optimus Prime") == "别名归一后是当前词"
    rel = next(x for x in first["blocks"] if x["block"] == "pick_list" and x["params"]["as"] == "相关")
    assert [o["text"] for o in rel["out"]] == ["megatron", "擎天柱", "starscream", "Optimus Prime"]


async def test_fixture_dinner_full(tmp_path, monkeypatch):
    install(monkeypatch)
    b = B.bind(B.load(ROOT / "tasks" / "fixture_dinner.yaml"), {"BASE": BASE})
    r = make_runner(tmp_path, b, site=dinner_site(), client=stub_4b(search_picker, dinner_fields))
    res = await asyncio.wait_for(r.run(), 30)
    top = res["ranked"]["top"]
    for cuisine, names in dinner_answer().items():
        assert [x["word"] for x in top[cuisine]] == names, cuisine
    unknown = {x["word"] for x in res["ranked"]["unknown"]}
    assert unknown == {"江南食堂", "东北饺子炒菜"}
    assert "大阪寿司屋" not in json.dumps(r.tree.mapping, ensure_ascii=False)      # $$$ 被比较积木滤掉
    assert r.tree.used == 19 and res["state"] == "done"
    lf = next(x for x in top["韩式拌饭"])
    assert lf["fields"]["价位"] == "价位：$" and lf["evidence"]["url"].endswith("id=k1")
