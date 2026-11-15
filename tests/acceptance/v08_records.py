"""V08（AC8，F8）：大模型跑一条用例后，每步记录齐全；报告里能点开标注图和模型回答。"""
from vcommon import action_of, decision_of, jload, run_case, steps

NEEDS_MODEL = True
FILES = ("clean.png", "annotated.png", "digest.json", "describe.json", "request.json", "response.json")


async def run(ctx):
    res, rec, cdir = await run_case(ctx, "v01_shop_extract.yaml", label="v08")
    sts = [s for s in steps(cdir) if (s / "decision.json").exists()]
    if not sts:
        return False, "没有步骤记录"
    with_target = 0
    for s in sts:
        miss = [f for f in FILES if not (s / "vlm" / f).exists()]
        if miss:
            return False, f"{s.name} 缺少 {miss}"
        resp = jload(s / "vlm" / "response.json")
        for k in ("raw", "elapsed_ms", "prompt_tokens", "output_tokens"):
            if k not in resp:
                return False, f"{s.name} response.json 缺 {k}"
        desc = jload(s / "vlm" / "describe.json")
        if not desc.get("reused") and not desc.get("calls"):
            return False, f"{s.name} 重新描述却没有调用记录"
        dg = jload(s / "vlm" / "digest.json")
        if not dg["numbered"] or "features" not in dg["numbered"][0]:
            return False, f"{s.name} digest.json 没有编号映射或稳定特征"
        d = decision_of(s)
        if isinstance(d.get("arguments", {}).get("element"), str):
            t = action_of(s).get("target")
            if not t or not t.get("role"):
                return False, f"{s.name} 带元素的动作缺少 target 稳定特征"
            ids = {n["element_id"] for n in dg["numbered"]}
            if d["arguments"]["element"] not in ids:
                return False, f"{s.name} 决策里的元素不在编号映射里"
            with_target += 1
    html = (rec.root / "report.html").read_text(encoding="utf-8")
    links = html.count("vlm/annotated.png"), html.count("vlm/response.json")
    if links[0] < len(sts) or links[1] < len(sts) or "决策方式：视觉大模型" not in html:
        return False, f"报告链接不全：标注图 {links[0]}、模型回答 {links[1]}，步数 {len(sts)}"
    return True, (f"{len(sts)} 步记录齐全（{with_target} 步带元素稳定特征），报告含标注图与模型回答链接；"
                  f"用例{'通过' if res.passed else '未通过：' + res.reason}；报告 {rec.root / 'report.html'}")
