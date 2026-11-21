import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tb_fakes import FakeDoc, FakeEl, FakeSite, install, make_ctx, query_of  # noqa: E402

from hands.taskbook import web  # noqa: E402
from hands.taskbook.engine import run_node  # noqa: E402


def results_site(pages=2):
    def route(url):
        if "/detail/" in url:
            name = url.rsplit("/", 1)[-1]
            return FakeDoc(f"详情 {name}", [FakeEl("返回", 10, "/back")], body=f"店名：{name}\n价位：$$")
        p = int((url.split("&p=")[1:] or ["1"])[0][:1])
        els = [FakeEl(f"结果 {p}-{i}", 100 + i * 120, f"http://s/detail/r{p}{i}", context=f"摘要 {i}")
               for i in range(5)]
        els.append(FakeEl("相关搜索", 1500, None, role="generic"))
        els += [FakeEl(w, 1550 + i * 40, f"http://s/search?q={w}", context="相关搜索 " + w)
                for i, w in enumerate(["megatron", "擎天柱", "shockwave"])]
        if p < pages:
            els.append(FakeEl("下一页", 2200, url.split("&p=")[0] + f"&p={p + 1}"))
        return FakeDoc(f"搜索 {query_of(url)} 第{p}页", els, body="\n".join(e.text for e in els), height=2400)
    return FakeSite(route)


def test_build_search_url():
    url, q = web.build_search_url({"url": "https://g.com/search?q={q}", "query": "{word} {city} 榜",
                                   "extra": {"hl": "{lang}", "num": 20}}, "川菜", "晚餐",
                                  {"city": "北京", "lang": "zh-CN"})
    assert q == "川菜 北京 榜"
    assert url == "https://g.com/search?q=%E5%B7%9D%E8%8F%9C+%E5%8C%97%E4%BA%AC+%E6%A6%9C&hl=zh-CN&num=20"


async def test_search_interval_and_shot(tmp_path, monkeypatch):
    install(monkeypatch)
    waits = []

    async def fake_interval(last, url, gaps, log):
        waits.append(url)
    monkeypatch.setattr(web, "respect_interval", fake_interval)
    site = results_site()
    ctx = make_ctx(tmp_path, site, [{"search": {"url": "http://s/search?q={q}", "query": "{word} transformers",
                                                "extra": {"hl": "en"}}}])
    run = await run_node(ctx)
    rec = run.blocks[0]
    assert rec["url"] == "http://s/search?q=optimus+transformers&hl=en" == site.visits[0] == waits[0]
    assert rec["shots"] == ["s01.png"] and (tmp_path / "s01.png").exists()
    assert rec["cost"] == 1 and ctx.tree.used == 1


async def test_scroll_all_bottom_then_top(tmp_path, monkeypatch):
    install(monkeypatch)
    ctx = make_ctx(tmp_path, results_site(), [{"search": {"url": "http://s/search?q={q}"}}, {"scroll_all": {}}])
    run = await run_node(ctx)
    downs = [a for a in ctx.actions if a[1] == "down"]
    assert downs[-1][2] == 1600                         # 2400 - 800：到底
    assert [a[2] for a in downs] == [720, 1440, 1600, 1600]      # 一屏 0.9×800；位置不再变化就停
    assert ctx.page.scroll_y == 0 and run.blocks[1]["screens"] == 3


async def test_scroll_all_limit(tmp_path, monkeypatch):
    install(monkeypatch)
    site = FakeSite(lambda url: FakeDoc("长", [], height=10 ** 6))
    ctx = make_ctx(tmp_path, site, [{"search": {"url": "http://s/?q={q}"}}, {"scroll_all": {}}])
    run = await run_node(ctx)
    assert run.blocks[1]["screens"] == web.MAX_SCROLL_SCREENS


async def test_scroll_to_text_and_fallback(tmp_path, monkeypatch):
    install(monkeypatch)
    ctx = make_ctx(tmp_path, results_site(), [{"search": {"url": "http://s/search?q={q}"}},
                                              {"scroll_all": {"mode": "to_text", "text": "megatron"}},
                                              {"scroll_all": {"mode": "to_text", "text": "不存在的字"}}])
    run = await run_node(ctx)
    assert run.blocks[1]["found"] == "megatron"
    assert "没找到「不存在的字」" in run.blocks[2]["note"] and run.blocks[2]["screens"] >= 1


OPEN_FLOW = [{"search": {"url": "http://s/search?q={q}"}},
             {"pick_list": {"region": "结果", "as": "结果"}},
             {"open": {"from": "结果", "n": 2, "back": True, "flow": [{"scroll_all": {}}]}},
             {"open": {"from": "结果", "n": 9, "flow": []}}]


async def _open_case(tmp_path, monkeypatch, tabs):
    install(monkeypatch)
    from hands.taskbook import engine
    real = engine._handlers()

    async def fake_pick(ctx, p, lists, rec, run, step):
        st = ctx.page.state()
        from hands.taskbook.engine import Item
        return [Item(e.name, e.id, st.url) for e in st.elements if e.name.startswith("结果")]
    monkeypatch.setattr(engine, "_handlers", lambda: {**real, "pick_list": fake_pick})
    ctx = make_ctx(tmp_path, results_site(), OPEN_FLOW, tabs=tabs)
    run = await run_node(ctx)
    return ctx, run


async def test_open_same_page_and_back(tmp_path, monkeypatch):
    ctx, run = await _open_case(tmp_path, monkeypatch, tabs=False)
    rec = next(b for b in run.blocks if b["step"] == "flow[2]")
    sub = rec["opened"][0]
    assert sub["how"] == "同一页" and sub["target"] == "结果 1-1" and sub["back"] is True
    assert ctx.page.cur.startswith("http://s/search")
    steps = [b["step"] for b in run.blocks]
    assert steps.index("flow[2]") < steps.index("flow[2].open.flow[0]")   # 子流程记录在后
    last = run.blocks[-1]
    assert "没有第 9 条" in last["note"] and last["cost"] == 0
    assert ctx.tree.used == 2                                               # 搜索 1 + 打开 1


async def test_open_new_tab(tmp_path, monkeypatch):
    ctx, run = await _open_case(tmp_path, monkeypatch, tabs=True)
    rec = next(b for b in run.blocks if b["step"] == "flow[2]")
    assert rec["opened"][0]["how"] == "新标签页" and rec["opened"][0]["back"] is True
    assert ("open_tab", "结果 1-1") in ctx.actions


async def test_open_count_merges_sub_outputs(tmp_path, monkeypatch):
    install(monkeypatch)
    from hands.taskbook import engine
    from hands.taskbook.engine import Item
    real = engine._handlers()

    async def fake_pick(ctx, p, lists, rec, run, step):
        st = ctx.page.state()
        return [Item(e.name, e.id, st.url) for e in st.elements if e.name.startswith("结果")]

    async def fake_fields(ctx, p, lists, rec, run, step):
        return [Item(ctx.page.state().title)]
    monkeypatch.setattr(engine, "_handlers", lambda: {**real, "pick_list": fake_pick, "read_fields": fake_fields})
    flow = [{"search": {"url": "http://s/search?q={q}"}}, {"pick_list": {"region": "结果", "as": "结果"}},
            {"open": {"from": "结果", "n": 2, "count": 3, "flow": [{"read_fields": {"names": ["x"], "as": "详情"}}]}}]
    ctx = make_ctx(tmp_path, results_site(), flow, budget=3)
    run = await run_node(ctx)
    assert [i.text for i in run.lists["详情"]] == ["详情 r11", "详情 r12"]      # 次数只够打开 2 个
    rec = run.blocks[2]
    assert rec["cost"] == 2 and rec["note"] == "次数用满" and run.stop == "budget"
    assert ctx.tree.used == 3


async def test_page_next_and_none(tmp_path, monkeypatch):
    install(monkeypatch)
    ctx = make_ctx(tmp_path, results_site(pages=2), [{"search": {"url": "http://s/search?q={q}"}},
                                                     {"page": {"next": True}}, {"page": {"next": True}}])
    run = await run_node(ctx)
    assert run.blocks[1]["clicked"] == "下一页" and ctx.page.cur.endswith("&p=2")
    assert run.blocks[2]["note"] == "没有下一页" and run.blocks[2]["cost"] == 0
    assert ctx.tree.used == 2
