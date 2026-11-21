"""TaskbookRunner：按展开规则挑节点 → 节点流程引擎 → 按顺序应用改树（来源 rule）→ 快照与记录 → 循环。

- 次数由引擎按积木消耗记账（搜索、打开、翻页各 1 次）；次数用满或任务书"停·交给人"时进入"等待决策"：
  暂停（原因 budget / decision），只能"再给 N 次"或"结束并输出"，不自行结束、不自行续跑。试跑到上限直接结束。
- 人工改树只在暂停或等待决策时进行（edit）；修改带树的版本号 rev，与当前不一致就拒绝（两端同时修改）。
  节点在执行期间被改过，本轮结果作废。
- 记录在 <运行目录>/book/：任务书副本、参数、每个节点每块的记录与截图、快照、修改记录、结果、报告页。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from ..checks import HandoffChecker
from ..pause import PauseGate
from ..status import STATUS
from .blocks import REGISTRY
from .book import TaskBook, dump
from .engine import NodeCtx, NodeRun, run_node
from .report import build_result, report_html

PICK_BLOCKS = ("pick_list", "read_fields")


def _dump(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def uses_4b(flow) -> bool:
    for item in flow or []:
        bid, params = next(iter(item.items()))
        if REGISTRY.get(bid) is not None and REGISTRY[bid].actor == "4b":
            return True
        if bid == "open" and uses_4b((params or {}).get("flow")):
            return True
    return False


def _safe(word: str) -> str:
    return re.sub(r"[^\w\-]+", "_", word)[:40] or "node"


class TaskbookRunner:
    def __init__(self, browser, config, recorder, book: TaskBook, gate: PauseGate | None = None,
                 confirm_fn=None, on_change=None, log=print, input_fn=input, handoff_fn=None,
                 should_stop=None, before_action=None, client=None, humanizer_factory=None,
                 trial: bool = False, budget: int | None = None, engine=None):
        from ..expand.tree import ExpandTree
        self.browser = browser
        self.config = config
        self.recorder = recorder
        self.book = book
        self.gate = gate or getattr(browser, "gate", None) or PauseGate(watch_human=False)
        self.confirm_fn = confirm_fn
        self.on_change = on_change
        self.log = log
        self.input_fn = input_fn
        self.handoff_fn = handoff_fn
        self.should_stop = should_stop or (lambda: False)
        self.before_action = before_action
        self.humanizer_factory = humanizer_factory
        self.client = client
        self.trial = trial
        self.engine = engine or run_node
        lim = book.limits
        self.budget = int(budget if budget is not None else (lim.get("trial_budget", 5) if trial else lim.get("budget", 120)))
        self.tree = ExpandTree(book.groups, book.start, self.budget)
        self.state = "idle"                 # running / waiting / done / stopped
        self.wait_reason: str | None = None
        self.current: str | None = None
        self.kept = 0
        self.dir = recorder.root / "book"
        self._snap = 0
        self._seq = 0
        self.evidence: dict[tuple, dict] = {}     # (键, 词) → 依据
        self.result: dict | None = None
        self.last_visit: dict = {}
        self.executor_factory = None              # 测试用：替换 ActionExecutor（page -> 执行器）

    # ---------------- 记录 ----------------
    def snapshot(self) -> dict:
        return {"state": self.state, "wait_reason": self.wait_reason, "current": self.current, "trial": self.trial,
                "book": {"name": self.book.name, "title": self.book.title, "values": self.book.values},
                "run": self.recorder.root.name, **self.tree.snapshot()}

    def _save(self, why: str):
        self._snap += 1
        snap = {"why": why, **self.snapshot()}
        _dump(self.dir / "snapshots" / f"{self._snap:04d}.json", snap)
        _dump(self.dir / "events.json", [e.to_dict() for e in self.tree.events])
        if self.on_change is not None:
            try:
                self.on_change(snap)
            except Exception:
                pass

    def log_control(self, op: str, source: str = "电脑", **detail):
        """运行中的修改与控制操作（暂停、恢复、确认、改树……）：时间、来源、内容。"""
        self.dir.mkdir(parents=True, exist_ok=True)
        with (self.dir / "edits.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "source": source, "op": op, **detail},
                               ensure_ascii=False, default=str) + "\n")

    # ---------------- 运行中改树（面板 / 平板 / 命令行） ----------------
    def edit(self, op: str, node: str | None = None, key: str | None = None, values: list | None = None,
             n: int | None = None, rev: int | None = None, source: str = "电脑", grace: float = 0.0) -> dict:
        t, g = self.tree, self.gate
        if rev is not None and int(rev) != t.rev:
            raise PermissionError("已被另一端修改，请刷新")
        if g.paused is None and self.state != "waiting":
            raise PermissionError("请先暂停，再修改任务树")
        if node is not None and node not in t.nodes:
            raise ValueError(f"没有节点 {node}")
        word = t.nodes[node].word if node else None
        if op == "delete":
            t.delete(node)
        elif op == "set_terminal":
            t.set_terminal(node)
        elif op == "set_pending":
            t.set_pending(node)
        elif op == "add_group":
            vs = [str(v).strip() for v in (values or []) if str(v).strip()]
            if not key or not vs:
                raise ValueError("加一组需要键和至少一个值")
            t.add_group(str(key), vs)
        elif op == "grant":
            t.grant(int(n or 0))
            if self.state == "waiting" and not t.exhausted:
                self.wait_reason = None
                g.resume("button")
                g.arm(grace)
        elif op == "finish":
            t.finish("user")
            if g.paused is not None:
                g.resume("button")
        else:
            raise ValueError(f"未知操作：{op}")
        self.log_control(op, source, node=node, word=word, key=key, values=values, n=n)
        self._save(f"edit:{op}")
        return self.snapshot()

    # ---------------- 执行器、检查器 ----------------
    def _parts(self):
        from ..config import desktop_humanize, desktop_settings, vlm_settings
        from ..confirm import ConfirmPolicy, cli_confirm_fn
        from ..driver import caps_of
        from ..guard import Guard
        from ..humanize import HumanizeConfig, Humanizer, new_seed
        caps = caps_of(self.browser)
        confirm = ConfirmPolicy.from_config(getattr(self.config, "confirm", None), caps.confirm_default)
        confirm_fn = self.confirm_fn or cli_confirm_fn(self.input_fn, self.log)
        grace = float(desktop_settings(self.config)["resume_grace_s"]) if caps.os_input else 0.0
        humanizer = None
        if self.humanizer_factory is not None:
            humanizer = self.humanizer_factory(f"book-{self.book.name}", 1)
        else:
            h = HumanizeConfig.from_dict(getattr(self.config, "humanize", None))
            if caps.os_input and not h.mouse.enabled:
                h = desktop_humanize(self.config, h)
            if h.any_enabled:
                humanizer = Humanizer(h, h.seed if h.seed is not None else new_seed())
        guard = Guard.from_config(getattr(self.config, "guard", None))
        from ..actions import ActionExecutor

        def make_executor(p):
            return ActionExecutor(p, humanizer, capture_trajectory=False, before_action=self.before_action,
                                  guard=guard, confirm=confirm, confirm_fn=confirm_fn, gate=self.gate)
        if self.executor_factory is not None:
            make_executor = self.executor_factory
        checker = HandoffChecker(self.browser, self.config, confirm, self.gate, self.handoff_fn, self.input_fn,
                                 self.log, grace=grace)
        vs = vlm_settings(self.config)
        client, prompts = self.client, None
        if uses_4b(self.book.flow):
            from ..vlm.client import OllamaClient
            from ..vlm.prompts import PromptBook
            client = client or OllamaClient(vs)
            prompts = PromptBook(vs.get("prompts_dir"))
        return make_executor, checker, client, prompts, vs, grace

    # ---------------- 主循环 ----------------
    async def _wait_decision(self, reason: str, detail: dict):
        self.state = "waiting"
        self.wait_reason = reason
        self.log(f"[taskbook] 等待决策（{reason}）：已用 {self.tree.used} / {self.tree.budget} 次")
        self.gate.pause("budget" if reason == "budget" else "decision", detail)
        self._save(reason)
        await self.gate.wait()
        self.state = "running"
        self.wait_reason = None

    async def run(self) -> dict:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "taskbook.yaml").write_text(self.book.text or dump(self.book), encoding="utf-8")
        _dump(self.dir / "params.json", {"name": self.book.name, "values": self.book.values, "budget": self.budget,
                                         "trial": self.trial, "driver": getattr(self.config, "driver", None)})
        make_executor, checker, client, prompts, vs, grace = self._parts()
        page = self.browser.page or await self.browser.new_page()
        can_tab = all(hasattr(page, m) for m in ("new_tab", "close_tab"))
        keep_tabs = int(self.book.limits.get("keep_tabs", 0) or 0)
        tree = self.tree
        self.state = "running"
        self._save("start")
        while True:
            await self.gate.wait()
            if self.should_stop():
                self.state = "stopped"
                break
            if tree.finished:
                break
            if tree.exhausted:
                if self.trial:
                    self.log(f"[taskbook] 试跑到上限 {tree.budget} 次，结束")
                    break
                await self._wait_decision("budget", {"used": tree.used, "budget": tree.budget})
                continue
            node = tree.next_node()
            if node is None:
                break
            self.current = node.id
            v0 = node.version
            tab = None
            if can_tab and keep_tabs and self.kept < keep_tabs:
                from ..pause import Interrupted
                for _ in range(3):
                    try:
                        tab = await page.new_tab("about:blank")
                        break
                    except Interrupted:                  # 人工介入暂停：等恢复后重开
                        await self.gate.wait()
                    except Exception as e:
                        self.log(f"[taskbook] 新标签页失败：{e}")
                        break
            tree.mark_searched(node.id, count=False)
            self._seq += 1
            node_dir = self.dir / "nodes" / f"{self._seq:04d}_{_safe(node.word)}"
            node_dir.mkdir(parents=True, exist_ok=True)
            self._save("node_start")
            ctx = NodeCtx(browser=self.browser, page=page, config=self.config, book=self.book, tree=tree,
                          node=node, node_dir=node_dir, gate=self.gate, checker=checker, client=client,
                          prompts=prompts, vlm=vs, make_executor=make_executor, should_stop=self.should_stop,
                          consume=self._consume, remaining=lambda: tree.budget - tree.used,
                          last_visit=self.last_visit, can_tab=can_tab, log=self.log)
            STATUS.set_phase("BOOK")
            try:
                nrun: NodeRun = await self.engine(ctx)
            finally:
                STATUS.set_phase(None)
                page = ctx.page or self.browser.page or page
            try:
                node.url = await page.url()
            except Exception:
                pass
            shots = [s for b in nrun.blocks for s in b.get("shots", [])]
            node.shot = str((node_dir / shots[-1]).relative_to(self.recorder.root)) if shots else None
            record = {"node": node.id, "word": node.word, "key": tree.key_of(node.id), "cost": nrun.cost,
                      "stop": nrun.stop, "blocks": nrun.blocks, "ops": [o.to_dict() for o in nrun.ops],
                      "applied": [], "rejected": []}
            if nrun.stop == "cancel" or self.should_stop():
                self.state = "stopped"
                _dump(node_dir / "blocks.json", {**record, "discarded": "已停止"})
                await self._close(page, tab)
                break
            if node.version != v0 or node.state == "deleted":
                self.log(f"[taskbook] {node.word}: 执行期间被用户修改，本轮结果作废")
                _dump(node_dir / "blocks.json", {**record, "discarded": "执行期间被修改"})
                await self._close(page, tab)
                self._save("discard")
                continue
            if nrun.stop == "budget":
                tree.unsearch(node.id)                  # 节点中途因次数停下：退回待查（次数不退还）
                _dump(node_dir / "blocks.json", {**record, "discarded": "次数用满，节点退回待查"})
                await self._close(page, tab)
                self._save("budget_stop")
                continue
            keep_flag = self._apply(node, nrun, record)
            _dump(node_dir / "blocks.json", record)
            if node.state in ("key", "deleted") and not keep_flag:
                await self._close(page, tab)
            elif tab is not None:
                node.tab = tab
                self.kept += 1
            self._save("node")
            if nrun.stop == "finish":
                tree.finish("rule")
                self.log(f"[taskbook] {node.word}: 任务书「停」条件满足，结束")
                break
            if nrun.stop == "handoff":
                if self.trial:
                    break
                await self._wait_decision("decision", {"why": "任务书要求交给人", "node": node.word})
        self.current = None
        if self.state != "stopped":
            self.state = "done"
        self.result = build_result(tree, self.book, self.evidence, self.state)
        self.result.update({"trial": self.trial, "run": self.recorder.root.name})
        _dump(self.dir / "result.json", self.result)
        self._save("end")
        try:
            (self.dir / "report.html").write_text(report_html(tree, self.book, self.result, self.dir), encoding="utf-8")
        except Exception as e:                       # 报告失败不影响结果
            self.log(f"[taskbook] 报告生成失败：{e}")
        return self.result

    def _consume(self, k: int):
        self.tree.consume(k)
        self._save("read")

    def _apply(self, node, nrun: NodeRun, record: dict) -> bool:
        """按顺序应用改树操作（来源 rule）。返回是否有生效的操作要求保留标签页。"""
        tree = self.tree
        keep = False
        for op in nrun.ops:
            words = [w.text for w in op.words]
            why = {"step": op.step, "reason": f"{op.step} 改树", "evidence": words[:20]}
            add = op.op in ("add_to_current", "add_to_key")
            target = node.word if op.op == "add_to_current" else op.key
            rejected = tree.apply_rule(node.id, op.op, words if add else [], key=op.key,
                                       new_terminal=op.new_terminal, why=why)
            bad = {r.get("word") for r in rejected}
            applied = [w for w in op.words if w.text not in bad] if add else (op.words if not rejected else [])
            for r in rejected:
                node.reasons.append({"t": time.time(), "actor": "system", "op": "rejected", "step": op.step,
                                     "reason": r.get("rejected"), "word": r.get("word")})
                record["rejected"].append({**r, "step": op.step, "op": op.op})
            if add:
                for w in applied:
                    self.evidence[(target, w.text)] = {
                        "step": op.step, "text": w.text, "url": w.url, "element": w.element,
                        "fields": {k: {"text": v.text, "element": v.element, "url": v.url}
                                   for k, v in (w.fields or {}).items()}}
            if applied or (not add and not rejected):
                record["applied"].append({"op": op.op, "words": [w.text for w in applied], "key": target,
                                          "step": op.step, "new_terminal": op.new_terminal})
                keep = keep or op.keep_tab
        return keep

    async def _close(self, page, tab):
        if tab is None:
            return
        try:
            await page.close_tab(tab)
        except Exception as e:
            self.log(f"[taskbook] 关闭标签页失败：{e}")
