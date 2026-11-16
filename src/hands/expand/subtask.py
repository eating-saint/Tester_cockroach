"""节点 → 子用例：交给现有 CaseRunner 执行（搜索、必要时打开阅读、用 extract 列出候选）。"""
from __future__ import annotations

import re
from urllib.parse import quote_plus

from ..runner import CaseSpec, Expect
from ..vlm.prompts import PromptBook
from .spec import TaskType
from .tree import ExpandTree, ExNode


def query_of(node: ExNode, tree: ExpandTree, tt: TaskType) -> str:
    key = tree.key_of(node.id) or ""
    q = tt.query.replace("{value}", node.word).replace("{key}", key)
    return re.sub(r"\s+", " ", q).strip()


def build(node: ExNode, tree: ExpandTree, tt: TaskType, prompts: PromptBook | None = None) -> CaseSpec:
    pb = prompts or PromptBook()
    key = tree.key_of(node.id) or "（根）"
    q = query_of(node, tree, tt)
    fields = list(tt.output.get("fields") or []) if tt.output.get("type") == "ranked" else []
    field_hint = (f"如果「{node.word}」本身是一家餐厅 / 一个条目，再抽它自己的 " + "、".join(fields) +
                  "（field 写字段名，value 逐字抄页面；只抽这一家的）") if fields else ""
    goal = pb.render("expand_node", word=node.word, key=key, query=q,
                     sources="\n".join(f"- {s}" for s in tt.sources) or "- 搜索结果",
                     conditions="\n".join(f"- {c.id}：{c.text}" for c in tt.conditions),
                     reads=tt.read_per_node, field_hint=field_hint)
    safe = re.sub(r"[^\w一-鿿-]+", "_", node.word)[:24]
    return CaseSpec(
        name=f"{node.id}_{safe}", start_url=tt.search_url.replace("{q}", quote_plus(q)),
        root_goal=goal, root_done_when="已用 extract 列出候选并 complete",
        limits={"max_depth": 1, "max_steps": tt.node_steps}, expect=Expect(),
        timeout_s=240, brief={"hints": [node.word] + ([key] if key != "（根）" else []),
                              "extract_fields": ["候选"] + fields})
