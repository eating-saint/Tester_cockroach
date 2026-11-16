"""ExpandRunner（spec F12–F16）：按展开规则挑节点 → 子用例交给现有 CaseRunner → 4b 判定 → 改树 → 循环。

- 次数用满：暂停（原因 budget），状态"等待决策"，不自行结束、不自行续跑。
- 人工改树只在暂停时进行（面板 / 命令行调用 ExpandTree 的用户操作）；节点在执行期间被改过，本轮结果作废。
- 末端叶子保留自己的搜索结果标签页（上限 keep_tabs）；变成键或被删除的节点关掉标签页。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..pause import PauseGate
from ..runner import CaseRunner
from ..status import STATUS
from . import subtask
from .judge import ExpandJudge, candidates_from
from .rank import rank
from .spec import TaskType
from .tree import ExpandTree


def _dump(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


class ExpandRunner:
    def __init__(self, browser, config, recorder, tt: TaskType, gate: PauseGate | None = None,
                 confirm_fn=None, on_change=None, log=print, input_fn=input, handoff_fn=None,
                 should_stop=None, before_action=None, client=None, humanizer_factory=None):
        self.browser = browser
        self.config = config
        self.recorder = recorder
        self.tt = tt
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
        self.tree = ExpandTree(tt.groups, tt.start, tt.budget)
        self.judge = ExpandJudge(config, client)
        self.state = "idle"                       # running / waiting / done / stopped
        self.current: str | None = None           # 正在执行的节点 id
        self.kept = 0
        self.dir = recorder.root / "expand"
        self._snap = 0
        self.result: dict | None = None

    # ---------------- 记录 ----------------
    def _save(self, why: str):
        self._snap += 1
        snap = {"why": why, "state": self.state, "current": self.current, **self.tree.snapshot()}
        _dump(self.dir / "snapshots" / f"{self._snap:04d}.json", snap)
        _dump(self.dir / "events.json", [e.to_dict() for e in self.tree.events])
        if self.on_change is not None:
            try:
                self.on_change(snap)
            except Exception:
                pass

    def snapshot(self) -> dict:
        return {"state": self.state, "current": self.current, "type": self.tt.to_dict(), **self.tree.snapshot()}

    # ---------------- 主循环 ----------------
    async def run(self) -> dict:
        from ..vlm.decider import VlmDecider
        _dump(self.dir / "type.json", self.tt.to_dict())
        page = self.browser.page or await self.browser.new_page()
        can_tab = all(hasattr(page, m) for m in ("new_tab", "close_tab"))
        self.state = "running"
        self._save("start")
        factory = VlmDecider.factory(self.config, self.client)
        tree = self.tree
        while True:
            await self.gate.wait()
            if self.should_stop():
                self.state = "stopped"
                break
            if tree.finished:
                break
            if tree.exhausted:
                self.state = "waiting"
                self.log(f"[expand] 已用 {tree.used} / {tree.budget} 次，停下等待决策")
                self.gate.pause("budget", {"used": tree.used, "budget": tree.budget})
                self._save("budget")
                await self.gate.wait()
                self.state = "running"
                continue
            node = tree.next_node()
            if node is None:
                break
            self.current = node.id
            v0 = node.version
            tab = None
            if can_tab and self.kept < self.tt.keep_tabs:
                try:
                    tab = await page.new_tab("about:blank")
                except Exception as e:                      # 开不了新标签页：就在当前标签页里做
                    self.log(f"[expand] 新标签页失败：{e}")
            case = subtask.build(node, tree, self.tt)
            tree.mark_searched(node.id)
            self._save("search")

            def hook(decision, result):
                if decision.tool in ("click", "open_tab") and result.ok and \
                        ({"url", "new_tab"} & set(result.changes or [])):
                    tree.consume(1)
                return not tree.exhausted

            runner = CaseRunner(self.browser, self.config, self.recorder, decider_factory=factory,
                                input_fn=self.input_fn, log=self.log, should_stop=self.should_stop,
                                before_action=self.before_action, handoff_fn=self.handoff_fn,
                                humanizer_factory=self.humanizer_factory, gate=self.gate,
                                confirm_fn=self.confirm_fn, budget_hook=hook)
            STATUS.set_phase("EXPAND")
            res = await runner.run_case(case)
            page = self.browser.page or page
            if node.version != v0 or node.state == "deleted":
                self.log(f"[expand] {node.word}: 执行期间被用户修改，本轮结果作废")
                await self._close(page, tab)
                self._save("discard")
                continue
            if res.root_summary == "budget" and tree.exhausted:      # 子用例因次数上限中途停下
                tree.unsearch(node.id)
                await self._close(page, tab)
                self._save("budget_stop")
                continue
            node.url = res.final_url
            shot = self._last_shot(case.name)
            node.shot = str(shot.relative_to(self.recorder.root)) if shot else None
            self._fields(node, res.extractions)
            cands = candidates_from(res.extractions)
            jres = await self.judge.judge(node, tree, cands, self.tt, shot.read_bytes() if shot else None,
                                          self.dir / "judge" / node.id)
            rejected = tree.apply(node.id, jres["ok"], "model") if jres["ok"] else []
            if jres["failed"]:
                node.reasons.append({"t": time.time(), "actor": "system", "op": "judge_failed",
                                     "reason": jres.get("why", "")})
            for r in jres["rejected"] + rejected:
                node.reasons.append({"t": time.time(), "actor": "system", "op": "rejected",
                                     "reason": r.get("rejected"), "detail": {k: v for k, v in r.items()
                                                                             if k != "rejected"}})
            if node.state in ("key", "deleted"):
                await self._close(page, tab)
            elif tab is not None:
                node.tab = tab
                self.kept += 1
            self._save("judge")
        self.current = None
        if self.state != "stopped":
            self.state = "done"
        self.result = self.build_result()
        _dump(self.dir / "result.json", self.result)
        self._save("end")
        try:
            (self.dir / "expand.html").write_text(self.report_html(), encoding="utf-8")
        except Exception as e:                       # 报告失败不影响结果
            self.log(f"[expand] 报告生成失败：{e}")
        return self.result

    def report_html(self) -> str:
        """展开过程页：变化时间线、最终树、结果表（spec F17）。"""
        import html as H
        e = H.escape
        t, res = self.tree, self.result or {}
        names = {"pending": "待查", "key": "键", "tentative": "暂定", "terminal": "末端叶子", "deleted": "已删除"}

        def node_li(nid):
            n = t.nodes[nid]
            kids = "".join(node_li(c) for c in n.children)
            last = next((r for r in reversed(n.reasons) if r.get("reason")), None)
            shot = f' <a href="../{e(n.shot)}">截图</a>' if n.shot else ""
            return (f"<li class='{n.state}'>{e(n.word)} <small>[{names.get(n.state, n.state)}·{e(n.origin)}·搜 {n.searches}]"
                    f"{(' ' + e(str(last['reason']))) if last else ''}</small>{shot}"
                    + (f"<ul>{kids}</ul>" if kids else "") + "</li>")
        roots = "".join(node_li(n.id) for n in t.nodes.values() if n.parent is None)
        rows = "".join(f"<tr><td>{i}</td><td>{e(s['why'])}</td><td>{s['leaves']}</td><td>{s['nodes']}</td>"
                       f"<td>{s['used']} / {s['budget']}</td></tr>" for i, s in enumerate(t.series, 1))
        ev = "".join(f"<tr><td>{e(time.strftime('%H:%M:%S', time.localtime(x.t)))}</td><td>{e(x.actor)}</td>"
                     f"<td>{e(x.op)}</td><td>{e(t.nodes[x.node].word if x.node in t.nodes else '')}</td>"
                     f"<td><code>{e(json.dumps(x.detail, ensure_ascii=False, default=str))[:160]}</code></td></tr>"
                     for x in t.events if x.op not in ("create",))
        rank_html = ""
        for g, items in ((res.get("rank") or {}).get("top") or {}).items():
            rank_html += f"<h3>{e(g or '结果')}</h3><table><tr><th>#</th><th>名称</th><th>价位</th><th>评分</th><th>网址</th></tr>" + "".join(
                f"<tr><td>{i}</td><td>{e(str(x['name']))}</td><td>{e(str(x.get('price_text') or '未知'))}</td>"
                f"<td>{e(str(x.get('rating_text') or '-'))}</td><td>{e(str(x.get('url') or ''))}</td></tr>"
                for i, x in enumerate(items, 1)) + "</table>"
        bad = (res.get("rank") or {}).get("ungrounded") or []
        if bad:
            rank_html += "<p>无依据（未参与排序）：" + "、".join(e(str(x["name"])) for x in bad) + "</p>"
        return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>展开过程 {e(self.tt.title)}</title>
<style>body{{font:14px sans-serif;margin:24px}} table{{border-collapse:collapse;margin:8px 0}} td,th{{border:1px solid #ccc;padding:3px 8px}}
li.terminal{{color:#1a7f4b}} li.key{{color:#2f6fd6}} li.deleted{{color:#999;text-decoration:line-through}} small{{color:#666}}</style></head><body>
<h1>展开过程：{e(self.tt.title)}</h1>
<p>状态 {e(str(res.get('state')))}；{e(t.summary_text())}</p>
<h2>结果</h2>{rank_html or '<p>末端叶子 %d 个（见下方树）</p>' % len(res.get('leaves', []))}
<h2>最终的树</h2><ul>{roots}</ul>
<h2>变化时间线</h2><table><tr><th>#</th><th>原因</th><th>末端叶子</th><th>节点</th><th>已用</th></tr>{rows}</table>
<h2>事件（含用户修改）</h2><table><tr><th>时间</th><th>谁</th><th>操作</th><th>节点</th><th>详情</th></tr>{ev}</table>
</body></html>"""

    async def _close(self, page, tab):
        if tab is None:
            return
        try:
            await page.close_tab(tab)
        except Exception as e:
            self.log(f"[expand] 关闭标签页失败：{e}")

    def _last_shot(self, case_name: str):
        d = self.recorder.root / case_name
        shots = sorted(d.glob("step_*/screenshot.png"))
        return shots[-1] if shots else None

    def _fields(self, node, rows):
        want = list(self.tt.output.get("fields") or [])
        for r in rows or []:
            if r.get("field") in want and r.get("field") not in node.fields:
                node.fields[r["field"]] = {"value": r.get("value", ""), "grounded": bool(r.get("grounded"))}

    # ---------------- 结果 ----------------
    def build_result(self) -> dict:
        tree = self.tree
        leaves = []
        for n in tree.leaves():
            last = next((r for r in reversed(n.reasons) if r.get("actor") in ("model", "user")), None)
            leaves.append({"id": n.id, "word": n.word, "key": tree.key_of(n.id), "url": n.url, "shot": n.shot,
                           "fields": n.fields, "origin": n.origin,
                           "reason": (last or {}).get("reason"), "evidence": (last or {}).get("evidence")})
        out = {"type": self.tt.name, "state": self.state, "counts": tree.counts(), "leaves": leaves,
               "tabs": [{"node": n.id, "word": n.word, "tab": str(n.tab), "url": n.url}
                        for n in tree.nodes.values() if n.tab is not None and n.state != "deleted"],
               "edits": [e.to_dict() for e in tree.events if e.actor == "user"],
               "mapping": tree.mapping}
        o = self.tt.output
        if o.get("type") == "ranked":
            fields = list(o.get("fields") or [])
            name_f = fields[0] if fields else "店名"
            price_f = next((f for f in fields if "价" in f or "price" in f.lower()), None)
            rate_f = next((f for f in fields if "评" in f or "rating" in f.lower()), None)
            items = []
            for lf in leaves:
                f = lf["fields"]
                if not f:
                    continue
                nm = f.get(name_f, {}).get("value") or lf["word"]
                grounded = all(v.get("grounded") for v in f.values())
                items.append({"name": nm, "price_text": f.get(price_f, {}).get("value") if price_f else None,
                              "rating_text": f.get(rate_f, {}).get("value") if rate_f else None,
                              "grounded": grounded, "group": lf["key"], "url": lf["url"], "node": lf["id"]})
            out["rank"] = rank(items, list(o.get("sort") or []), int(o.get("n", 5)),
                               group_by=o.get("group_by"))
        return out
