"""d12（AC12）/ d13（AC13）/ d26（AC26）：仿搜索夹具 + 替身模型复现变形金刚例子。

python tests/acceptance/d12_expand.py [--driver cdp|desktop] [--keep-tabs N]
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
from hands.expand.run import ExpandRunner  # noqa: E402
from hands.expand.spec import load_task_type  # noqa: E402
from hands.recorder import RunRecorder  # noqa: E402

EXPECT = ["optimus", "optimus/galvatron", "optimus/megatron", "megatron/starscream", "megatron/shockwave",
          "megatron/soundwave", "optimus/starscream", "movie", "movie/fallen", "movie/decepticon", "movie/autobot",
          "decepticon/megatron", "megatron/starscream", "megatron/shockwave", "megatron/soundwave"]


async def main(driver: str, keep_tabs: int | None) -> int:
    srv = dc.start_fixtures()
    if driver == "desktop":
        dc.no_touch()
    b, cfg = await dc.browser_for(driver)
    checks: list[tuple[str, bool, str]] = []
    try:
        tt = load_task_type(ROOT / "tasks" / "fixture_transformers.yaml", {"BASE": dc.BASE})
        if keep_tabs is not None:
            tt.keep_tabs = keep_tabs
        script = dc.ExpandScript({"decepticon": ["megatron"]},
                                 {"decepticon": [{"op": "new_key", "from": 1, "condition": "c1",
                                                  "reason": "第一页 80% 提到 megatron"}]})
        rec = RunRecorder(cfg.runs_dir, f"d12-{driver}")
        r = ExpandRunner(b, cfg, rec, tt, client=dc.expand_client(script), log=lambda *a: None)
        res = await r.run()
        t = r.tree
        order = []
        for e in t.events:
            if e.op == "search":
                k = t.key_of(e.node)
                order.append(f"{k}/{t.nodes[e.node].word}" if k else t.nodes[e.node].word)
        checks.append(("C12.1-4 搜索顺序与例子一致", order == EXPECT, json.dumps(order, ensure_ascii=False)))
        checks.append(("C12.4 建出 decepticon <megatron>", t.mapping.get("decepticon") == ["megatron"],
                       str(t.mapping.get("decepticon"))))
        stars = [n for n in t.nodes.values() if n.word == "starscream"]
        checks.append(("C12.2 starscream 各处各搜一次", len(stars) == 3 and all(n.searches == 1 for n in stars),
                       f"{len(stars)} 个"))
        checks.append(("C12.6 全部末端叶子结束", t.finished and res["state"] == "done", str(t.counts())))
        snaps = sorted((rec.root / "expand" / "snapshots").glob("*.json"))
        checks.append(("C12.6 树的演变有逐步快照", len(snaps) >= len(EXPECT), f"{len(snaps)} 份"))
        # d13 标签页
        page = b.page
        kept = [n for n in t.nodes.values() if n.tab is not None and n.state == "terminal"]
        bad = [n for n in t.nodes.values() if n.tab is not None and n.state in ("key", "deleted")]
        checks.append(("C13.1 末端叶子保留标签页、键没有", bool(kept) and not bad,
                       f"保留 {len(kept)}，键/删除仍挂标签 {len(bad)}"))
        limit = tt.keep_tabs
        checks.append((f"C13.2 保留不超过上限 {limit}", len(kept) <= limit, str(len(kept))))
        no_url = [n.word for n in t.nodes.values() if n.state != "deleted" and n.searches and not (n.url and n.shot)]
        checks.append(("C13.3 每个搜过的页面有网址和截图", not no_url, str(no_url)))
    finally:
        await dc.close_agent_window(b)
        srv.stop()
    ok = all(c[1] for c in checks)
    for name, passed, detail in checks:
        print(f"{'通过' if passed else '失败'}  {name}  {detail}")
    print(f"运行目录：{rec.root}")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--driver", default="cdp")
    ap.add_argument("--keep-tabs", type=int, default=None)
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.driver, a.keep_tabs)))
