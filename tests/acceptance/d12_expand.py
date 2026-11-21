"""d12（AC4、AC16）：仿搜索夹具 + 替身 4b（只选编号）跑变形金刚积木任务书。

- 树的演变与上一章 AC12 的 15 步预期逐步一致；夹具里的别名「擎天柱」「Optimus Prime」经别名表归一后不进树；
  运行中没有任何 4b 判定改树的调用（只有选编号）；末端叶子保留标签页、键没有。
- --replay <运行目录>：用该次记录里的任务书副本和参数、同一替身再跑一遍，最后的树应与原运行相同（AC16）。

python tests/acceptance/d12_expand.py [--driver cdp|playwright|desktop] [--keep-tabs N] [--replay 运行目录]
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
from hands.recorder import RunRecorder  # noqa: E402
from hands.taskbook import book as B  # noqa: E402
from hands.taskbook.runner import TaskbookRunner  # noqa: E402

EXPECT = ["optimus", "optimus/galvatron", "optimus/megatron", "megatron/starscream", "megatron/shockwave",
          "megatron/soundwave", "optimus/starscream", "movie", "movie/fallen", "movie/decepticon", "movie/autobot",
          "decepticon/megatron", "megatron/starscream", "megatron/shockwave", "megatron/soundwave"]


def tree_shape(snap: dict) -> dict:
    nodes = sorted((n["word"], n["parent"] and next(x["word"] for x in snap["nodes"] if x["id"] == n["parent"]),
                    n["state"]) for n in snap["nodes"])
    return {"nodes": nodes, "mapping": snap["mapping"]}


def last_snapshot(run_root: Path) -> dict:
    snaps = sorted((run_root / "book" / "snapshots").glob("*.json"))
    return json.loads(snaps[-1].read_text(encoding="utf-8"))


async def main(driver: str, keep_tabs: int | None, replay: str | None) -> int:
    srv = dc.start_fixtures()
    if driver == "desktop":
        dc.no_touch()
    b, cfg = await dc.browser_for(driver)
    checks: list[tuple[str, bool, str]] = []
    rec = None
    try:
        if replay:
            src = Path(replay)
            text = (src / "book" / "taskbook.yaml").read_text(encoding="utf-8")
            values = json.loads((src / "book" / "params.json").read_text(encoding="utf-8"))["values"]
            book = B.bind(B.parse(text, "replay"), {**values, "BASE": dc.BASE})
        else:
            book = B.bind(B.load(ROOT / "tasks" / "fixture_transformers.yaml"), {"BASE": dc.BASE})
        if keep_tabs is not None:
            book.limits["keep_tabs"] = keep_tabs
        stub = dc.BookStub()
        rec = RunRecorder(cfg.runs_dir, f"d12-{driver}" + ("-replay" if replay else ""))
        r = TaskbookRunner(b, cfg, rec, book, client=dc.book_client(stub), log=lambda *a: None)
        res = await r.run()
        t = r.tree
        if replay:
            same = tree_shape(last_snapshot(Path(replay))) == tree_shape(last_snapshot(rec.root))
            checks.append(("AC16 用记录里的任务书副本与参数重跑，树相同", same, f"原 {replay} / 新 {rec.root.name}"))
        order = dc.search_order(t)
        checks.append(("C21 搜索顺序与上一章 AC12 一致", order == EXPECT, json.dumps(order, ensure_ascii=False)))
        checks.append(("C21 建出 decepticon <megatron>", t.mapping.get("decepticon") == ["megatron"],
                       str(t.mapping.get("decepticon"))))
        tree_text = json.dumps(t.mapping, ensure_ascii=False) + " ".join(n.word for n in t.nodes.values())
        checks.append(("C21 别名「擎天柱」「Optimus Prime」没有进树", "擎天柱" not in tree_text and "Optimus Prime" not in tree_text,
                       ""))
        first = json.loads(sorted((r.dir / "nodes").iterdir())[0].joinpath("blocks.json").read_text(encoding="utf-8"))
        flt = next(x for x in first["blocks"] if x["block"] == "filter")
        why = {d["text"]: d["reason"] for d in flt.get("dropped", [])}
        checks.append(("C21 别名经别名表归一后作为当前词滤掉（记录有原因）",
                       why.get("擎天柱") == "别名归一后是当前词" and why.get("Optimus Prime") == "别名归一后是当前词",
                       json.dumps(why, ensure_ascii=False)))
        checks.append(("C21 没有 4b 判定改树的调用（只有选编号）", set(stub.calls) <= {"pick_list", "pick_fields"},
                       str(sorted(set(stub.calls)))))
        stars = [n for n in t.nodes.values() if n.word == "starscream"]
        checks.append(("starscream 各处各查一次", len(stars) == 3 and all(n.searches == 1 for n in stars),
                       f"{len(stars)} 个"))
        checks.append(("全部节点处理完、任务结束", t.finished and res["state"] == "done", str(t.counts())))
        snaps = sorted((r.dir / "snapshots").glob("*.json"))
        checks.append(("树的演变有逐步快照", len(snaps) >= len(EXPECT), f"{len(snaps)} 份"))
        # 标签页：末端叶子保留、键没有、不超过上限（驱动支持标签页时）
        can_tab = all(hasattr(b.page, m) for m in ("new_tab", "close_tab"))
        kept = [n for n in t.nodes.values() if n.tab is not None and n.state == "terminal"]
        bad = [n for n in t.nodes.values() if n.tab is not None and n.state in ("key", "deleted")]
        limit = book.limits["keep_tabs"]
        if can_tab and limit:
            checks.append(("末端叶子保留标签页、键没有", bool(kept) and not bad, f"保留 {len(kept)}，键/删除仍挂标签 {len(bad)}"))
        checks.append((f"保留不超过上限 {limit}", len(kept) <= limit, str(len(kept))))
        no_url = [n.word for n in t.nodes.values() if n.state != "deleted" and n.searches and not (n.url and n.shot)]
        checks.append(("每个处理过的节点有网址和截图", not no_url, str(no_url)))
    finally:
        await dc.close_agent_window(b)
        srv.stop()
    ok = all(c[1] for c in checks)
    for name, passed, detail in checks:
        print(f"{'通过' if passed else '失败'}  {name}  {detail}")
    if rec is not None:
        print(f"运行目录：{rec.root}")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--driver", default="cdp")
    ap.add_argument("--keep-tabs", type=int, default=None)
    ap.add_argument("--replay", default=None, help="用这次运行目录里的任务书副本与参数重跑，比较最后的树")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.driver, a.keep_tabs, a.replay)))
