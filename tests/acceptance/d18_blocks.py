"""d18（AC3）：仿搜索夹具 + 替身 4b，逐项检查网页积木与 4b 选编号积木的行为。三种驱动各跑一遍。

搜索词按模板拼对（含附加参数）；滚动看完后能抽到视口外的「相关搜索」；抽列表的每条文字在页面上逐字存在、
同一节点不重复；替身给出不存在的编号被拒并记录；打开第 N 个后执行子积木串并返回；读详情字段缺失项记"未找到"；
翻页后积木在第 2 页执行。

python tests/acceptance/d18_blocks.py [--driver cdp|playwright|desktop]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import dcommon as dc  # noqa: E402
from hands.pagestate import build_page_state  # noqa: E402
from hands.recorder import RunRecorder  # noqa: E402
from hands.taskbook import book as B  # noqa: E402
from hands.taskbook.runner import TaskbookRunner  # noqa: E402

BOOK = """format: taskbook/1
name: d18_blocks
title: 积木行为验收
params:
  - {name: BASE, type: text, default: "http://127.0.0.1:8771"}
  - {name: lang, type: text, default: zh-CN}
groups:
  - optimus: [megatron]
start: optimus
limits: {budget: 3, keep_tabs: 0, trial_budget: 3}
flow:
  - search: {url: "{BASE}/f22_search/search.html?q={q}", query: "{word}", extra: {hl: "{lang}", src: "{key}x"}}
  - scroll_all: {mode: bottom_then_top}
  - pick_list: {region: 相关搜索, anchor: [相关搜索], max: 12, as: 相关}
  - pick_list: {region: 搜索结果的标题和摘要, max: 10, as: 结果}
  - open:
      from: 结果
      n: 2
      back: true
      flow:
        - read_fields: {names: [标题, 价位], name_field: 标题, as: 详情}
  - page: {next: true}
  - pick_list: {region: 搜索结果的标题和摘要, max: 10, as: 第二页}
output: {type: leaves}
"""
BAD_NO = 999


async def main(driver: str) -> int:
    srv = dc.start_fixtures()
    if driver == "desktop":
        dc.no_touch()
    b, cfg = await dc.browser_for(driver)
    checks: list[tuple[str, bool, str]] = []
    rec = None
    try:
        book = B.bind(B.parse(BOOK), {"BASE": dc.BASE})
        stub = dc.BookStub(bad_no=BAD_NO)
        rec = RunRecorder(cfg.runs_dir, f"d18-{driver}")
        r = TaskbookRunner(b, cfg, rec, book, client=dc.book_client(stub), trial=True, log=lambda *a: None)
        await r.run()
        node = sorted((r.dir / "nodes").iterdir())[0]
        d = json.loads((node / "blocks.json").read_text(encoding="utf-8"))
        blk = {x["step"]: x for x in d["blocks"]}
        s = blk["flow[0]"]
        want = f"{dc.BASE}/f22_search/search.html?q=optimus&hl=zh-CN&src=x"
        checks.append(("搜索网址按模板拼对（含附加参数）", s.get("url") == want, s.get("url", "")))
        rel = [o["text"] for o in blk["flow[2]"].get("out", [])]
        checks.append(("滚动看完后抽到视口外的相关搜索", rel == ["megatron", "擎天柱", "starscream", "Optimus Prime"],
                       json.dumps(rel, ensure_ascii=False)))
        rej = [x for x in blk["flow[2]"].get("rejected", []) + blk["flow[3]"].get("rejected", [])
               if x.get("why") == "编号不存在"]
        checks.append(("替身给出不存在的编号被拒并记录", any(x.get("no") == BAD_NO for x in rej), f"{len(rej)} 条"))
        res = [o["text"] for o in blk["flow[3]"].get("out", [])]
        checks.append(("同一节点按原文去重", len(res) == len(set(res)) and len(rel) == len(set(rel)), f"{len(res)} 条结果"))
        # 逐字存在：回到每张清单的来源页面核对
        missing = []
        for step in ("flow[2]", "flow[3]", "flow[6]"):
            outs = blk[step].get("out", [])
            if not outs:
                continue
            await b.page.navigate(outs[0]["url"])
            st = await build_page_state(b.page)
            hay = st.body_text + "\n" + "\n".join(f"{e.name}\n{e.text}" for e in st.elements)
            missing += [o["text"] for o in outs if o["text"] not in hay]
        checks.append(("抽列表的每条文字在页面上逐字存在", not missing and bool(res), json.dumps(missing, ensure_ascii=False)))
        op = blk["flow[4]"]
        sub = (op.get("opened") or [{}])[0]
        rf = blk.get("flow[4].open.flow[0]", {})
        checks.append(("打开第 N 个后执行子积木串并返回", sub.get("target") == res[1] if len(res) > 1 else False
                       and sub.get("back") is True and bool(rf.get("out")),
                       f"{sub.get('target')} back={sub.get('back')} 子流程产出 {len(rf.get('out', []))}"))
        checks.append(("打开后返回原页", sub.get("back") is True, str(sub.get("back"))))
        checks.append(("读详情字段缺失项记“未找到”", rf.get("missing") == ["价位"] and "未找到" in (rf.get("note") or ""),
                       str(rf.get("missing"))))
        pg = blk["flow[5]"]
        p2 = [o["text"] for o in blk["flow[6]"].get("out", [])]
        checks.append(("翻页后积木在第 2 页执行", pg.get("clicked") == "下一页" and "p=2" in (pg.get("page_url") or "")
                       and bool(p2) and all("第 2 页" in t for t in p2), json.dumps(p2[:3], ensure_ascii=False)))
        checks.append(("次数：搜索 + 打开 + 翻页 = 3", r.tree.used == 3, str(r.tree.used)))
    finally:
        await dc.close_agent_window(b)
        srv.stop()
    for name, ok, detail in checks:
        print(f"{'通过' if ok else '失败'}  {name}  {detail}")
    if rec is not None:
        print(f"运行目录：{rec.root}")
    return 0 if checks and all(c[1] for c in checks) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--driver", default="cdp")
    sys.exit(asyncio.run(main(ap.parse_args().driver)))
