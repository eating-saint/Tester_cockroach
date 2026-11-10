"""T02 M1 崩溃恢复：运行中强制结束 Chrome → 5 秒内检测到 → 自动重启 → 当前用例从头重跑并通过。"""
import json
import time

from hands.browser import kill_tree


async def run(ctx):
    b = await ctx.get_browser()
    state = {"kill_time": None, "history_len": len(b.crash_history)}

    def on_step(info):
        if info["attempt"] == 1 and info["index"] == 2 and state["kill_time"] is None:
            state["kill_time"] = time.time()
            kill_tree(info["browser"].pid)

    result, rec, report = await ctx.run_case("t02_crash.yaml", on_step=on_step)
    if state["kill_time"] is None:
        return False, "未触发强制结束"
    new = b.crash_history[state["history_len"]:]
    if not new:
        return False, "未检测到崩溃"
    delay = new[0][0] - state["kill_time"]
    if delay >= 5:
        return False, f"崩溃检测耗时 {delay:.2f}s ≥ 5s"
    if not result.passed:
        return False, f"重跑后用例未通过: {result.reason}"
    if result.crashes != 1:
        return False, f"崩溃次数 {result.crashes} ≠ 1"
    rep = json.loads((rec.root / "report.json").read_text(encoding="utf-8"))
    if rep["results"][0]["crashes"] != 1:
        return False, "报告中崩溃次数不为 1"
    return True, f"检测耗时 {delay:.2f}s，重跑通过，报告 crashes=1（{new[0][1]}）"
