"""V07（AC7，F7）：默认规则决策；配置文件和命令行都能切到大模型，命令行优先；面板能切换，任务运行中切换被拒。"""
import tempfile
from pathlib import Path

from hands.config import PROJECT_ROOT, load_config

NEEDS_MODEL = False


async def run(ctx):
    notes = []
    tmp = Path(tempfile.mkdtemp(prefix="v07_"))
    (tmp / "a.yaml").write_text("headless: true\n", encoding="utf-8")
    (tmp / "b.yaml").write_text("decision: vlm\n", encoding="utf-8")
    if load_config(tmp / "a.yaml").decision != "rule":
        return False, "默认决策方式不是规则决策"
    if load_config(tmp / "b.yaml").decision != "vlm":
        return False, "配置文件写 vlm 没有生效"
    if load_config(tmp / "b.yaml", decision="rule").decision != "rule":
        return False, "命令行没有优先于配置文件"
    notes.append("默认 rule；配置 vlm 生效；命令行优先")
    # 面板：能选视觉大模型；任务运行中切换被拒
    await ctx.close_browser()                     # 面板要用同一个用户目录
    import importlib.util                         # tests/panel/common.py 与验收的 common 同名，按文件路径单独加载
    spec = importlib.util.spec_from_file_location("panel_common", PROJECT_ROOT / "tests" / "panel" / "common.py")
    pc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pc)
    p = pc.PanelCtx()
    await p.start()
    try:
        info = await p.get("/api/info")
        spec_d = next(s for s in info["data"]["specs"] if s["path"] == "decision")
        if "vlm" in (spec_d.get("disabled_choices") or []):
            return False, "面板里视觉大模型仍是不可选"
        await p.req("PUT", "/api/settings", {"decision": "vlm"})
        await p.req("PUT", "/api/settings", {"decision": "rule"})
        await p.start_job("manual")
        await p.wait_state(lambda s: s["busy"], 30)
        status, data = await p.req("PUT", "/api/settings", {"decision": "vlm"}, expect_ok=False)
        await p.post("/api/jobs/stop")
        await p.wait_job(30)
        if status != 409 or "不能切换决策方式" not in str(data):
            return False, f"任务运行中切换应被拒，实际 {status} {data}"
        st = await p.get("/api/vlm/status")
        notes.append(f"面板可选视觉大模型；手动任务运行中切换被拒（409）；模型状态：{st['data']['message']}")
    finally:
        await p.close()
    return True, "；".join(notes)
