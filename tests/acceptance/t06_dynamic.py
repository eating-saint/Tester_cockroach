"""T06 M2 动态等待（F3）：等待结束时列表已出现；不使用固定 sleep。"""
import re

from hands.config import PROJECT_ROOT

from common import load_expected, read_elements


def static_sleep_check() -> list[str]:
    """等待/动作代码中不得出现固定 sleep；条件轮询行需标注 condition-poll。
    humanize.py 里的停顿是有意的节奏控制（轨迹点间隔、思考时间），不是"等页面"，需标注 humanize-pacing。"""
    bad = []
    for f in ("page.py", "actions.py", "monitor.py", "pagestate.py", "dom.py", "humanize.py"):
        for n, line in enumerate((PROJECT_ROOT / "src" / "hands" / f).read_text(encoding="utf-8").splitlines(), 1):
            if (re.search(r"\b(asyncio\.sleep|time\.sleep)\(", line) and "condition-poll" not in line
                    and not (f == "humanize.py" and "humanize-pacing" in line)):
                bad.append(f"{f}:{n}")
    return bad


async def run(ctx):
    exp = load_expected("f03_dynamic")
    bad = static_sleep_check()
    if bad:
        return False, f"等待代码中出现固定 sleep: {bad}"
    page = await ctx.page()
    w = await page.navigate(ctx.url("f03_dynamic/index.html"))
    els, _ = await read_elements(page)          # 不再额外等待
    links = [e for e in els if e.role == "link" and e.name.startswith("条目")]
    if w.status != "ok":
        return False, f"等待结果 {w.status}"
    if len(links) != exp["list_links"]:
        return False, f"等待返回时列表链接数 {len(links)} ≠ {exp['list_links']}"
    if w.elapsed_ms < exp["min_wait_ms"]:
        return False, f"等待仅 {w.elapsed_ms}ms，未覆盖 2s 的延迟请求"
    return True, f"等待 {w.elapsed_ms}ms 后列表已出现 {len(links)} 项；无固定 sleep"
