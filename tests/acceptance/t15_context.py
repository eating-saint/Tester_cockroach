"""T15 M9 上下文清理：子任务结束后，母任务的上下文里只剩摘要，没有子任务的 PageState。"""
import json

PAGESTATE_KEYS = ("elements", "body_text", "monitor", "viewport")


async def run(ctx):
    seen = {}

    def on_step(info):
        tree, d = info["tree"], info["decision"]
        if d is not None and d.tool == "complete" and "n1" in tree.nodes and tree.nodes["n1"].status == "done" \
                and "checked" not in seen:
            seen["checked"] = True
            child = tree.nodes["n1"]
            root = tree.root
            view = json.dumps(tree.view().to_dict(), ensure_ascii=False)
            seen["child_context"] = child.context
            seen["summaries"] = list(root.child_summaries)
            seen["leak"] = [k for k in PAGESTATE_KEYS if f'"{k}"' in view or f'"{k}"' in json.dumps(root.context, ensure_ascii=False)]

    res, _, _ = await ctx.run_case("t13_reddit_yes.yaml", on_step=on_step)
    if "checked" not in seen:
        return False, "未观察到子任务完成"
    if seen["child_context"] is not None:
        return False, "子任务结束后 context 未清理"
    if not seen["summaries"] or "KW1" not in seen["summaries"][0]["summary"]:
        return False, f"母任务没有拿到子任务摘要: {seen['summaries']}"
    if seen["leak"]:
        return False, f"母任务上下文包含 PageState 字段 {seen['leak']}"
    return True, f"子任务 context=None；母任务只含摘要（{len(seen['summaries'])} 条），无 PageState 字段"
