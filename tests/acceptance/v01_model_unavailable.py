"""V01（AC1，F1）：模型服务没启动 / 模型没拉取时给中文提示、该步失败、不改用规则决策；正常调用时回答里没有思考内容。"""
from vcommon import jload, run_case, steps

NEEDS_MODEL = True


async def run(ctx):
    notes = []
    # ① 服务没启动：指向无人监听的端口
    res, rec, cdir = await run_case(ctx, "v03_inject.yaml", vlm={"base_url": "http://127.0.0.1:9"}, label="v01-down")
    if res.passed or "模型服务没有启动" not in (res.root_summary or "") + res.reason:
        tree = jload(cdir / "tree_events.json")
        if not any("模型服务没有启动" in str(e) for e in tree["events"]):
            return False, f"服务没启动：期望失败且提示'模型服务没有启动'，实际 passed={res.passed} reason={res.reason}"
    decided = [s.name for s in steps(cdir) if (s / "decision.json").exists()]
    if decided:
        return False, f"服务没启动时不应有任何决策记录（说明改用了别的决策），实际 {decided}"
    notes.append("服务没启动：失败并提示，没有决策步骤")
    # ② 模型没拉取
    res, rec, cdir = await run_case(ctx, "v03_inject.yaml", vlm={"model": "no-such-model:1b"}, label="v01-nomodel")
    tree = jload(cdir / "tree_events.json")
    if res.passed or not any("没有拉取" in str(e) for e in tree["events"]):
        return False, f"模型没拉取：期望失败且提示'没有拉取'，实际 passed={res.passed} reason={res.reason}"
    notes.append("模型没拉取：失败并提示 ollama pull")
    # ③ 正常调用：回答不含思考内容
    res, rec, cdir = await run_case(ctx, "v03_inject.yaml", label="v01-ok")
    raws = [jload(p)["raw"] for s in steps(cdir) for p in (s / "vlm").glob("response*.json")]
    if not raws:
        return False, "正常运行没有模型回答记录"
    if any("<think>" in r for r in raws):
        return False, "模型回答里出现了 <think> 思考内容"
    notes.append(f"正常调用 {len(raws)} 次回答都不含思考内容（用例 {'通过' if res.passed else '未通过'}）")
    return True, "；".join(notes)
