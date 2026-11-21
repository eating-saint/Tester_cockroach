"""d17（AC5）：仿点评夹具 + 替身 4b（只选编号）跑点餐积木任务书，结果与标准答案一致。

- 每类前 2（价位升序、评分降序）与标准答案一致；"价位 ≤ $$"比较过滤正确（$$$ 的店不进树）；
  价位未知的店单独列出、排在已知之后。

python tests/acceptance/d17_dinner.py [--driver cdp|playwright|desktop]
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

DATA = json.loads((ROOT / "fixtures" / "f23_dinner" / "data.json").read_text(encoding="utf-8"))


async def main(driver: str) -> int:
    srv = dc.start_fixtures()
    if driver == "desktop":
        dc.no_touch()
    b, cfg = await dc.browser_for(driver)
    checks = []
    rec = None
    try:
        book = B.bind(B.load(ROOT / "tasks" / "fixture_dinner.yaml"), {"BASE": dc.BASE})
        rec = RunRecorder(cfg.runs_dir, f"d17-{driver}")
        r = TaskbookRunner(b, cfg, rec, book, client=dc.book_client(), log=lambda *a: None)
        res = await r.run()
        top = res["ranked"]["top"]
        for c, want in DATA["answer"].items():
            if c == "rule":
                continue
            got = [x["word"] for x in top.get(c, [])]
            checks.append((f"C22 {c} 前 2 与标准答案一致", got == want, f"{got} / 期望 {want}"))
        expensive = [r_["name"] for rs in DATA["cuisines"].values() for r_ in rs if r_["price"] == "$$$"]
        tree_words = {n.word for n in r.tree.nodes.values() if n.state != "deleted"}
        checks.append(("C22 价位 ≤ $$ 比较过滤正确（$$$ 不进树）", not (set(expensive) & tree_words), str(expensive)))
        unknown_want = {r_["name"] for rs in DATA["cuisines"].values() for r_ in rs if not r_["price"]}
        unknown_got = {x["word"] for x in res["ranked"]["unknown"]}
        checks.append(("C22 价位未知的单独列出", unknown_got == unknown_want, f"{sorted(unknown_got)}"))
        bad_order = [g for g, rows in top.items()
                     if any(not a["known"] and b_["known"] for a, b_ in zip(rows, rows[1:]))]
        checks.append(("C22 价位未知的排在已知之后", not bad_order, str(bad_order)))
        grounded = all(x["evidence"] and x["evidence"].get("url") for g in top.values() for x in g)
        checks.append(("每个结果有依据（来源步骤与网址）", grounded, ""))
        checks.append(("任务结束且用量在上限内", res["state"] == "done" and r.tree.used <= book.limits["budget"],
                       str(r.tree.counts())))
    finally:
        await dc.close_agent_window(b)
        srv.stop()
    for name, ok, detail in checks:
        print(f"{'通过' if ok else '失败'}  {name}  {detail}")
    if rec is not None:
        print(f"运行目录：{rec.root}")
    return 0 if all(c[1] for c in checks) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--driver", default="cdp")
    sys.exit(asyncio.run(main(ap.parse_args().driver)))
