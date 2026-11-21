import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tb_fakes import FakeDoc, FakeEl, FakeSite, install, make_ctx, stub_4b  # noqa: E402
from test_taskbook_web import results_site  # noqa: E402

from hands.taskbook.engine import run_node  # noqa: E402

SEARCH = {"search": {"url": "http://s/search?q={q}"}}


def by_text(pred):
    return lambda region, lines: [no for no, text, _ in lines if pred(region, text)]


async def test_pick_maps_numbers_to_text_and_rejects(tmp_path, monkeypatch):
    install(monkeypatch)

    def pick(region, lines):
        nos = [no for no, text, _ in lines if text.startswith("结果")]
        return nos + [999]                                 # 999 不存在
    ctx = make_ctx(tmp_path, results_site(), [SEARCH, {"pick_list": {"region": "搜索结果", "max": 10, "as": "结果"}}],
                   client=stub_4b(pick))
    run = await run_node(ctx)
    texts = [i.text for i in run.lists["结果"]]
    assert texts == [f"结果 1-{i}" for i in range(5)]
    st = ctx.page.state()
    body = "\n".join(e.name for e in st.elements)
    assert all(t in body for t in texts)                   # 原文逐字来自页面
    rec = run.blocks[1]
    assert any(r["why"] == "编号不存在" and r["no"] == 999 for r in rec["rejected"])
    assert run.lists["结果"][0].context == "摘要 0" and run.lists["结果"][0].element


async def test_pick_anchor_dedupe_and_stop(tmp_path, monkeypatch):
    install(monkeypatch)
    seen_regions = []

    def pick(region, lines):
        seen_regions.append(region)
        # 故意把锚点之前的结果也选上；相关搜索里的词每屏都选（测去重）
        return [no for no, text, _ in lines if text in ("megatron", "擎天柱", "shockwave", "结果 1-4")]
    ctx = make_ctx(tmp_path, results_site(), [
        SEARCH, {"pick_list": {"region": "相关搜索", "anchor": ["Related searches", "相关搜索"], "max": 12,
                               "as": "相关"}}], client=stub_4b(pick))
    run = await run_node(ctx)
    rec = run.blocks[1]
    assert rec["anchor"] == "相关搜索"
    assert [i.text for i in run.lists["相关"]] == ["megatron", "擎天柱", "shockwave"]
    assert any(r["why"] == "重复" for r in rec["rejected"]) or len(rec["picked"]) == 1


async def test_pick_rejects_above_anchor_same_screen(tmp_path, monkeypatch):
    install(monkeypatch)
    els = [FakeEl("starscream", 200, context="结果摘要"), FakeEl("相关搜索", 300, role="generic"),
           FakeEl("megatron", 350, context="相关搜索 megatron")]
    site = FakeSite(lambda url: FakeDoc("p", els, height=800))
    ctx = make_ctx(tmp_path, site, [SEARCH, {"pick_list": {"region": "相关搜索", "anchor": ["相关搜索"], "as": "x"}}],
                   client=stub_4b(by_text(lambda r, t: t in ("starscream", "megatron"))))
    run = await run_node(ctx)
    assert [i.text for i in run.lists["x"]] == ["megatron"]
    assert [r["text"] for r in run.blocks[1]["rejected"] if r["why"] == "在锚点之前"] == ["starscream"]


async def test_pick_anchor_missing_falls_back(tmp_path, monkeypatch):
    install(monkeypatch)
    ctx = make_ctx(tmp_path, results_site(), [SEARCH, {"pick_list": {"region": "结果", "anchor": ["没有的锚点"],
                                                                      "max": 2, "as": "x"}}],
                   client=stub_4b(by_text(lambda r, t: t.startswith("结果"))))
    run = await run_node(ctx)
    assert "没找到锚点" in run.blocks[1]["note"] and len(run.lists["x"]) == 2


async def test_pick_stops_when_block_ends_and_max(tmp_path, monkeypatch):
    install(monkeypatch)
    # 每屏一个条目，条目在第 1、2 屏，第 3 屏没有 → 区块结束
    els = [FakeEl(f"条目{i}", 100 + i * 700) for i in range(2)] + [FakeEl("页脚", 2300)]
    site = FakeSite(lambda url: FakeDoc("长页", els, height=4000))
    ctx = make_ctx(tmp_path, site, [SEARCH, {"pick_list": {"region": "条目", "as": "x"}}],
                   client=stub_4b(by_text(lambda r, t: t.startswith("条目"))))
    run = await run_node(ctx)
    assert [i.text for i in run.lists["x"]] == ["条目0", "条目1"]
    assert len(run.blocks[1]["picked"]) == 3
    ctx = make_ctx(tmp_path, site, [SEARCH, {"pick_list": {"region": "条目", "max": 1, "as": "x"}}],
                   client=stub_4b(by_text(lambda r, t: t.startswith("条目"))))
    run = await run_node(ctx)
    assert [i.text for i in run.lists["x"]] == ["条目0"] and len(run.blocks[1]["picked"]) == 1


def test_split_shared_context():
    from hands.taskbook.engine import Item
    from hands.taskbook.pick import split_contexts
    block = "「x」的搜索结果 A 篇 讲 optimus 和 megatron B 篇 讲 starscream C 篇 资料"
    items = [Item("A 篇", context=block), Item("B 篇", context=block), Item("C 篇", context=block),
             Item("D", context="D 独立的块")]
    split_contexts(items)
    assert [i.context for i in items] == ["A 篇 讲 optimus 和 megatron", "B 篇 讲 starscream", "C 篇 资料",
                                          "D 独立的块"]


async def test_pick_retry_then_fail(tmp_path, monkeypatch):
    install(monkeypatch)
    client = stub_4b(by_text(lambda r, t: t.startswith("结果")), junk_first=2)
    ctx = make_ctx(tmp_path, results_site(), [SEARCH, {"pick_list": {"region": "结果", "max": 3, "as": "x"}}],
                   client=client)
    run = await run_node(ctx)
    assert len(run.lists["x"]) == 3 and len(run.blocks[1]["calls"]) == 3     # 两次乱答 + 一次正确
    client = stub_4b(by_text(lambda r, t: True), junk_first=3)
    ctx = make_ctx(tmp_path, results_site(), [SEARCH, {"pick_list": {"region": "结果", "as": "x"}}], client=client)
    run = await run_node(ctx)
    assert run.lists["x"] == [] and "3 次" in run.blocks[1]["error"]


async def test_read_fields_element_and_text_line(tmp_path, monkeypatch):
    install(monkeypatch)
    site = FakeSite(lambda url: FakeDoc("小馆 - 详情", [FakeEl("小馆", 50), FakeEl("预订", 90)],
                                        body="小馆\n地址：朝阳区\n价位：$$ · 川菜\n营业中"))

    def fields(names, els, lines):
        no_name = next(no for no, t, _ in els if t == "小馆")
        t_price = next(l.split(" ")[0] for l in lines if "价位" in l)
        return {"店名": no_name, "价位": t_price, "评分": None}
    ctx = make_ctx(tmp_path, site, [SEARCH, {"read_fields": {"names": ["店名", "价位", "评分"], "name_field": "店名",
                                                             "as": "详情"}}], client=stub_4b(fields=fields))
    run = await run_node(ctx)
    it = run.lists["详情"][0]
    assert it.text == "小馆" and it.fields["价位"].text == "价位：$$ · 川菜"
    assert it.fields["价位"].element.startswith("T") and "评分" not in it.fields
    assert run.blocks[1]["missing"] == ["评分"] and "未找到" in run.blocks[1]["note"]
