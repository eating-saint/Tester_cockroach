"""T13 M8+M9 规则用例（F10 两个版本）。"""
import json

from common import load_expected


async def run(ctx):
    kws = load_expected("f10_search")["keywords"]
    yes, rec, _ = await ctx.run_case("t13_reddit_yes.yaml")
    if not yes.passed:
        return False, f"有 reddit 版本失败: {yes.reason}"
    events = json.loads((rec.case_dir("t13_reddit_yes") / "tree_events.json").read_text(encoding="utf-8"))["events"]
    seq = [(e["kind"], e["node_id"]) for e in events]
    want = [("create", "root"), ("create", "n1"), ("complete", "n1"), ("complete", "root")]
    if seq != want:
        return False, f"任务树事件序列 {seq} ≠ {want}"
    child_summary = events[2]["payload"]["summary"]
    if not all(k in child_summary for k in kws):
        return False, f"子任务摘要缺少评论：{child_summary!r}"
    no, _, _ = await ctx.run_case("t13_reddit_no.yaml")
    if not no.passed:
        return False, f"无 reddit 版本失败: {no.reason}"
    return True, f"有 reddit：{seq}，5 条评论全部读到；无 reddit：{no.root_summary}，无点击"
