"""用例执行（控制层）：CHECK → ANALYZE → EXECUTE → VERIFY → NEXT/FINISH；崩溃重跑、人工介入。"""
from __future__ import annotations

import asyncio
import inspect
import json
import shutil
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import yaml

from . import captcha
from .actions import ActionExecutor, ActionResult
from .checks import MAX_HANDOFFS, HandoffChecker, respect_interval
from .cdp import BrowserCrashed
from .confirm import ConfirmPolicy, cli_confirm_fn
from .decision import TOOL_REGISTRY, DecisionInput, decide_with_retry
from .driver import AgentWindowClosed, caps_of
from .pause import PauseGate
from .humanize import HumanizeConfig, Humanizer, derive_seed, new_seed
from .pagestate import build_page_state
from .guard import Guard
from .recorder import RunRecorder, StepRecord
from .rule_decider import Rule, RuleDecider
from .status import STATUS
from .tasktree import TaskTree


REPEAT_BLOCK = 3        # 同一个动作连续第几次没有效果时，不再执行，只把原因回传给决策方
REPEAT_FAIL = 5         # 连续到第几次，判当前任务失败，不再空耗到步数上限


class RepeatWatch:
    """原地打转检测：工具 + 参数完全相同、且页面一直没有变化时计数（ver1 调试问题 2）。

    不针对某个模型——规则决策写错规则同样会卡住，所以放在控制层。
    """

    def __init__(self):
        self.sig: str | None = None
        self.n = 0

    @staticmethod
    def signature(decision) -> str | None:
        if decision is None:
            return None
        return json.dumps([decision.tool, decision.arguments], ensure_ascii=False, sort_keys=True, default=str)

    def check(self, decision) -> int:
        """这个动作是连续第几次出现（1 = 不算重复）。"""
        s = self.signature(decision)
        if s is not None and s == self.sig:
            self.n += 1
        else:
            self.sig, self.n = s, 1
        return self.n

    def note(self, effective: bool) -> None:
        if effective:                      # 页面变了就不算原地打转，重新计数
            self.sig, self.n = None, 0


@dataclass
class Expect:
    root_status: str = "done"
    summary_contains: list[str] = field(default_factory=list)
    summary_count_min: dict[str, int] = field(default_factory=dict)   # 摘要中某字符串至少出现几次
    final_url_contains: str | None = None
    tree_shape: list[str] | None = None
    no_clicks: bool = False
    extract_required: list[str] = field(default_factory=list)   # ver1：这些字段必须抽到有依据的值


@dataclass
class CaseSpec:
    name: str
    start_url: str
    root_goal: str
    root_done_when: str = ""
    inputs: dict = field(default_factory=dict)
    limits: dict = field(default_factory=lambda: {"max_depth": 3, "max_steps": 20})
    rules: list[Rule] = field(default_factory=list)
    default: Rule | None = None
    expect: Expect = field(default_factory=Expect)
    timeout_s: float = 300
    brief: dict = field(default_factory=dict)        # ver1 任务书附加信息：hints、extract_fields


@dataclass
class CaseResult:
    name: str
    passed: bool
    reason: str
    crashes: int = 0
    steps: int = 0
    tree_events_file: str | None = None
    duration_ms: int = 0
    root_summary: str | None = None
    final_url: str | None = None
    tree_shape: list[str] = field(default_factory=list)
    humanize_seed: int | None = None     # 开启鼠标轨迹/干扰项时，本用例使用的种子（用于重放）
    human_handoffs: list[dict] = field(default_factory=list)   # 人工介入：[{start, end, hit, url}]
    decision: str = "rule"                                     # ver1：本用例的决策方式
    extractions: list[dict] = field(default_factory=list)      # ver1：抽取结果（含是否有依据）
    pauses: list[dict] = field(default_factory=list)           # 桌面：暂停记录 [{start, end, reason, how}]
    confirms: list[dict] = field(default_factory=list)         # 桌面：需确认请求与回答

    def to_dict(self) -> dict:
        return asdict(self)


def load_case(path, vars: dict | None = None) -> CaseSpec:
    text = Path(path).read_text(encoding="utf-8")
    for k, v in (vars or {}).items():
        text = text.replace("${" + k + "}", str(v))
    d = yaml.safe_load(text)
    return CaseSpec(
        name=d.get("name") or Path(path).stem,
        start_url=d["start_url"],
        root_goal=d.get("root_goal", ""),
        root_done_when=d.get("root_done_when", ""),
        inputs=d.get("inputs") or {},
        limits={"max_depth": 3, "max_steps": 20, **(d.get("limits") or {})},
        rules=[Rule.from_dict(r) for r in d.get("rules") or []],
        default=Rule.from_dict(d["default"]) if d.get("default") else None,
        expect=Expect(**(d.get("expect") or {})),
        timeout_s=float(d.get("timeout_s", 300)),
        brief={k: d[k] for k in ("hints", "extract_fields") if d.get(k)},
    )


class CaseFailed(Exception):
    pass


class CaseRunner:
    def __init__(self, browser, config, recorder: RunRecorder, decider_factory=None,
                 on_step=None, input_fn=input, log=print, should_stop=None, before_action=None,
                 handoff_fn=None, humanizer_factory=None, gate=None, confirm_fn=None, budget_hook=None):
        self.browser = browser
        self.config = config
        self.recorder = recorder
        # 桌面驱动（spec F8、F9）：暂停门、需确认。CDP / Playwright 下暂停门不监听人工输入、需确认默认关
        self.caps = caps_of(browser)
        self.gate = gate or getattr(browser, "gate", None) or PauseGate(watch_human=False)
        self.confirm = ConfirmPolicy.from_config(getattr(config, "confirm", None), self.caps.confirm_default)
        self.confirm_fn = confirm_fn or cli_confirm_fn(input_fn, log)
        self.budget_hook = budget_hook                # (decision, result) -> bool；False = 停（关联展开次数上限）
        self._confirms: dict[str, list[dict]] = {}
        self._pause_log_start = 0
        # decider_factory 为 None：按 config.decision 选（rule = 规则决策；vlm = 视觉大模型，ver1）
        self.decision = getattr(config, "decision", "rule")
        if decider_factory is None:
            if self.decision == "vlm":
                from .vlm.decider import VlmDecider
                decider_factory = VlmDecider.factory(config)
            else:
                decider_factory = RuleDecider.from_case
        self.decider_factory = decider_factory
        self._extractions: dict[str, list[dict]] = {}
        self._case_name = ""
        self.on_step = on_step
        self.input_fn = input_fn
        self.log = log
        # 以下钩子都可选；不传时行为和 ver0 一样
        self.should_stop = should_stop               # () -> bool：每步开头检查，为真则以 user_stopped 结束
        self.before_action = before_action           # 传给 ActionExecutor
        self.handoff_fn = handoff_fn                 # 人工介入；默认 captcha.handoff（切有界面、等回车）
        self.humanizer_factory = humanizer_factory   # (case_name, attempt) -> Humanizer | None
        self._handoffs: dict[str, list[dict]] = {}
        self.checker = HandoffChecker(browser, config, self.confirm, self.gate, handoff_fn, input_fn, log,
                                      grace=self._grace())
        self._stopped = False
        self._current_humanizer = None
        self._repeat = RepeatWatch()                 # 每次 _attempt 开头重置
        self._last_visit: dict[str, float] = {}
        self.humanize = HumanizeConfig.from_dict(getattr(config, "humanize", None))
        if self.caps.os_input and not self.humanize.mouse.enabled:
            from .config import desktop_humanize      # 桌面驱动：真实鼠标默认平滑、放慢移动
            self.humanize = desktop_humanize(config, self.humanize)
        # 运行种子：配置里写了就用配置的，否则随机生成；各用例的种子由它和用例名派生
        self.run_seed = self.humanize.seed if self.humanize.seed is not None else new_seed()
        self._case_seed: dict[str, int] = {}

    def _humanizer(self, case_name: str, attempt: int):
        if self.humanizer_factory is not None:
            h = self.humanizer_factory(case_name, attempt)
            if h is not None:
                self._case_seed[case_name] = h.seed
            return h
        if not self.humanize.any_enabled:
            return None
        seed = derive_seed(self.run_seed, case_name, attempt)
        self._case_seed[case_name] = seed
        return Humanizer(self.humanize, seed)

    # ---------------- 礼貌间隔 ----------------
    async def _respect_interval(self, url: str):
        await respect_interval(self._last_visit, url, self.config.min_interval_s, self.log)

    async def _page(self):
        if self.browser.page is None:
            await self.browser.new_page()
        return self.browser.page

    # ---------------- 单次执行 ----------------
    async def _attempt(self, case: CaseSpec, attempt: int) -> tuple[TaskTree, int, int]:
        page = await self._page()
        fresh = getattr(page, "fresh_start", None)      # 用例开始：驱动可借机回收资源（CDP 驱动什么都不做）
        if fresh is not None:
            await fresh()
        await self._respect_interval(case.start_url)
        from .pause import Interrupted
        for _ in range(5):                       # 开头导航被暂停打断：等恢复后重来（桌面驱动）
            try:
                await page.navigate(case.start_url)
                break
            except Interrupted:
                await self.gate.wait()
        else:
            await page.navigate(case.start_url)
        tree = TaskTree(case.root_goal, case.root_done_when, case.limits["max_depth"], case.limits["max_steps"])
        decider = self.decider_factory(case)
        humanizer = self._humanizer(case.name, attempt)
        self._current_humanizer = humanizer

        guard = Guard.from_config(getattr(self.config, "guard", None))

        def make_executor(p):
            return ActionExecutor(p, humanizer, capture_trajectory=humanizer is not None,
                                  before_action=self.before_action, guard=guard,
                                  confirm=self.confirm, confirm_fn=self.confirm_fn, gate=self.gate)
        executor = make_executor(page)
        self._repeat = RepeatWatch()
        deadline = time.monotonic() + case.timeout_s
        index = 0
        clicks = 0
        handoffs = 0
        self.checker.pending = None                  # 上一步动作要求转人工（如目标是密码框）
        try:
            while not tree.finished:
                # 暂停中（人一碰鼠标键盘 / 面板暂停 / 改树）：等恢复；等待时长不计入用例时限（spec F8）
                paused_s = await self.gate.wait()
                deadline += paused_s
                if self.should_stop is not None and self.should_stop():
                    self._stopped = True
                    tree.fail("user_stopped")
                    break
                if time.monotonic() > deadline:
                    tree.fail("timeout")
                    break
                index += 1
                t0 = time.monotonic()
                sdir = self.recorder.step_dir(case.name, index)
                # CHECK
                STATUS.set_phase("CHECK")
                try:
                    state = await build_page_state(page, sdir)
                except Exception as e:
                    from .pause import Interrupted
                    if isinstance(e, Interrupted):       # 观察途中被暂停（极少见）：这一步作废，恢复后重来
                        index -= 1
                        continue
                    raise
                # 验证码 / 登录 / 上一步要求 / 系统对话框 → 转人工（逻辑在 checks.HandoffChecker，与任务书积木共用）
                hit = await self.checker.detect(state, page)
                if hit:
                    handoffs += 1
                    self.log(f"[HUMAN_REQUIRED] {case.name}: 检测到 {hit}")
                    if handoffs > MAX_HANDOFFS:
                        tree.fail(f"captcha not resolved: {hit}")
                        break
                    page, rec = await self.checker.handle(hit, state, sdir)
                    self._handoffs.setdefault(case.name, []).append(rec)
                    deadline += rec["end"] - rec["start"]      # 等人的时间不算进用例时限（F12：人工处理后继续）
                    executor = make_executor(page)
                    index -= 1
                    continue
                if self.decision == "vlm":       # ver1：大模型决策时才测遮挡（规则决策不多花时间）
                    vis = [e for e in state.elements if e.visible and e.in_viewport]
                    occ = getattr(page, "occluded", None)
                    if occ is not None and vis:
                        state.occluded = sorted(await occ(vis))
                t1 = time.monotonic()
                # ANALYZE
                STATUS.set_phase("ANALYZE")
                node_id = tree.active_id
                try:
                    decision, raws, tries = await decide_with_retry(
                        decider, DecisionInput(state, tree.view(), case.inputs, step_dir=sdir, brief=case.brief,
                                               caps=self.caps))
                except Exception as e:
                    from .vlm.client import ModelUnavailable
                    if not isinstance(e, ModelUnavailable):
                        raise
                    self.log(f"[模型不可用] {case.name}: {e}")
                    tree.fail(f"模型不可用：{e}")
                    break
                t2 = time.monotonic()
                # EXECUTE
                STATUS.set_phase("EXECUTE")
                page, clicks = await self._step_execute(case, tree, state, decision, raws, tries, executor, page,
                                                        node_id, index, attempt, clicks, t0, t1, t2)
        except AgentWindowClosed:
            # 桌面驱动：用户关掉了代理窗口。任务结束，不重跑（spec F2）
            self.log(f"[desktop] {case.name}: 代理窗口已关闭，任务结束")
            if not tree.finished:
                while not tree.finished:
                    tree.fail("代理窗口已关闭")
        finally:
            STATUS.set_phase(None)
            self._confirms.setdefault(case.name, []).extend(executor.confirms)
        return tree, index, clicks

    def _grace(self) -> float:
        from .config import desktop_settings
        return float(desktop_settings(self.config)["resume_grace_s"]) if self.caps.os_input else 0.0

    async def _step_execute(self, case, tree, state, decision, raws, tries, executor, page, node_id, index,
                            attempt, clicks, t0, t1, t2):
        if True:
            repeated = self._repeat.check(decision)
            if decision is None:
                result = ActionResult(False, False, error="invalid decision after 3 tries")
                tree.fail("invalid decision")
            elif repeated >= REPEAT_BLOCK:
                # 同一个动作连做了几次，页面一直没变：不再执行，把原因回传，逼决策方换做法
                what = f"{decision.tool} {json.dumps(decision.arguments, ensure_ascii=False, default=str)}"
                why = (f"重复动作：{what} 已经连续 {repeated} 次，页面都没有变化，"
                       f"不再执行；请换一个元素或换一种工具")
                result = ActionResult(False, False, error=why)
                self.log(f"[repeat] {case.name}: {why}")
                if repeated >= REPEAT_FAIL:
                    tree.fail(f"重复动作：{what} 连续 {repeated} 次没有效果")
            else:
                result = await self._execute(decision, state, tree, executor)
                interrupted = isinstance(result.data, dict) and result.data.get("interrupted")
                if interrupted:
                    self._repeat = RepeatWatch()        # 被打断的动作不算"原地打转"
                else:
                    self._repeat.note(result.effective)
                if isinstance(result.data, dict) and result.data.get("handoff"):
                    self.checker.set_pending(f"login:{result.data['handoff']}")
                if decision.tool in ("click", "open_tab", "right_click") and result.ok:
                    clicks += 1
                if executor.page is not page:
                    page = executor.page
                if self.budget_hook is not None and not tree.finished and not self.budget_hook(decision, result):
                    tree.fail("budget")
            t3 = time.monotonic()
            # VERIFY
            target = target_label = None
            if decision and isinstance(decision.arguments.get("element"), str):
                from .vlm.digest import stable_features, ROLE_ZH
                target = stable_features(state, decision.arguments["element"])
                if target:
                    target_label = f"{ROLE_ZH.get(target['role'], target['role'])}「{target['name'] or target['text']}」"
            step_summary = {"tool": decision.tool if decision else None,
                            "arguments": decision.arguments if decision else None,
                            "ok": result.ok, "effective": result.effective, "error": result.error,
                            "data": result.data if decision and TOOL_REGISTRY[decision.tool].kind == "read" else None,
                            "changes": list(result.changes), "target_label": target_label}
            if decision and decision.tool == "create_child":
                tree.record_step(step_summary, node_id)
            elif decision and TOOL_REGISTRY[decision.tool].kind != "tree":
                tree.record_step(step_summary, node_id)
            tree.tick_step(node_id)
            durations = {"check": int((t1 - t0) * 1000), "decide": int((t2 - t1) * 1000),
                         "execute": int((t3 - t2) * 1000), "total": int((time.monotonic() - t0) * 1000)}
            rec = StepRecord(index, node_id, "pagestate.json", state.screenshot, raws,
                             decision.to_dict() if decision else None, tries - 1, result.to_dict(), durations,
                             target=target)
            extra = None
            drain = getattr(page, "drain_injected", None)
            if drain is not None:                       # 桌面驱动：本步的元素来源、换算比例、注入事件（F17）
                src = {}
                for e in state.elements:
                    src[e.source] = src.get(e.source, 0) + 1
                fr = getattr(page, "_frame", None)
                extra = {"desktop.json": {"element_sources": src, "frame": fr.to_dict() if fr else None,
                                          "read_ms": getattr(page, "last_read_ms", None),
                                          "check_ms": durations["check"], "wait": result.wait.to_dict() if result.wait else None,
                                          "injected": drain(), "interrupted": bool(isinstance(result.data, dict)
                                                                                   and result.data.get("interrupted"))}}
            self.recorder.save_step(case.name, rec, state, trajectory_png=result.trajectory_png,
                                    dpr=state.device_pixel_ratio, extra_files=extra)
            if self.on_step is not None:
                r = self.on_step({"index": index, "tree": tree, "page": page, "browser": self.browser,
                                  "attempt": attempt, "case": case, "decision": decision,
                                  "result": result, "durations": durations})
                if inspect.isawaitable(r):
                    await r
        return page, clicks

    async def _execute(self, decision, state, tree: TaskTree, executor: ActionExecutor) -> ActionResult:
        a = decision.arguments
        tool = decision.tool
        if tool == "create_child":
            r = tree.create_child(a["goal"], a["done_when"])
            return ActionResult(r["ok"], r["ok"], data=r, error=r.get("error"))
        if tool == "complete":
            return ActionResult(True, True, data=tree.complete(a["summary"]))
        if tool == "fail":
            return ActionResult(True, True, data=tree.fail(a["reason"]))
        if tool == "extract":                      # ver1 内容抽取：回查依据，不动页面
            from .vlm.extract import verify_extract
            rows = verify_extract(a["items"], state)
            self._extractions.setdefault(self._case_name, []).extend(rows)
            bad = [r["field"] for r in rows if not r["grounded"]]
            # 有无依据的字段就算失败：模型在下一步看到"失败（无依据…）"，比"成功但有错误"更不容易误解
            return ActionResult(not bad, False, data={"extract": rows},
                                error=(f"无依据的字段：{bad}（值必须逐字出现在所指元素或正文里）" if bad else None))

        def el(eid):
            e = state.element(eid)
            if e is None:
                raise LookupError(f"unknown element {eid}")
            return e
        if tool == "show_grid":                    # 桌面：下一步的元素清单叠加网格编号
            rg = getattr(executor.page, "request_grid", None)
            if rg is None:
                return ActionResult(False, False, error="本驱动不支持网格")
            rg()
            return ActionResult(True, False, data="下一步的元素清单会加上网格编号（格 N）")
        try:
            if tool == "right_click":
                return await executor.right_click(el(a["element"]))
            if tool == "click":
                return await executor.click(el(a["element"]))
            if tool == "open_tab":
                return await executor.open_tab(el(a["element"]), bool(a.get("switch", False)))
            if tool == "type":
                return await executor.type(el(a["element"]), a["text"])
            if tool == "press":
                return await executor.press(a["key"])
            if tool == "scroll":
                return await executor.scroll(a.get("direction"), a.get("pixels"), bool(a.get("to_bottom")))
            if tool == "back":
                return await executor.back()
            if tool == "read_text":
                return await executor.read_text(el(a["element"]) if a.get("element") else None, a.get("contains"))
            if tool == "read_links":
                return await executor.read_links(a.get("href_contains"), a.get("href_excludes"),
                                                 a.get("title_min_len", 0))
        except LookupError as e:
            return ActionResult(False, False, error=str(e))
        return ActionResult(False, False, error=f"tool not executable: {tool}")

    # ---------------- 判定 ----------------
    async def _judge(self, case: CaseSpec, tree: TaskTree, clicks: int) -> tuple[bool, str, str | None]:
        ex = case.expect
        url = None
        try:
            url = await self.browser.page.url()
        except Exception:
            pass
        reasons = []
        if tree.root.status != ex.root_status:
            reasons.append(f"root_status={tree.root.status} (期望 {ex.root_status})，summary={tree.root.summary!r}")
        summaries = " ".join(n.summary or "" for n in tree.nodes.values())
        missing = [s for s in ex.summary_contains if s not in summaries]
        if missing:
            reasons.append(f"摘要缺少 {missing}")
        for s, n in ex.summary_count_min.items():
            if summaries.count(s) < n:
                reasons.append(f"摘要中 {s!r} 出现 {summaries.count(s)} 次（期望至少 {n} 次）")
        if ex.final_url_contains and (url is None or ex.final_url_contains not in url):
            reasons.append(f"final_url={url!r} 不含 {ex.final_url_contains!r}")
        if ex.tree_shape is not None and tree.shape() != ex.tree_shape:
            reasons.append(f"tree_shape={tree.shape()} (期望 {ex.tree_shape})")
        if ex.no_clicks and clicks:
            reasons.append(f"发生了 {clicks} 次点击（期望无点击）")
        if ex.extract_required:
            got = {r["field"] for r in self._extractions.get(case.name, []) if r["grounded"] and r["value"].strip()}
            miss = [f for f in ex.extract_required if f not in got]
            if miss:
                reasons.append(f"抽取缺少有依据的字段 {miss}")
        return (not reasons), "; ".join(reasons), url

    # ---------------- 对外 ----------------
    async def run_case(self, case: CaseSpec) -> CaseResult:
        start = time.monotonic()
        crashes = 0
        self._stopped = False
        self._handoffs[case.name] = []
        self._case_name = case.name
        self._confirms[case.name] = []
        pause_from = len(self.gate.log)
        self.recorder.reset_case(case.name)
        while True:
            self._extractions[case.name] = []          # 崩溃重跑时抽取结果从头记
            try:
                tree, steps, clicks = await self._attempt(case, crashes + 1)
                break
            except BrowserCrashed as e:
                crashes += 1
                self.log(f"[crash] {case.name}: 浏览器崩溃（{e}），第 {crashes} 次")
                d = self.recorder.case_dir(case.name)
                shutil.move(str(d), str(d.with_name(f"{case.name}.crash{crashes}")))
                self.recorder.reset_case(case.name)
                if crashes > self.config.max_crash_reruns:
                    res = CaseResult(case.name, False, f"crashed {crashes} times", crashes,
                                     duration_ms=int((time.monotonic() - start) * 1000))
                    self.recorder.save_case_result(case.name, res)
                    await self.browser.restart()
                    return res
                await self.browser.restart()
        self.recorder.save_tree(case.name, tree)
        passed, reason, url = await self._judge(case, tree, clicks)
        if self._stopped:
            passed, reason = False, "user_stopped"
        res = CaseResult(case.name, passed, reason, crashes, steps,
                         str(self.recorder.case_dir(case.name) / "tree_events.json"),
                         int((time.monotonic() - start) * 1000), tree.root.summary, url, tree.shape(),
                         self._case_seed.get(case.name), list(self._handoffs.get(case.name, [])),
                         self.decision, list(self._extractions.get(case.name, [])),
                         [p for p in self.gate.log[pause_from:] if p.get("reason") != "handoff"],
                         list(self._confirms.get(case.name, [])))
        self.recorder.save_case_result(case.name, res)
        return res

    async def run_cases(self, cases: list[CaseSpec]) -> list[CaseResult]:
        results = []
        for c in cases:
            try:
                results.append(await self.run_case(c))
            except Exception as e:  # 单条失败不影响后续
                traceback.print_exc()
                results.append(CaseResult(c.name, False, f"{type(e).__name__}: {e}"))
                if not self.browser.is_alive():
                    await self.browser.restart()
        self.report = self.recorder.write_report(
            results, {"run_seed": self.run_seed, **self.humanize.to_dict()} if self.humanize.any_enabled else None)
        return results
