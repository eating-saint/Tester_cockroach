"""V06（AC6，F6、N9）：假结账页上，规则决策、大模型决策、面板指定目标三种方式点下单按钮都被拦截，页面上没有下单；
记录写明命中原因；配置增删拦截词后行为随之变化；没有整体关闭拦截的选项。"""
import inspect
import json
import tempfile
from pathlib import Path

from hands.guard import Guard
from hands.pagestate import build_page_state
from hands.runner import CaseRunner, load_case
from hands.recorder import RunRecorder

from vcommon import DESC_OK, action_of, cfg_with, say, scripted, steps

NEEDS_MODEL = False

RULE_CASE = """name: v06_rule_place
start_url: ${BASE}/f17_shop/cart.html
root_goal: 点结账
rules:
  - when: {steps_used: 0, element: {role: button, name_contains: "%s"}}
    do: {tool: click, arguments: {element: "{{match}}"}}
  - when: {steps_used: {min: 1}}
    do: {tool: complete, arguments: {summary: 结束}}
expect: {root_status: done}
"""


async def orders(page):
    return await page.evaluate("JSON.stringify(window.__orders || [])")


async def rule_click(ctx, name, guard=None, label="v06-rule"):
    p = ctx.artifact(f"{label}.yaml")
    p.write_text(RULE_CASE % name, encoding="utf-8")
    case = load_case(p, {"BASE": ctx.base})
    rec = RunRecorder(ctx.run_root / "cases", label=label)
    b = await ctx.get_browser()
    res = await CaseRunner(b, cfg_with(ctx, "rule", guard=guard), rec, log=lambda *a: None).run_case(case)
    return res, rec.root / case.name, json.loads(await orders(b.page))


async def run(ctx):
    notes = []
    # ① 规则决策点 Place order
    res, cdir, od = await rule_click(ctx, "Place order")
    err = action_of(steps(cdir)[0])["result"]["error"] or ""
    if od or not err.startswith("guard_blocked") or "place order" not in err:
        return False, f"规则决策：期望被拦截且没有下单，实际 orders={od} error={err}"
    notes.append(f"规则决策被拦（{err}）")
    # ② 大模型决策（替身）点"继续"（文字不含下单词，表单提交到 /checkout/submit）
    b = await ctx.get_browser()
    await b.page.navigate(ctx.url("f17_shop/cart.html"))
    tmp = Path(tempfile.mkdtemp(prefix="v06_"))
    st = await build_page_state(b.page, tmp)
    from hands.vlm.digest import PageDigest
    from hands.config import vlm_settings
    dg = PageDigest.build(st, (tmp / st.screenshot).read_bytes(), {}, "", vlm_settings(ctx.config))
    no = next(n.no for n in dg.numbered if st.element(n.element_id).name == "继续")
    factory, client = scripted(ctx.config, [DESC_OK, say("click", element=no), say("complete", summary="结束")])
    case_text = "name: v06_vlm_cont\nstart_url: ${BASE}/f17_shop/cart.html\nroot_goal: 结账\nexpect: {root_status: done}\n"
    p = ctx.artifact("v06-vlm.yaml")
    p.write_text(case_text, encoding="utf-8")
    rec = RunRecorder(ctx.run_root / "cases", label="v06-vlm")
    res = await CaseRunner(b, cfg_with(ctx, "vlm"), rec, decider_factory=factory, log=lambda *a: None).run_case(
        load_case(p, {"BASE": ctx.base}))
    s1 = steps(rec.root / "v06_vlm_cont")[0]
    err = action_of(s1)["result"]["error"] or ""
    od = json.loads(await orders(b.page))
    if od or "form_action" not in err:
        return False, f"大模型决策：期望按表单地址拦截，实际 orders={od} error={err}"
    # 拦截结果回传：第二步的请求里能看到 guard_blocked
    req2 = json.loads((steps(rec.root / "v06_vlm_cont")[1] / "vlm" / "request.json").read_text(encoding="utf-8"))
    if "guard_blocked" not in req2["user"]:
        return False, "拦截结果没有回传给大模型"
    notes.append("大模型决策点'继续'按表单地址被拦，结果回传到下一步")
    # ③ 面板指定目标点击：与面板 _target 相同的构造方式（ActionExecutor + 配置里的拦截清单）
    from hands.actions import ActionExecutor
    await b.page.navigate(ctx.url("f17_shop/cart.html"))
    st = await build_page_state(b.page, tmp / "p")
    el = next(e for e in st.elements if e.name == "Place order")
    r = await ActionExecutor(b.page, guard=Guard.from_config(ctx.config.guard)).click(el)
    od = json.loads(await orders(b.page))
    if od or not (r.error or "").startswith("guard_blocked"):
        return False, f"面板指定目标：期望被拦截，实际 orders={od} error={r.error}"
    notes.append("面板指定目标被拦")
    # ④ 配置增删：去掉 place order 后，规则决策的点击不再被词拦截（夹具页会记一次"下单"）；"继续"仍被路径拦截
    res, cdir, od = await rule_click(ctx, "Place order", guard={"remove_words": ["place order"]}, label="v06-rm")
    if od != ["place"]:
        return False, f"去掉拦截词后应能点到（夹具记录 place），实际 orders={od}"
    res, cdir, od = await rule_click(ctx, "继续", guard={"remove_words": ["place order"]}, label="v06-rm2")
    if od:
        return False, f"去掉拦截词后，'继续'按钮仍应被路径拦截，实际 orders={od}"
    notes.append("去掉 place order 后该按钮不再被拦，'继续'仍按路径被拦")
    # ⑤ 没有整体关闭的开关
    params = set(inspect.signature(Guard.from_config).parameters) | set(inspect.signature(Guard).parameters)
    if {"enabled", "disable", "off"} & params or Guard.from_config({"enabled": False}).check(el) is None:
        return False, "拦截可以被整体关闭"
    notes.append("配置里没有关闭拦截的选项")
    return True, "；".join(notes)
