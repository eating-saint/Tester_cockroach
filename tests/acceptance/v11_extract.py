"""V11（AC11，F11）：仿电商详情页抽取的标题、价格、评分与标准答案一致且有依据；
页面上不存在的值标"无依据"，不算有效值，用例判失败并写明原因。"""
from common import load_expected

from vcommon import DESC_OK, run_case, say, scripted

NEEDS_MODEL = True


async def run(ctx):
    exp = load_expected("f17_shop")["items"]["3"]
    res, rec, cdir = await run_case(ctx, "v01_shop_extract.yaml", label="v11")
    got = {}
    for r in res.extractions:
        if r["grounded"]:
            got[r["field"]] = r["value"]
    wrong = {k: (got.get(k), exp[k]) for k in ("标题", "价格", "评分") if got.get(k, "").strip() != exp[k]}
    if not res.passed or wrong:
        return False, f"抽取与标准答案不一致：{wrong}（用例 {'通过' if res.passed else res.reason}）"
    note = f"抽取 {got} 与标准答案一致且都有依据"
    # 替身模型给出页面上不存在的价格
    factory, _ = scripted(ctx.config, [DESC_OK, say("extract", items=[{"field": "标题", "value": "街角面馆的三十年"},
                                                                    {"field": "作者", "value": "张三"}]),
                                       say("complete", summary="结束")])
    res2, rec2, cdir2 = await run_case(ctx, "v03_inject.yaml", factory=factory, label="v11-fake")
    bad = [r for r in res2.extractions if r["field"] == "作者"]
    if not bad or bad[0]["grounded"]:
        return False, f"编造的作者没有被标为无依据：{res2.extractions}"
    if res2.passed or "作者" not in res2.reason:
        return False, f"有无依据字段时用例应失败并写明原因，实际 passed={res2.passed} reason={res2.reason}"
    html = (rec2.root / "report.html").read_text(encoding="utf-8")
    if "张三（无依据）" not in html:
        return False, "报告里没有标出无依据的字段"
    return True, note + f"；编造的作者'张三'标为无依据，用例失败：{res2.reason}"
