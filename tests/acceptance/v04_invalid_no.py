"""V04（AC4，F4）：替身模型给出不存在的编号、上一页才有的编号 → 判不合法并带原因重问，第 3 次给对后成功；
3 次都不合法 → 该步记为决策失败，程序正常结束。"""
import json
import tempfile
from pathlib import Path

from hands.decision import DecisionInput, decide_with_retry
from hands.pagestate import build_page_state
from hands.tasktree import TaskTree

from vcommon import DESC_OK, run_case, say, scripted, steps, jload

NEEDS_MODEL = False


async def run(ctx):
    page = await ctx.page()
    await page.navigate(ctx.url("f17_shop/index.html"))
    tmp = Path(tempfile.mkdtemp(prefix="v04_"))
    st = await build_page_state(page, tmp)
    from hands.vlm.digest import PageDigest
    from hands.config import vlm_settings
    n = len(PageDigest.build(st, (tmp / st.screenshot).read_bytes(), {}, "", vlm_settings(ctx.config)).numbered)
    factory, client = scripted(ctx.config, [DESC_OK, say("click", element=n + 30),      # 本页没有的编号
                                            say("click", element=n + 5),               # 上一页才有的编号（超出本页清单）
                                            say("click", element=4)])                   # 本页的搜索框
    dec = factory(None)
    d, raws, tries = await decide_with_retry(dec, DecisionInput(st, TaskTree("g").view(), step_dir=tmp))
    if d is None or tries != 3:
        return False, f"期望第 3 次成功，实际 tries={tries} decision={d}"
    u2, u3 = client.calls[2]["user"], client.calls[3]["user"]
    if f"编号 {n + 30} 不在本页的元素清单里" not in u2 or f"编号 {n + 5} 不在本页的元素清单里" not in u3:
        return False, "重问时没有把不合法原因带给模型"
    if not (tmp / "vlm" / "request_try2.json").exists() or not (tmp / "vlm" / "request_try3.json").exists():
        return False, "重问的请求没有分别存档"
    note = f"编号 {n + 30}、{n + 5} 被判不合法并带原因重问，第 3 次（编号 4）成功"
    # 3 次都不合法：用例里该步决策失败，程序正常结束
    factory, _ = scripted(ctx.config, [DESC_OK] + [say("click", element=999)] * 3)
    res, rec, cdir = await run_case(ctx, "v03_inject.yaml", factory=factory, label="v04-allbad")
    tree = jload(cdir / "tree_events.json")
    if res.passed or not any("invalid decision" in json.dumps(e, ensure_ascii=False) for e in tree["events"]):
        return False, f"3 次都不合法时应以 invalid decision 失败，实际 {res.reason}"
    st1 = jload(steps(cdir)[0] / "decision.json")
    if st1["decision"] is not None or len(st1["raw"]) != 3:
        return False, "决策失败那一步的记录不对"
    return True, note + "；3 次都不合法时该步记为决策失败，用例正常结束"
