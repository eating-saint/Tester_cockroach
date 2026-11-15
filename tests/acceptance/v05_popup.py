"""V05（AC5，F5）：首页有弹窗遮挡，大模型先关闭弹窗，再完成任务。"""
from vcommon import action_of, decision_of, run_case, steps

NEEDS_MODEL = True


async def run(ctx):
    res, rec, cdir = await run_case(ctx, "v02_popup.yaml", label="v05")
    sts = steps(cdir)
    first = None
    for s in sts:
        d = decision_of(s)
        if d.get("tool") in ("click", "type", "press", "scroll", "open_tab"):
            first = (d, action_of(s).get("target") or {})
            break
    if first is None:
        return False, "没有任何页面动作"
    d, tgt = first
    closed = (d["tool"] == "click" and tgt.get("name") in ("关闭", "×")) or (d["tool"] == "press" and
                                                                             d["arguments"].get("key") == "Escape")
    if not closed:
        return False, f"第一个页面动作不是关闭弹窗：{d['tool']} {tgt.get('name')}"
    if not res.passed:
        return False, f"关闭弹窗后任务没有完成：{res.reason}"
    return True, f"第一步关闭弹窗（{d['tool']} {tgt.get('name') or d['arguments']}），共 {res.steps} 步完成"
