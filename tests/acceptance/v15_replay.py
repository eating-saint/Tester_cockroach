"""V15（AC15，N3）：从记录里抽 10 个带元素的步骤，各用存下的输入重放 10 次，总体选号一致率 ≥ 80%。"""
import sys

from hands.config import PROJECT_ROOT, vlm_settings
from hands.vlm.client import OllamaClient

from vcommon import decision_of, run_case, steps

NEEDS_MODEL = True
STEPS, N, NEED = 10, 10, 0.8


async def run(ctx):
    sys.path.insert(0, str(PROJECT_ROOT / "tools"))
    import replay_step
    # 优先用本轮验收已有的记录（如 V12），不够 10 步再跑 v01 补
    pool = []
    for cdir in sorted((ctx.run_root / "cases").glob("*/v01_shop_extract")):
        pool += [s for s in steps(cdir) if (s / "vlm" / "request.json").exists()
                 and isinstance(decision_of(s).get("arguments", {}).get("element"), str)]
    k = 0
    while len(pool) < STEPS and k < 6:
        res, rec, cdir = await run_case(ctx, "v01_shop_extract.yaml", label=f"v15-{k + 1}")
        pool += [s for s in steps(cdir) if (s / "vlm" / "request.json").exists()
                 and isinstance(decision_of(s).get("arguments", {}).get("element"), str)]
        k += 1
    if len(pool) < STEPS:
        return False, f"只找到 {len(pool)} 个带元素的步骤"
    client = OllamaClient(vlm_settings(ctx.config))
    same = total = 0
    worst = []
    for s in pool[:STEPS]:
        r = await replay_step.replay(s, N, client)
        same += r["same"]
        total += r["n"]
        worst.append(f"{s.parent.parent.name[-8:]}/{s.name} {r['original']} {r['same']}/{r['n']}")
    rate = same / total
    return rate >= NEED, f"选号一致率 {same}/{total} = {rate:.0%}（要求 ≥ {NEED:.0%}）；" + "；".join(worst)
