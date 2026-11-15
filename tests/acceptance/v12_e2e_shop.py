"""V12（AC12）：仿电商任务"搜索 spicy → 进入最相关的商品 → 抽取标题、价格、评分"，大模型连续跑 5 次至少 4 次完成。"""
from vcommon import run_case

NEEDS_MODEL = True
ROUNDS, NEED = 5, 4


async def run(ctx):
    rows = []
    for i in range(ROUNDS):
        res, rec, cdir = await run_case(ctx, "v01_shop_extract.yaml", label=f"v12-{i + 1}")
        rows.append((res.passed, res.steps, res.reason))
    ok = sum(1 for r in rows if r[0])
    detail = "，".join(f"#{i + 1} {'通过' if p else '失败'} {s} 步" + ("" if p else f"（{why}）")
                      for i, (p, s, why) in enumerate(rows))
    return ok >= NEED, f"{ok}/{ROUNDS} 次完成（要求 ≥ {NEED}）：{detail}"
