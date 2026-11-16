"""关联展开树（spec F12、F14、F15）：纯逻辑，不碰浏览器。

展开规则（plan.md「关联展开：树」）：
1. 根队列 = [起始词] + 其余组的键（按定义顺序）。逐个根处理；一个根的子树按深度优先、组内按值的先后找待查节点。
2. 节点搜过后：词是已知键且不在自己的上级链上 → 分叉（按映射生成子节点）；在上级链上 → 末端叶子（循环）；否则暂定叶子。
3. 已经作为根或分叉展开过的键，不再单独作为根跑一遍。
4. 运行中 new_key 产生的键排到当前根之后（多个按产生顺序）；该键的值等那个根轮到时再展开。
5. 同一个词在不同键下是不同节点，各自搜索、各自判定（每次出现都复查）。
6. 一个根的子树没有待查节点时，其中的暂定叶子统一转为末端叶子。
7. 结束：没有待处理的根、没有待查和暂定节点。次数用满（used ≥ budget）由运行器停下等人。
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

STATES = ("pending", "key", "tentative", "terminal", "deleted")
JUDGE_OPS = ("add_value", "new_key", "terminal", "delete")


@dataclass
class ExNode:
    id: str
    word: str
    parent: str | None
    state: str = "pending"
    origin: str = "initial"          # initial / model / user
    searches: int = 0
    reasons: list[dict] = field(default_factory=list)
    fields: dict = field(default_factory=dict)
    tab: object = None
    url: str | None = None
    shot: str | None = None
    children: list[str] = field(default_factory=list)
    version: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tab"] = None if self.tab is None else str(self.tab)
        return d


@dataclass
class ExEvent:
    t: float
    actor: str                       # model / user / system
    op: str
    node: str | None
    detail: dict

    def to_dict(self) -> dict:
        return asdict(self)


class ExpandTree:
    def __init__(self, groups: list[tuple[str, list[str]]], start: str, budget: int = 120):
        self.mapping: dict[str, list[str]] = {}
        self.origin_of: dict[tuple[str, str], str] = {}
        for k, vs in groups:
            for v in vs:
                self._map_add(k, v, "initial")
        self.start = start
        self.budget = int(budget)
        self.used = 0
        self.nodes: dict[str, ExNode] = {}
        self.events: list[ExEvent] = []
        self.series: list[dict] = []
        self.expanded: set[str] = set()       # 已作为根或分叉展开过的键
        self.roots: list[dict] = [{"key": start, "node": None, "status": "pending"}]
        for k, _ in groups:
            if k != start:
                self.roots.append({"key": k, "node": None, "status": "pending"})
        self._seq = 0
        self._cur_root: int | None = None
        self._insert_at: int | None = None
        self.finished_by_user = False
        self._changed("init")

    # ---------------- 内部 ----------------
    def _map_add(self, key: str, value: str, origin: str) -> bool:
        vs = self.mapping.setdefault(key, [])
        if value in vs or value == key:
            return False
        vs.append(value)
        self.origin_of[(key, value)] = origin
        return True

    def _new(self, word: str, parent: str | None, origin: str) -> ExNode:
        self._seq += 1
        n = ExNode(f"x{self._seq}", word, parent, origin=origin)
        self.nodes[n.id] = n
        if parent is not None:
            self.nodes[parent].children.append(n.id)
        self._event("system" if origin != "user" else "user", "create", n.id, {"word": word, "parent": parent})
        return n

    def _event(self, actor: str, op: str, node: str | None, detail: dict | None = None):
        self.events.append(ExEvent(time.time(), actor, op, node, dict(detail or {})))

    def _changed(self, why: str):
        c = self.counts()
        self.series.append({"t": time.time(), "why": why, **c})

    def ancestors(self, nid: str) -> list[ExNode]:
        out, cur = [], self.nodes[nid].parent
        while cur is not None:
            out.append(self.nodes[cur])
            cur = self.nodes[cur].parent
        return out

    def key_of(self, nid: str) -> str | None:
        p = self.nodes[nid].parent
        return self.nodes[p].word if p else None

    def _expand(self, n: ExNode, origin_default: str = "initial"):
        """按映射给键节点生成子节点（已有的同名子节点不重复生成）。"""
        have = {self.nodes[c].word for c in n.children if self.nodes[c].state != "deleted"}
        for v in self.mapping.get(n.word, []):
            if v not in have:
                self._new(v, n.id, self.origin_of.get((n.word, v), origin_default))
        self.expanded.add(n.word)

    def _subtree(self, nid: str) -> list[ExNode]:
        out, stack = [], [nid]
        while stack:
            cur = self.nodes[stack.pop()]
            out.append(cur)
            stack.extend(reversed(cur.children))
        return out

    def _first_pending(self, nid: str) -> ExNode | None:
        for n in self._subtree(nid):                  # 前序遍历 = 深度优先、组内按先后
            if n.state == "pending":
                return n
        return None

    def _finalize(self, nid: str):
        for n in self._subtree(nid):
            if n.state == "tentative":
                n.state = "terminal"
                n.reasons.append({"t": time.time(), "actor": "system", "op": "terminal",
                                  "reason": "所在键已全部搜完，暂定叶子转为末端叶子"})
                self._event("system", "terminal", n.id, {"why": "finalize"})

    # ---------------- 挑节点 ----------------
    def next_node(self) -> ExNode | None:
        for i, r in enumerate(self.roots):
            if r["status"] == "skipped":
                continue
            if r["status"] == "pending":
                if r["node"] is None:
                    if r["key"] in self.expanded:
                        r["status"] = "skipped"            # 已在别处展开过，不再单独作为根
                        self._event("system", "skip_root", None, {"key": r["key"]})
                        continue
                    r["node"] = self._new(r["key"], None, "initial").id
                else:                                       # 运行中建出的新键：这时才展开它的值
                    n = self.nodes[r["node"]]
                    if n.state != "deleted":
                        self._expand(n, "model")
                r["status"] = "active"
            if self.nodes[r["node"]].state == "deleted":
                r["status"] = "done"
                continue
            self._cur_root = i
            self._insert_at = i + 1 if self._insert_at is None or self._insert_at <= i else self._insert_at
            n = self._first_pending(r["node"])
            if n is not None:
                return n
            if r["status"] != "done":
                self._finalize(r["node"])
                r["status"] = "done"
                self._insert_at = None
                self._changed("root_done")
        return None

    def mark_searched(self, nid: str) -> ExNode:
        n = self.nodes[nid]
        self.used += 1
        n.searches += 1
        self._event("system", "search", nid, {"word": n.word, "used": self.used})
        if n.word in self.mapping:
            if any(a.word == n.word for a in self.ancestors(nid)):
                n.state = "terminal"
                n.reasons.append({"t": time.time(), "actor": "system", "op": "terminal", "reason": "循环"})
                self._event("system", "terminal", nid, {"why": "cycle"})
            else:
                n.state = "key"
                self._expand(n)
                self._event("system", "key", nid, {"children": [self.nodes[c].word for c in n.children]})
        else:
            n.state = "tentative"
        self._changed("search")
        return n

    def unsearch(self, nid: str):
        """子用例因次数上限中途停下：节点回到待查（次数不退还）。"""
        n = self.nodes[nid]
        for c in list(n.children):
            for x in self._subtree(c):
                x.state = "deleted"
        n.children = []
        n.state = "pending"
        self._event("system", "set_pending", nid, {"why": "budget"})

    def consume(self, k: int = 1):
        self.used += k
        self._event("system", "read", None, {"n": k, "used": self.used})
        self._changed("read")

    # ---------------- 判定 ----------------
    def apply(self, nid: str, ops: list[dict], actor: str = "model") -> list[dict]:
        """应用 4b 的判定，返回被拒的项 [{op, reason}]。调用方已做过"有依据"检查。"""
        n = self.nodes[nid]
        rejected = []
        for op in ops:
            kind = op.get("op")
            why = {"t": time.time(), "actor": actor, "op": kind, "reason": op.get("reason", ""),
                   "evidence": op.get("evidence"), "condition": op.get("condition")}
            if kind not in JUDGE_OPS:
                rejected.append({**op, "rejected": "不允许的操作"})
                continue
            if n.state == "deleted":
                rejected.append({**op, "rejected": "节点已删除"})
                continue
            if kind == "terminal":
                if n.state in ("tentative", "pending"):
                    n.state = "terminal"
                n.reasons.append(why)
                self._event(actor, "terminal", nid, {"reason": why["reason"]})
            elif kind == "delete":
                for x in self._subtree(nid):
                    x.state = "deleted"
                n.reasons.append(why)
                self._event(actor, "delete", nid, {"reason": why["reason"]})
            elif kind == "add_value":
                key, word = str(op.get("key") or ""), str(op.get("word") or "").strip()
                if not word or (key not in self.mapping and key != n.word):
                    rejected.append({**op, "rejected": "key 不是已知的键，也不是当前词"})
                    continue
                if key == n.word and n.state != "key":
                    self._new_key(n, word, actor, why)
                    continue
                if not self._map_add(key, word, actor):
                    rejected.append({**op, "rejected": "这个值已经在该键下"})
                    continue
                for k in [x for x in self.nodes.values() if x.word == key and x.state == "key"]:
                    if k.word in self.expanded:
                        self._new(word, k.id, actor)
                n.reasons.append(why)
                self._event(actor, "add_value", nid, {"key": key, "word": word})
            elif kind == "new_key":
                word = str(op.get("word") or "").strip()
                if not word:
                    rejected.append({**op, "rejected": "word 为空"})
                    continue
                self._new_key(n, word, actor, why)
        self._changed("judge")
        return rejected

    def _new_key(self, n: ExNode, word: str, actor: str, why: dict):
        added = self._map_add(n.word, word, actor)
        n.reasons.append(why)
        if n.state != "key":
            n.state = "key"
            pos = self._insert_at if self._insert_at is not None else len(self.roots)
            self.roots.insert(pos, {"key": n.word, "node": n.id, "status": "pending"})
            self._insert_at = pos + 1
        elif added and n.word in self.expanded:
            self._new(word, n.id, actor)
        self._event(actor, "new_key", n.id, {"key": n.word, "word": word})

    # ---------------- 用户操作（面板 / 下一章手机共用） ----------------
    def _touch(self, nid: str):
        self.nodes[nid].version += 1
        for a in self.ancestors(nid):
            a.version += 1

    def delete(self, nid: str, actor: str = "user"):
        for x in self._subtree(nid):
            x.state = "deleted"
        self._touch(nid)
        self._event(actor, "delete", nid, {"word": self.nodes[nid].word})
        self._changed("edit")

    def set_terminal(self, nid: str, actor: str = "user"):
        n = self.nodes[nid]
        for c in n.children:
            for x in self._subtree(c):
                x.state = "deleted"
        n.state = "terminal"
        n.origin = n.origin if n.origin != "initial" else n.origin
        self._touch(nid)
        self._event(actor, "set_terminal", nid, {"word": n.word})
        self._changed("edit")

    def set_pending(self, nid: str, actor: str = "user"):
        n = self.nodes[nid]
        for c in n.children:
            for x in self._subtree(c):
                x.state = "deleted"
        n.children = []
        n.state = "pending"
        self._touch(nid)
        for r in self.roots:                   # 所在根如果已经结束，重新打开
            if r["node"] is not None and (r["node"] == nid or any(a.id == r["node"] for a in self.ancestors(nid))):
                r["status"] = "active"
        self._event(actor, "set_pending", nid, {"word": n.word})
        self._changed("edit")

    def add_group(self, key: str, values: list[str], actor: str = "user"):
        key = key.strip()
        new = [v.strip() for v in values if v.strip() and self._map_add(key, v.strip(), actor)]
        if key in self.expanded:
            for k in [x for x in self.nodes.values() if x.word == key and x.state == "key"]:
                for v in new:
                    self._new(v, k.id, actor)
                for r in self.roots:
                    if r["node"] is not None and (r["node"] == k.id or any(a.id == r["node"] for a in self.ancestors(k.id))):
                        r["status"] = "active"
        elif not any(r["key"] == key for r in self.roots):
            self.roots.append({"key": key, "node": None, "status": "pending"})
        self._event(actor, "add_group", None, {"key": key, "values": new})
        self._changed("edit")

    def grant(self, k: int, actor: str = "user"):
        self.budget += int(k)
        self._event(actor, "grant", None, {"n": int(k), "budget": self.budget})
        self._changed("grant")

    def finish(self, actor: str = "user"):
        self.finished_by_user = True
        self._event(actor, "finish", None, {})

    # ---------------- 查询 ----------------
    @property
    def exhausted(self) -> bool:
        return self.used >= self.budget

    @property
    def finished(self) -> bool:
        if self.finished_by_user:
            return True
        if any(r["status"] in ("pending",) for r in self.roots):
            return False
        live = [n for n in self.nodes.values() if n.state != "deleted"]
        return bool(live) and not any(n.state in ("pending", "tentative") for n in live)

    def counts(self) -> dict:
        live = [n for n in self.nodes.values() if n.state != "deleted"]
        by = {s: sum(1 for n in live if n.state == s) for s in STATES if s != "deleted"}
        return {"nodes": len(live), "leaves": by["terminal"], "pending": by["pending"], "tentative": by["tentative"],
                "keys": by["key"], "used": self.used, "budget": self.budget}

    def leaves(self) -> list[ExNode]:
        return [n for n in self.nodes.values() if n.state == "terminal"]

    def summary_text(self, limit: int = 60) -> str:
        keys = "；".join(f"{k} <{', '.join(v)}>" for k, v in list(self.mapping.items())[:limit])
        c = self.counts()
        return (f"已知键值：{keys}\n节点 {c['nodes']} 个：键 {c['keys']}、末端叶子 {c['leaves']}、"
                f"暂定叶子 {c['tentative']}、待查 {c['pending']}；已用 {c['used']} / {c['budget']} 次")

    def snapshot(self) -> dict:
        return {"nodes": [n.to_dict() for n in self.nodes.values()], "roots": [dict(r) for r in self.roots],
                "mapping": {k: list(v) for k, v in self.mapping.items()}, "counts": self.counts(),
                "series": list(self.series), "finished": self.finished, "exhausted": self.exhausted}
