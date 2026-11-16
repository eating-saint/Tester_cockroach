"""d17（AC17）：仿点评夹具 + 替身模型跑点餐定义，结果排序与标准答案一致；无依据项被排除。

python tests/acceptance/d17_dinner.py [--driver cdp|desktop]
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

DATA = json.loads((ROOT / "fixtures" / "f23_dinner" / "data.json").read_text(encoding="utf-8"))


def script() -> dc.ExpandScript:
    cands, judge, fields = {}, {}, {}
    for c, rows in DATA["cuisines"].items():
        names = [r["name"] for r in rows]
        cands[c] = names
        ops = [{"op": "new_key", "from": 1, "condition": "c1", "reason": "便宜"}]
        ops += [{"op": "add_value", "key": c, "from": i, "condition": "c1", "reason": "便宜"}
                for i in range(2, len(names) + 1)]
        judge[c] = ops
        for r in rows:
            fields[r["name"]] = [("店名", r["name"]), ("评分", f"评分：{r['rating']}")]
            if r["price"]:
                fields[r["name"]].append(("价位", f"价位：{r['price']}"))
    # 故意编一家页面上不存在的店：判定引用它会被拒（无依据）
    cands["日本寿司"] = cands["日本寿司"] + ["编出来的寿司店"]
    judge["日本寿司"].append({"op": "add_value", "key": "日本寿司", "from": len(cands["日本寿司"]), "reason": "编的"})
    return dc.ExpandScript(cands, judge, fields)


async def main(driver: str) -> int:
    srv = dc.start_fixtures()
    if driver == "desktop":
        dc.no_touch()
    b, cfg = await dc.browser_for(driver)
    checks = []
    try:
        tt = load_task_type(ROOT / "tasks" / "fixture_dinner.yaml", {"BASE": dc.BASE, "n": 2})
        rec = RunRecorder(cfg.runs_dir, f"d17-{driver}")
        r = ExpandRunner(b, cfg, rec, tt, client=dc.expand_client(script()), log=lambda *a: None)
        res = await r.run()
        top = res["rank"]["top"]
        for c, want in DATA["answer"].items():
            if c == "rule":
                continue
            got = [x["name"] for x in top.get(c, [])]
            checks.append((f"C17.2 {c} 前 2 与标准答案一致", got == want, f"{got} / 期望 {want}"))
        fake = [n.word for n in r.tree.nodes.values() if n.word == "编出来的寿司店"]
        rejected = any(x.get("op") == "rejected" and "无依据" in str(x.get("reason"))
                       for n in r.tree.nodes.values() for x in n.reasons)
        checks.append(("C14.1 / C17.4 编出来的店被拒、不进树", not fake and rejected, f"树里={fake} 记录拒绝={rejected}"))
        unknown = [x for g in top.values() for x in g if not x.get("price_known")]
        checks.append(("C17.3 价位写法统一比较（前 n 里没有价位未知）", not unknown, str(unknown)))
        checks.append(("任务结束且用量在上限内", res["state"] == "done" and r.tree.used <= tt.budget,
                       str(r.tree.counts())))
    finally:
        await dc.close_agent_window(b)
        srv.stop()
    for name, ok, detail in checks:
        print(f"{'通过' if ok else '失败'}  {name}  {detail}")
    print(f"运行目录：{rec.root}")
    return 0 if all(c[1] for c in checks) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--driver", default="cdp")
    sys.exit(asyncio.run(main(ap.parse_args().driver)))
