"""T14 M9 步数上限：永远达不成的规则 → 达到步数上限后强制 fail，程序不卡死。"""
import asyncio
import json


async def run(ctx):
    try:
        res, rec, _ = await asyncio.wait_for(ctx.run_case("t14_unreachable.yaml"), 60)
    except asyncio.TimeoutError:
        return False, "60 秒内未结束（卡死）"
    if not res.passed:
        return False, res.reason
    events = json.loads((rec.case_dir("t14_unreachable") / "tree_events.json").read_text(encoding="utf-8"))["events"]
    last = events[-1]
    if last["kind"] != "force_fail" or last["payload"].get("reason") != "step_limit":
        return False, f"最后事件 {last}"
    return True, f"{res.steps} 步后 force_fail(step_limit)，耗时 {res.duration_ms}ms"
