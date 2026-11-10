"""M9 任务树：母/子任务、深度与步数上限、子任务结束后只把摘要交回父节点。纯逻辑。"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field


@dataclass
class TreeEvent:
    t: float
    kind: str        # create / complete / fail / force_fail
    node_id: str
    payload: dict

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TaskNode:
    id: str
    parent_id: str | None
    goal: str
    done_when: str
    depth: int
    status: str = "running"          # running / done / failed
    steps_used: int = 0
    summary: str | None = None
    child_summaries: list[dict] = field(default_factory=list)
    context: list[dict] | None = field(default_factory=list)


@dataclass
class TaskView:
    node_id: str
    goal: str
    done_when: str
    depth: int
    steps_used: int
    max_steps: int
    child_summaries: list[dict]
    recent_steps: list[dict]

    def to_dict(self) -> dict:
        return asdict(self)


class TaskTree:
    RECENT = 5

    def __init__(self, root_goal: str, root_done_when: str = "", max_depth: int = 3, max_steps: int = 20):
        self.max_depth = max_depth
        self.max_steps = max_steps
        self.nodes: dict[str, TaskNode] = {}
        self.events: list[TreeEvent] = []
        self.finished = False
        self._seq = 0
        root = self._new_node(None, root_goal, root_done_when, 0, "root")
        self.active_id = root.id

    # ---- 内部 ----
    def _new_node(self, parent_id, goal, done_when, depth, node_id=None) -> TaskNode:
        if node_id is None:
            self._seq += 1
            node_id = f"n{self._seq}"
        n = TaskNode(node_id, parent_id, goal, done_when, depth)
        self.nodes[node_id] = n
        self._event("create", node_id, {"goal": goal, "done_when": done_when, "parent": parent_id})
        return n

    def _event(self, kind, node_id, payload):
        self.events.append(TreeEvent(time.time(), kind, node_id, payload))

    def _end(self, status: str, text: str, kind: str):
        if self.finished:
            raise RuntimeError("tree already finished")
        n = self.active
        n.status = status
        n.summary = text
        n.context = None
        self._event(kind, n.id, {"summary": text} if status == "done" else {"reason": text})
        if n.parent_id is None:
            self.finished = True
        else:
            parent = self.nodes[n.parent_id]
            parent.child_summaries.append({"goal": n.goal, "status": status, "summary": text})
            self.active_id = parent.id

    # ---- 对外 ----
    @property
    def active(self) -> TaskNode:
        return self.nodes[self.active_id]

    @property
    def root(self) -> TaskNode:
        return self.nodes["root"]

    def create_child(self, goal: str, done_when: str = "") -> dict:
        cur = self.active
        if cur.depth + 1 > self.max_depth:
            return {"ok": False, "error": "max_depth"}
        n = self._new_node(cur.id, goal, done_when, cur.depth + 1)
        self.active_id = n.id
        return {"ok": True, "node_id": n.id}

    def complete(self, summary: str) -> dict:
        nid = self.active_id
        self._end("done", summary, "complete")
        return {"ok": True, "node_id": nid}

    def fail(self, reason: str) -> dict:
        nid = self.active_id
        self._end("failed", reason, "fail")
        return {"ok": True, "node_id": nid}

    def record_step(self, step: dict, node_id: str | None = None):
        n = self.nodes[node_id] if node_id else self.active
        if n.context is not None:
            n.context.append(step)

    def tick_step(self, node_id: str | None = None):
        """给执行该步的节点计数；若该节点仍在运行且达上限则强制失败。"""
        n = self.nodes[node_id] if node_id else self.active
        n.steps_used += 1
        if n.status == "running" and n.id == self.active_id and n.steps_used >= self.max_steps and not self.finished:
            self._end("failed", "step_limit", "force_fail")

    def view(self) -> TaskView:
        n = self.active
        recent = (n.context or [])[-self.RECENT:]
        return TaskView(n.id, n.goal, n.done_when, n.depth, n.steps_used, self.max_steps,
                        list(n.child_summaries), list(recent))

    def shape(self) -> list[str]:
        def path(n: TaskNode) -> str:
            parts = []
            while n is not None:
                parts.append("root" if n.parent_id is None else n.goal)
                n = self.nodes.get(n.parent_id) if n.parent_id else None
            return "/".join(reversed(parts))
        return [path(n) for n in self.nodes.values()]

    def events_json(self) -> list[dict]:
        return [e.to_dict() for e in self.events]
