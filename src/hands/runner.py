"""用例执行（控制层）：CHECK → ANALYZE → EXECUTE → VERIFY → NEXT/FINISH；崩溃重跑、人工介入。"""
from __future__ import annotations

import asyncio
import inspect
import shutil
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import yaml

from . import captcha
from .actions import ActionExecutor, ActionResult
from .cdp import BrowserCrashed
from .decision import TOOL_REGISTRY, DecisionInput, decide_with_retry
from .humanize import HumanizeConfig, Humanizer, derive_seed, new_seed
from .pagestate import build_page_state
from .recorder import RunRecorder, StepRecord
from .rule_decider import Rule, RuleDecider
from .status import STATUS
from .tasktree import TaskTree


@dataclass
class Expect:
    root_status: str = "done"
    summary_contains: list[str] = field(default_factory=list)
    summary_count_min: dict[str, int] = field(default_factory=dict)   # 摘要中某字符串至少出现几次
    final_url_contains: str | None = None
    tree_shape: list[str] | None = None
    no_clicks: bool = False


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
    )


class CaseFailed(Exception):
    pass


class CaseRunner:
    def __init__(self, browser, config, recorder: RunRecorder, decider_factory=RuleDecider.from_case,
                 on_step=None, input_fn=input, log=print, should_stop=None, before_action=None,
                 handoff_fn=None, humanizer_factory=None):
        self.browser = browser
        self.config = config
        self.recorder = recorder
        self.decider_factory = decider_factory
        self.on_step = on_step
        self.input_fn = input_fn
        self.log = log
        # 以下钩子都可选；不传时行为和 ver0 一样
        self.should_stop = should_stop               # () -> bool：每步开头检查，为真则以 user_stopped 结束
        self.before_action = before_action           # 传给 ActionExecutor
        self.handoff_fn = handoff_fn                 # 人工介入；默认 captcha.handoff（切有界面、等回车）
        self.humanizer_factory = humanizer_factory   # (case_name, attempt) -> Humanizer | None
        self._handled: set = set()
        self._handoffs: dict[str, list[dict]] = {}
        self._stopped = False
        self._current_humanizer = None
        self._last_visit: dict[str, float] = {}
        self.humanize = HumanizeConfig.from_dict(getattr(config, "humanize", None))
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
        host = (urlparse(url).hostname or "").lower()
        for domain, gap in (self.config.min_interval_s or {}).items():
            if host == domain or host.endswith("." + domain):
                last = self._last_visit.get(domain)
                if last is not None:
                    wait = gap - (time.monotonic() - last)
                    if wait > 0:
                        self.log(f"[interval] {domain}: 距上次访问不足 {gap}s，等待 {wait:.1f}s")
                        await asyncio.sleep(wait)
                self._last_visit[domain] = time.monotonic()

    async def _page(self):
        if self.browser.page is None:
            await self.browser.new_page()
        return self.browser.page

    # ---------------- 单次执行 ----------------
    async def _attempt(self, case: CaseSpec, attempt: int) -> tuple[TaskTree, int, int]:
        page = await self._page()
        await self._respect_interval(case.start_url)
        await page.navigate(case.start_url)
        tree = TaskTree(case.root_goal, case.root_done_when, case.limits["max_depth"], case.limits["max_steps"])
        decider = self.decider_factory(case)
        humanizer = self._humanizer(case.name, attempt)
        self._current_humanizer = humanizer

        def make_executor(p):
            return ActionExecutor(p, humanizer, capture_trajectory=humanizer is not None,
                                  before_action=self.before_action)
        executor = make_executor(page)
        deadline = time.monotonic() + case.timeout_s
        index = 0
        clicks = 0
        handoffs = 0
        try:
            while not tree.finished:
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
                state = await build_page_state(page, sdir)
                hit = captcha.detect(state, self.config.captcha_markers)
                if hit and hit.startswith("iframe:") and captcha.signature(state, hit) in self._handled:
                    # 同一个验证框已经人工处理过：真实验证框（Turnstile、reCAPTCHA）通过后 iframe 还留在页面上，不重复转人工。
                    # 网址或正文命中（拦截页本身，如 walmart.com/blocked）还在，说明没通过：照常再转人工，3 次后判失败。
                    hit = None
                if hit:
                    handoffs += 1
                    self.log(f"[HUMAN_REQUIRED] {case.name}: 检测到 {hit}")
                    if handoffs > 3:
                        tree.fail(f"captcha not resolved: {hit}")
                        break
                    h_start = time.time()
                    if self.handoff_fn is None:
                        page = await captcha.handoff(self.browser, state.url, sdir, self.input_fn, self.log,
                                                     markers=self.config.captcha_markers)
                    else:
                        page = await self.handoff_fn(self.browser, state.url, sdir, self.input_fn, self.log)
                    self._handoffs.setdefault(case.name, []).append(
                        {"start": h_start, "end": time.time(), "hit": hit, "url": state.url})
                    self._handled.add(captcha.signature(state, hit))
                    executor = make_executor(page)
                    index -= 1
                    continue
                t1 = time.monotonic()
                # ANALYZE
                STATUS.set_phase("ANALYZE")
                node_id = tree.active_id
                decision, raws, tries = await decide_with_retry(decider, DecisionInput(state, tree.view(), case.inputs))
                t2 = time.monotonic()
                # EXECUTE
                STATUS.set_phase("EXECUTE")
                page, clicks = await self._step_execute(case, tree, state, decision, raws, tries, executor, page,
                                                        node_id, index, attempt, clicks, t0, t1, t2)
        finally:
            STATUS.set_phase(None)
        return tree, index, clicks

    async def _step_execute(self, case, tree, state, decision, raws, tries, executor, page, node_id, index,
                            attempt, clicks, t0, t1, t2):
        if True:
            if decision is None:
                result = ActionResult(False, False, error="invalid decision after 3 tries")
                tree.fail("invalid decision")
            else:
                result = await self._execute(decision, state, tree, executor)
                if decision.tool == "click" and result.ok:
                    clicks += 1
                if executor.page is not page:
                    page = executor.page
            t3 = time.monotonic()
            # VERIFY
            step_summary = {"tool": decision.tool if decision else None,
                            "arguments": decision.arguments if decision else None,
                            "ok": result.ok, "effective": result.effective, "error": result.error,
                            "data": result.data if decision and TOOL_REGISTRY[decision.tool].kind == "read" else None}
            if decision and decision.tool == "create_child":
                tree.record_step(step_summary, node_id)
            elif decision and TOOL_REGISTRY[decision.tool].kind != "tree":
                tree.record_step(step_summary, node_id)
            tree.tick_step(node_id)
            durations = {"check": int((t1 - t0) * 1000), "decide": int((t2 - t1) * 1000),
                         "execute": int((t3 - t2) * 1000), "total": int((time.monotonic() - t0) * 1000)}
            rec = StepRecord(index, node_id, "pagestate.json", state.screenshot, raws,
                             decision.to_dict() if decision else None, tries - 1, result.to_dict(), durations)
            self.recorder.save_step(case.name, rec, state, trajectory_png=result.trajectory_png,
                                    dpr=state.device_pixel_ratio)
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

        def el(eid):
            e = state.element(eid)
            if e is None:
                raise LookupError(f"unknown element {eid}")
            return e
        try:
            if tool == "click":
                return await executor.click(el(a["element"]))
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
        return (not reasons), "; ".join(reasons), url

    # ---------------- 对外 ----------------
    async def run_case(self, case: CaseSpec) -> CaseResult:
        start = time.monotonic()
        crashes = 0
        self._stopped = False
        self._handoffs[case.name] = []
        self.recorder.reset_case(case.name)
        while True:
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
                         self._case_seed.get(case.name), list(self._handoffs.get(case.name, [])))
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
