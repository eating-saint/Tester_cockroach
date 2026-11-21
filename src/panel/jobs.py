"""JobManager（同一时刻一个任务）与五种任务：演示、用例、指定目标、手动、人工介入。"""
from __future__ import annotations

import asyncio
import json
import math
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from hands.actions import ActionExecutor
from hands.config import DATA_ROOT
from hands.dom import DomReader
from hands.guard import Guard
from hands.humanize import DIRECTION_NAMES, HumanizeConfig, Humanizer, derive_seed, direction_of, new_seed
from hands.recorder import RunRecorder, StepRecord
from hands.runner import CaseResult, CaseRunner, load_case
from hands.status import STATUS

REJECT_TEXT = {"miss": "没点中高亮目标", "left_area": "鼠标移出了窗口", "too_slow": "超过 5 秒", "too_few": "轨迹点太少",
               "duplicate": "重复样本", "page_changed": "页面变了"}
KIND_NAMES = {"demo": "演示", "case": "用例", "target": "指定目标", "manual": "手动", "sample_real": "真实网站采集",
              "compare_capture": "人眼比对采集", "compare_model": "人眼比对：模型作答", "book": "任务书"}
OS_INPUT_BLOCKED = ("demo", "sample_real", "target", "manual")     # 桌面驱动下不提供（用户直接操作真实窗口即可）
DEMO_IDS = {"中心": "b-c", "东": "b-e", "东南": "b-se", "南": "b-s", "西南": "b-sw", "西": "b-w",
            "西北": "b-nw", "北": "b-n", "东北": "b-ne", "小按钮": "b-tiny"}


class JobBusy(Exception):
    def __init__(self, title: str):
        super().__init__(title)
        self.title = title


@dataclass
class JobInfo:
    id: str
    kind: str
    title: str
    state: str = "running"          # running | need_human | stopping | done | failed | stopped
    started: float = field(default_factory=time.time)
    progress: dict = field(default_factory=dict)
    result: dict | None = None
    need_human: dict | None = None
    confirm_pending: dict | None = None     # 需确认：{kind, tool, target, hit, text}

    def to_dict(self) -> dict:
        return asdict(self)


def tree_snapshot(tree) -> dict:
    nodes = []
    for n in tree.nodes.values():
        nodes.append({"id": n.id, "parent": n.parent_id, "goal": n.goal, "depth": n.depth, "status": n.status,
                      "steps_used": n.steps_used, "summary": n.summary, "child_summaries": n.child_summaries})
    return {"nodes": nodes, "active_id": tree.active_id, "max_steps": tree.max_steps, "finished": tree.finished}


def _jsonable(x):
    return json.loads(json.dumps(x, ensure_ascii=False, default=str))


class JobManager:
    def __init__(self, service):
        self.svc = service
        self.current: JobInfo | None = None
        self.last: JobInfo | None = None
        self._task: asyncio.Task | None = None
        self._stop = False
        self._continue = asyncio.Event()
        self.tree: dict | None = None
        self.steps: list[dict] = []
        self.demo_rounds: list[dict] = []        # 演示：本轮 + 上一轮
        self.last_target: dict | None = None
        from hands.pause import PauseGate
        self._gate_local = PauseGate(watch_human=False)   # CDP / Playwright：只响应面板按钮
        self._confirm_fut: asyncio.Future | None = None
        self.expand = None                       # 当前（或最近一次）任务书运行器（TaskbookRunner）
        self.expand_snapshot: dict | None = None
        self.book_client = None                  # 验收用：替身 4b（设了就不连本机 Ollama）

    # ---------------- 暂停 / 确认（spec F8、F9、F18） ----------------
    @property
    def gate(self):
        return getattr(self.svc.browser, "gate", None) or self._gate_local

    def _grace(self) -> float:
        from hands.config import desktop_settings
        from hands.driver import caps_of
        return float(desktop_settings(self.svc.config)["resume_grace_s"]) if caps_of(self.svc.browser).os_input else 0

    def _log_control(self, op: str, source: str, **detail):
        """任务书运行中的控制操作记进 edits.jsonl（来源：电脑 / 设备名）。"""
        r = self.expand
        if r is not None and self.current is not None and self.current.kind == "book":
            try:
                r.log_control(op, source, **detail)
            except Exception:
                pass

    def pause(self, source: str = "电脑") -> bool:
        if not self.busy:
            return False
        ok = self.gate.pause("user")
        if ok:
            self._log_control("pause", source)
        self.svc.push_soon()
        return ok

    def resume(self, source: str = "电脑") -> bool:
        g = self.gate
        if g.paused is None or g.paused.reason == "handoff":
            return False
        if self.expand is not None and self.expand.state == "waiting":
            return False                         # 次数用满：用"再给 N 次"或"结束并输出"
        g.resume("button")
        g.arm(self._grace())
        self._log_control("resume", source)
        self.svc.push_soon()
        return True

    async def _confirm_fn(self, req) -> bool:
        job = self.current
        if job is None:
            return False
        prev = job.state
        job.state = "need_confirm"
        job.confirm_pending = {**req.to_dict(), "text": req.describe()}
        self._confirm_fut = asyncio.get_running_loop().create_future()
        self.svc.event("need_confirm", {"confirm": job.confirm_pending})
        self.svc.push_soon()
        STATUS.set_wait("human")
        try:
            return bool(await self._confirm_fut)
        finally:
            STATUS.clear_wait()
            job.confirm_pending = None
            self._confirm_fut = None
            if job.state == "need_confirm":
                job.state = prev if prev != "need_confirm" else "running"
            self.gate.arm(self._grace())
            self.svc.push_soon()

    def confirm(self, allow: bool, source: str = "电脑") -> bool:
        f = self._confirm_fut
        if f is None or f.done():
            return False
        self._log_control("confirm", source, allow=bool(allow))
        f.set_result(bool(allow))
        return True

    # ---------------- 公共 ----------------
    @property
    def busy(self) -> bool:
        return self.current is not None and self.current.state in ("running", "need_human", "stopping",
                                                                   "need_confirm")

    @property
    def stop_requested(self) -> bool:
        return self._stop

    def state(self) -> dict | None:
        j = self.current or self.last
        return j.to_dict() if j else None

    async def start(self, kind: str, params: dict) -> JobInfo:
        if self.busy:
            raise JobBusy(self.current.title)
        if kind == "expand":                       # 上一章的名字：同任务书
            kind = "book"
        if kind not in KIND_NAMES:
            raise ValueError(f"unknown job kind: {kind}")
        from hands.driver import caps_of
        if kind in OS_INPUT_BLOCKED and caps_of(self.svc.browser).os_input:
            raise ValueError(f"桌面驱动下不提供「{KIND_NAMES[kind]}」：请直接操作代理窗口，或切换到 CDP 驱动")
        if kind == "book":
            params = {**params, "_book": await self.prepare_book(params)}
        if kind == "sample_real":
            if getattr(self.svc.browser, "driver", self.svc.config.driver) != "cdp":
                raise ValueError("Playwright 驱动不支持真实网站采集（请切换到 CDP 驱动）")
            if params.get("device") not in ("mouse", "touchpad"):
                raise ValueError("请先选择设备（鼠标 / 触控板）")
            if self.svc.browser_alive() and self.svc.browser.headless and not params.get("allow_headless"):
                raise ValueError("请先切到有界面：真实网站采集要在真实的 Chrome 窗口里用手操作")
        if kind != "compare_model":                  # 模型作答只用模型服务，不用浏览器
            await self.svc.ensure_browser()
        title = KIND_NAMES[kind]
        if kind == "case":
            title += " " + Path(params.get("case", "")).stem
        job = JobInfo(uuid.uuid4().hex[:8], kind, title)
        self.current = job
        self._stop = False
        self._continue = asyncio.Event()
        if kind in ("case", "demo", "book"):
            self.tree = None
            self.steps = []
        if kind == "book":
            b = params["_book"]
            job.title += f" {b.title or b.name}" + ("（试跑）" if params.get("trial") else "")
        if self.gate.paused is not None:            # 上一个任务遗留的暂停（例如次数用满后停止）
            self.gate.resume("new_job")
        from . import compare as cmp
        runner = {"demo": self._demo, "case": self._case, "target": self._target, "manual": self._manual,
                  "sample_real": self._sample_real, "book": self._book,
                  "compare_capture": lambda j, p: cmp.job_capture(self, j, p),
                  "compare_model": lambda j, p: cmp.job_model(self, j, p)}[kind]
        self._task = asyncio.create_task(self._wrap(job, runner, params))
        self.svc.push_soon()
        return job

    async def _wrap(self, job: JobInfo, fn, params):
        try:
            self.svc.settings.apply_now(self.svc.config)
            await fn(job, params)
            if job.state in ("running", "stopping", "need_human"):
                job.state = "stopped" if self._stop else "done"
        except Exception as e:
            traceback.print_exc()
            job.state = "failed"
            job.result = {**(job.result or {}), "passed": False, "reason": f"{type(e).__name__}: {e}"}
        finally:
            STATUS.clear_wait()
            self.last = job
            self.current = None
            self.svc.on_job_end(job)
            self.svc.event("job_end", {"job": job.to_dict()})
            self.svc.push_soon()

    def stop(self, source: str = "电脑") -> bool:
        if not self.busy:
            return False
        self._log_control("stop", source)
        self._stop = True
        self.current.state = "stopping"
        self._continue.set()          # 等人工 / 手动模式的任务也能结束
        self.confirm(False)           # 等确认的动作按拒绝处理
        if self.gate.paused is not None:
            self.gate.resume("stop")  # 暂停中也要能结束：恢复后循环开头看到停止标志
        self.svc.push_soon()
        return True

    def continue_human(self) -> bool:
        if self.current is None or self.current.state != "need_human":
            return False
        self._continue.set()
        return True

    def accepts_input(self) -> bool:
        j = self.current
        return j is not None and (j.kind == "manual" or j.state == "need_human")

    def before_action(self, executor):
        if self.svc.settings.apply_now(self.svc.config, executor.humanizer):
            self.svc.push_soon()

    def _seed(self) -> int:
        s = self.svc.settings.current.get("humanize.seed")
        return int(s) if s is not None else new_seed()

    def _humanizer(self, seed: int, force_mouse: bool = False) -> Humanizer:
        cfg = HumanizeConfig.from_dict(self.svc.config.humanize)
        from hands.driver import caps_of
        if caps_of(self.svc.browser).os_input and not cfg.mouse.enabled:
            from hands.config import desktop_humanize     # 桌面驱动：真实鼠标默认平滑、放慢移动
            cfg = desktop_humanize(self.svc.config, cfg)
        if force_mouse:
            cfg.mouse.enabled = True
        cfg.seed = seed
        return Humanizer(cfg, seed, library=self.svc.samples.library)

    def _recorder(self, label: str) -> RunRecorder:
        return RunRecorder(self.svc.config.runs_dir, f"panel-{label}")

    # ---------------- 演示 ----------------
    async def _demo(self, job: JobInfo, params: dict):
        seed = int(params["seed"]) if params.get("seed") not in (None, "") else self._seed()
        self.svc.ensure_fixtures()
        page = self.svc.browser.page
        await page.navigate(self.svc.fixture_url("f12_mouse/index.html"))
        h = self._humanizer(seed, force_mouse=True)
        ex = ActionExecutor(page, h, before_action=self.before_action)
        rec = self._recorder("demo")
        order: list[tuple[str, str]] = [("中心", "起点")]
        for name in ["东", "东南", "南", "西南", "西", "西北", "北", "东北"]:
            order += [(name, "目标"), ("中心", "回中心")]
        order.append(("小按钮", "目标"))
        rows = []
        prev = self.demo_rounds[-1] if self.demo_rounds else None
        cur = {"seed": seed, "rows": rows, "job": job.id}
        self.demo_rounds = ([prev] if prev else []) + [cur]
        job.progress = {"step": 0, "max_steps": len(order), "seed": seed}
        for i, (name, role) in enumerate(order):
            if self._stop:
                break
            els, _ = await DomReader().read(page)
            el = next((e for e in els if e.name == name), None)
            if el is None:
                rows.append({"i": i + 1, "name": name, "role": role, "hit": False, "error": "未找到按钮"})
                continue
            v0 = self.svc.settings.version
            r = await ex.click(el)
            last = json.loads(await page.evaluate("JSON.stringify(window.__clicks.slice(-1)[0]||null)") or "null")
            hit = bool(r.ok and last and last["id"] == DEMO_IDS[name]
                       and last["rect"][0] <= last["x"] <= last["rect"][0] + last["rect"][2]
                       and last["rect"][1] <= last["y"] <= last["rect"][1] + last["rect"][3])
            t = r.trajectory or {}
            row = {"i": i + 1, "name": name, "role": role, "hit": hit, "planned_ms": t.get("duration_ms"),
                   "actual_ms": t.get("actual_ms"), "template": t.get("template"), "source": t.get("source"),
                   "sample_id": t.get("sample_id"), "fallback": t.get("fallback"),
                   "overshoot": bool(t.get("overshoot_point")), "settings_version": v0,
                   "overshoot_prob": h.cfg.mouse.overshoot_prob, "error": r.error, "trajectory": t}
            rows.append(row)
            job.progress["step"] = i + 1
            self.svc.event("demo_click", {"row": row})
            self.svc.push_soon()
        hits = sum(1 for r in rows if r["hit"])
        passed = hits == len(order) and not self._stop
        (rec.root / "demo.json").write_text(json.dumps(_jsonable(cur), ensure_ascii=False, indent=1), encoding="utf-8")
        res = CaseResult("demo", passed, f"命中 {hits}/{len(rows)}" + ("（用户停止）" if self._stop else ""),
                         steps=len(rows), humanize_seed=seed)
        rec.write_report([res], {"run_seed": seed, **h.cfg.to_dict()})
        job.result = {"passed": passed, "reason": res.reason, "seed": seed, "hits": hits, "total": len(rows),
                      "report": f"{rec.root.name}/report.html"}

    # ---------------- 用例 ----------------
    async def _case(self, job: JobInfo, params: dict):
        cases_dir = DATA_ROOT / "cases"
        path = (cases_dir / params["case"]).resolve()
        if cases_dir.resolve() not in path.parents or not path.exists():
            raise FileNotFoundError(params["case"])
        text = path.read_text(encoding="utf-8")
        if "${BASE}" in text or "${XBASE}" in text:
            self.svc.ensure_fixtures()
        case = load_case(path, self.svc.fixture_vars())
        job.progress = {"step": 0, "max_steps": case.limits.get("max_steps"), "attempt": 1}
        rec = self._recorder(case.name)
        run_seed = self._seed()

        def factory(name, attempt):
            return self._humanizer(derive_seed(run_seed, name, attempt))

        def on_step(info):
            tree = info["tree"]
            self.tree = tree_snapshot(tree)
            d = info.get("decision")
            r = info.get("result")
            self.steps.append(_jsonable({
                "index": info["index"], "attempt": info["attempt"], "tool": d.tool if d else None,
                "arguments": d.arguments if d else None, "ok": r.ok if r else None,
                "effective": r.effective if r else None, "changes": r.changes if r else None,
                "error": r.error if r else None, "durations": info.get("durations"),
                "trajectory": r.trajectory if r else None}))
            self.steps = self.steps[-300:]
            job.progress.update({"step": info["index"], "attempt": info["attempt"]})
            self.svc.push_soon()

        runner = CaseRunner(self.svc.browser, self.svc.config, rec, on_step=on_step, log=self.svc.log,
                            should_stop=lambda: self._stop, before_action=self.before_action,
                            handoff_fn=self._handoff, humanizer_factory=factory,
                            gate=self.gate, confirm_fn=self._confirm_fn)
        res = await runner.run_case(case)
        report = rec.write_report([res], {"run_seed": run_seed, **HumanizeConfig.from_dict(
            self.svc.config.humanize).to_dict()})
        job.result = {"passed": res.passed, "reason": res.reason, "steps": res.steps, "crashes": res.crashes,
                      "report": f"{rec.root.name}/{report.name}", "human_handoffs": res.human_handoffs}
        if res.reason == "user_stopped":
            job.state = "stopped"

    # ---------------- 任务书（使用者自制的关联展开任务） ----------------
    @property
    def books(self):
        from .books import BookService
        return BookService(DATA_ROOT / "tasks")

    async def prepare_book(self, params: dict):
        """发起前：读任务书（按名称或草稿文本）、校验、填参数、检查本机 Ollama。不合格直接拒绝启动。"""
        from hands.taskbook.book import BookError, bind
        from hands.taskbook.convert import detect
        from hands.taskbook.runner import uses_4b
        from hands.taskbook.validate import check_text
        text, name = params.get("text"), params.get("name") or params.get("type")
        if not text:
            if not name:
                raise ValueError("请选择任务书")
            name = Path(str(name)).stem
            text = self.books.get(name)["text"]
        if detect(text) == "legacy":
            raise ValueError("这是上一章的旧格式：请先在搭建工具里导入，用积木补全节点流程（需要用积木改写）")
        b, probs = check_text(text, name)
        if b is not None and b.status == "needs_rewrite":
            raise ValueError("这份任务书由旧格式转换而来，需要用积木改写节点流程后才能运行")
        if b is None or probs:
            raise ValueError("任务书有问题，不能运行：" + "；".join(str(p) for p in probs))
        values = {k: v for k, v in (params.get("values") or params.get("params") or {}).items()
                  if v not in (None, "")}
        if any(p.get("name") == "BASE" for p in b.params) and "BASE" not in values:
            self.svc.ensure_fixtures()
            values["BASE"] = self.svc.fixture_vars()["BASE"]
        try:
            b = bind(b, values)
        except BookError as e:
            raise ValueError(str(e)) from None
        if uses_4b(b.flow) and self.book_client is None:
            from hands.config import vlm_settings
            from hands.vlm.client import ModelUnavailable, OllamaClient
            try:
                await OllamaClient(vlm_settings(self.svc.config)).check()
            except ModelUnavailable as e:
                raise RuntimeError(f"本机 Ollama 不可用：{e}") from None
        return b

    async def _book(self, job: JobInfo, params: dict):
        from hands.taskbook.runner import TaskbookRunner
        book = params["_book"]
        trial = bool(params.get("trial"))
        budget = int(params["budget"]) if params.get("budget") not in (None, "", 0) else None
        rec = self._recorder(f"book-{book.name}" + ("-trial" if trial else ""))
        run_seed = self._seed()

        def factory(name, attempt):
            return self._humanizer(derive_seed(run_seed, name, attempt))

        def on_change(snap):
            self.expand_snapshot = _jsonable(snap)
            c = snap.get("counts", {})
            job.progress = {"used": c.get("used"), "budget": c.get("budget"), "leaves": c.get("leaves"),
                            "nodes": c.get("nodes"), "state": snap.get("state"), "run": rec.root.name,
                            "wait_reason": snap.get("wait_reason"), "trial": trial}
            self.svc.event("expand_changed", {"counts": c, "state": snap.get("state"), "run": rec.root.name})
            if snap.get("state") == "waiting":
                self.svc.event("need_decision", {"why": snap.get("wait_reason"), "counts": c})
            self.svc.push_soon()

        runner = TaskbookRunner(self.svc.browser, self.svc.config, rec, book, gate=self.gate,
                                confirm_fn=self._confirm_fn, on_change=on_change, log=self.svc.log,
                                handoff_fn=self._handoff, should_stop=lambda: self._stop,
                                before_action=self.before_action, humanizer_factory=factory,
                                trial=trial, budget=budget, client=self.book_client)
        self.expand = runner
        self._log_control("start", params.get("_source") or "电脑", name=book.name, values=book.values, trial=trial)
        res = await runner.run()
        self.expand_snapshot = _jsonable({**runner.snapshot(), "result": res})
        job.result = {"passed": res["state"] == "done", "reason": f"末端叶子 {len(res['leaves'])} 个",
                      "dir": f"{rec.root.name}/book", "run": rec.root.name, "trial": trial,
                      "ranked": res.get("ranked")}
        if self._stop:
            job.state = "stopped"

    def expand_edit(self, op: str, node: str | None = None, key: str | None = None,
                    values: list | None = None, n: int | None = None, rev: int | None = None,
                    source: str = "电脑") -> dict:
        r = self.expand
        if r is None or self.current is None or self.current.kind != "book":
            raise PermissionError("没有正在运行的任务书")
        r.edit(op, node, key, values, n, rev=rev, source=source, grace=self._grace())
        self.svc.push_soon()
        return self.expand_snapshot or {}

    async def _handoff(self, browser, url, save_dir, input_fn=None, log=print):
        """面板版人工介入：不切有界面，在预览里手动处理，点"继续"后返回当前页面。"""
        from hands import screenshot as shot
        job = self.current
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        try:
            (save_dir / "captcha.png").write_bytes(await shot.capture(browser.page))
        except Exception as e:
            log(f"[HUMAN_REQUIRED] 截图失败: {e}")
        job.state = "need_human"
        job.need_human = {"url": url, "since": time.time()}
        self._continue = asyncio.Event()
        await self.svc.preview.set_on(True)
        STATUS.set_wait("human")
        self.svc.event("need_human", {"url": url})
        self.svc.push_soon()
        try:
            await self._continue.wait()
        finally:
            STATUS.clear_wait()
            job.need_human = None
            if job.state == "need_human":
                job.state = "running"
            self.svc.push_soon()
        if browser.page is None:
            await browser.new_page()
        return browser.page

    # ---------------- 指定目标 ----------------
    async def _target(self, job: JobInfo, params: dict):
        page = self.svc.browser.page
        if params.get("url"):
            await page.navigate(params["url"])
        x, y = float(params["x"]), float(params["y"])
        els, _ = await DomReader().read(page)
        hits = [e for e in els if e.visible and e.box.contains(x, y) and e.box.width * e.box.height > 0]
        if not hits:
            job.result = {"passed": False, "reason": f"({x:.0f},{y:.0f}) 处没有可点元素"}
            return
        el = min(hits, key=lambda e: e.box.width * e.box.height)
        seed = self._seed()
        h = self._humanizer(seed)
        rec = self._recorder("target")
        ex = ActionExecutor(page, h, capture_trajectory=h.mouse_on, before_action=self.before_action,
                            guard=Guard.from_config(getattr(self.svc.config, "guard", None)))   # ver1 兜底拦截
        t0 = time.monotonic()
        r = await ex.click(el)
        dur = int((time.monotonic() - t0) * 1000)
        step = StepRecord(1, "root", "", None, [], {"tool": "click", "arguments": {"element": el.id}}, 0,
                          r.to_dict(), {"execute": dur, "total": dur})
        rec.save_step("target", step, trajectory_png=r.trajectory_png)
        res = CaseResult("target", r.ok, r.error or ("生效：" + ",".join(r.changes) if r.effective else "未检测到变化"),
                         steps=1, humanize_seed=seed)
        rec.write_report([res], {"run_seed": seed, **h.cfg.to_dict()})
        self.last_target = _jsonable({"element": el.to_dict(), "result": r.to_dict()})
        job.result = {"passed": r.ok, "reason": res.reason, "element": el.to_dict(), "effective": r.effective,
                      "changes": r.changes, "trajectory": r.trajectory, "report": f"{rec.root.name}/report.html"}

    # ---------------- 真实网站采集（F15，K33–K34） ----------------
    async def _pick_real_target(self, page, start, device, rng):
        """从当前页可点、可见的主文档元素里挑目标：距离 150–600，优先格子最缺的方向。"""
        els, _ = await DomReader().read(page)
        lay = await page.layout()
        vw, vh = lay["vw"], lay["vh"]
        grid = self.svc.samples.grid().get(device, {})
        by_dir: dict[int, list] = {}
        for e in els:
            b = e.box
            if not (e.visible and e.enabled and not e.frame_path and min(b.width, b.height) >= 8):
                continue
            cx, cy = b.x + b.width / 2, b.y + b.height / 2
            if not (0 <= b.x and 0 <= b.y and b.x + b.width <= vw and b.y + b.height <= vh):
                continue                                   # 只挑完整在视口里的
            dist = math.hypot(cx - start[0], cy - start[1])
            if 150 <= dist <= 600 and not b.contains(*start):
                by_dir.setdefault(direction_of(cx - start[0], cy - start[1]), []).append(e)
        if not by_dir:
            return None, None, []
        count = lambda d: sum((grid.get(d) or {}).values())   # noqa: E731
        order = sorted(range(8), key=lambda d: (count(d), rng.random()))
        skipped = [d for d in order if d not in by_dir and count(d) < 5]
        d = next(d for d in order if d in by_dir)
        return d, rng.choice(by_dir[d]), skipped

    async def _sample_real(self, job: JobInfo, params: dict):
        import random as _random
        from .samples import RealSampleInjector

        device = params["device"]
        rng = _random.Random()
        q: asyncio.Queue = asyncio.Queue()
        b = self.svc.browser
        page = b.page
        inj = RealSampleInjector(q.put_nowait)
        await inj.install(page.session)
        st = {"start": None, "doc": None, "t": None}
        leaves: dict[str, float] = {}
        target = None
        prog = job.progress = {"device": device, "saved": 0, "rejected": 0, "question": None,
                               "last": None, "msg": "请在页面上随便点一下，作为第一题的起点。", "can_back": False}

        def say(text):
            prog["msg"] = text
            self.svc.push_soon()

        try:
            while not self._stop:
                if b.page is not page and b.page is not None:     # 换了标签页：在新页上重装
                    await inj.uninstall()
                    page = b.page
                    inj = RealSampleInjector(q.put_nowait)
                    await inj.install(page.session)
                    st.update(start=None, doc=None, t=None)
                    target = None
                    prog["question"] = None
                    say("已切换到新标签页。请在页面上随便点一下，作为下一题的起点。")
                try:
                    m = await asyncio.wait_for(q.get(), 0.5)
                except asyncio.TimeoutError:
                    continue
                if m.get("k") == "leave":
                    leaves[m.get("doc")] = float(m.get("t", 0))
                    continue
                if m.get("k") != "down":
                    continue
                x, y, t = float(m["x"]), float(m["y"]), float(m["t"])
                prog["question"] = None                    # 这一题结束：新题出来之前不显示旧目标
                try:
                    url_before = await page.url()
                except Exception:
                    url_before = None
                if target is not None and st["doc"] == m.get("doc") and st["t"] is not None:
                    t0 = st["t"]
                    pts = [[st["start"][0], st["start"][1], 0]]
                    for px, py, pt in m.get("pts") or []:
                        if t0 < pt < t:
                            pts.append([float(px), float(py), max(int(round(pt - t0)), pts[-1][2])])
                    pts.append([x, y, max(int(round(t - t0)), pts[-1][2])])
                    lv = leaves.get(m.get("doc"))
                    raw = {"device": device, "source": "real", "domain": m.get("host") or None,
                           "start": list(st["start"]), "end": [x, y], "target": target.box.to_dict(),
                           "points": pts, "dpr": float(m.get("dpr") or 1.0),
                           "left_area": bool(lv is not None and t0 < lv < t)}
                    res = self.svc.samples.add(raw)
                    prog["last"] = res
                    if res.get("ok"):
                        prog["saved"] += 1
                    else:
                        prog["rejected"] += 1
                    self.svc.event("sample_real", {"result": res})
                elif target is not None:
                    prog["last"] = {"ok": False, "reason": "page_changed"}
                st.update(start=(x, y), doc=m.get("doc"), t=t)
                await inj.session.send("Overlay.hideHighlight")
                # 点击可能触发跳转：等一下看页面是否换了文档
                await asyncio.sleep(0.6)  # condition-poll
                jumped = False
                try:
                    await page.wait_settled()
                except Exception:
                    pass
                try:
                    cur_url = await page.url()
                except Exception:
                    cur_url = None
                if url_before is not None and cur_url is not None and cur_url != url_before:
                    jumped = True
                if jumped:
                    st.update(start=None, doc=None, t=None)
                    target = None
                    prog["question"] = None
                    prog["can_back"] = True
                    last = prog.get("last") or {}
                    say(("页面已跳转，样本已保存。" if last.get("ok") else "页面已跳转。") +
                        "可点“后退”回到原页面；然后在页面上随便点一下作为新起点。")
                    continue
                d, el, skipped = await self._pick_real_target(page, st["start"], device, rng)
                if el is None:
                    target = None
                    prog["question"] = None
                    say("本页在这个位置附近没有合适的目标（150–600 像素内），已跳过。换个位置点一下，或换个页面。")
                    continue
                target = el
                await inj.highlight(el)
                prog["question"] = {"direction": d, "name": el.name[:40], "box": el.box.to_dict()}
                last = prog.get("last")
                head = ""
                if last is not None:
                    head = "已保存。" if last.get("ok") else f"作废：{REJECT_TEXT.get(last.get('reason'), last.get('reason'))}。"
                skip = f"（本页没有合适的“{'、'.join(DIRECTION_NAMES[s] for s in skipped)}”方向目标，已跳过）" if skipped else ""
                say(f"{head}下一题：方向 {DIRECTION_NAMES[d]}，点橙色高亮的元素。{skip}")
        finally:
            await inj.uninstall()
            prog["question"] = None
        job.result = {"passed": True, "reason": f"已保存 {prog['saved']} 条，作废 {prog['rejected']} 条",
                      "saved": prog["saved"], "rejected": prog["rejected"]}

    # ---------------- 手动 ----------------
    async def _manual(self, job: JobInfo, params: dict):
        await self.svc.preview.set_on(True)
        await self._continue.wait()           # stop() 会 set
        job.result = {"passed": True, "reason": "手动模式结束"}
