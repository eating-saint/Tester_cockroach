"""4b 判定（spec F13）：节点搜完后增减节点。判定以 4b 为准，程序只拦两类：引用无依据的候选、不允许的操作。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..config import vlm_settings
from ..vlm.client import OllamaClient
from ..vlm.extract import normalize
from ..vlm.prompts import PromptBook
from .spec import TaskType
from .tree import JUDGE_OPS, ExpandTree, ExNode

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"ops": {"type": "array", "items": {"type": "object", "properties": {
        "op": {"type": "string", "enum": list(JUDGE_OPS)}, "key": {"type": "string"},
        "from": {"type": "integer"}, "condition": {"type": "string"}, "reason": {"type": "string"}},
        "required": ["op"]}}},
    "required": ["ops"],
}
TRIES = 3


def _dump(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _parse(text: str):
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    obj = json.loads(t)
    if not isinstance(obj, dict) or not isinstance(obj.get("ops"), list):
        raise ValueError("需要 {\"ops\": [...]}")
    return obj["ops"]


def candidates_from(extractions: list[dict]) -> list[dict]:
    """子用例的抽取结果 → 候选清单（编号从 1 开始）。只有 field 为「候选」的项是候选。"""
    out = []
    for r in extractions:
        if r.get("field") == "候选":
            out.append({"no": len(out) + 1, "value": r.get("value", ""), "grounded": bool(r.get("grounded")),
                        "where": r.get("where"), "element": r.get("element")})
    return out


def check_ops(ops: list, node: ExNode, tree: ExpandTree, cands: list[dict]) -> tuple[list[dict], list[dict]]:
    by_no = {c["no"]: c for c in cands}
    ok, bad = [], []
    for raw in ops:
        if not isinstance(raw, dict):
            bad.append({"op": raw, "rejected": "不是对象"})
            continue
        op = dict(raw)
        kind = op.get("op")
        if kind not in JUDGE_OPS:
            bad.append({**op, "rejected": "不允许的操作"})
            continue
        if kind in ("add_value", "new_key"):
            c = by_no.get(op.get("from")) if isinstance(op.get("from"), int) else None
            if c is None:
                bad.append({**op, "rejected": "无依据：from 不是候选编号"})
                continue
            if not c["grounded"]:
                bad.append({**op, "rejected": "无依据：该候选在页面上查不到"})
                continue
            word = str(op.get("word") or c["value"]).strip()
            if normalize(word) != normalize(c["value"]):
                bad.append({**op, "rejected": "无依据：word 与候选值不一致"})
                continue
            if normalize(word) == normalize(node.word):
                bad.append({**op, "rejected": "候选就是当前词本身"})
                continue
            op["word"] = c["value"].strip()
            op["evidence"] = {"value": c["value"], "where": c["where"], "element": c["element"]}
            if kind == "add_value":
                key = str(op.get("key") or "")
                if key not in tree.mapping and key != node.word:
                    bad.append({**op, "rejected": "不允许的操作：key 不是已知的键，也不是当前词"})
                    continue
        ok.append(op)
    return ok, bad


class ExpandJudge:
    def __init__(self, config, client=None, prompts: PromptBook | None = None):
        self.s = vlm_settings(config)
        self.client = client if client is not None else OllamaClient(self.s)
        self.pb = prompts or PromptBook(self.s.get("prompts_dir"))

    def _render(self, node, tree, tt, cands, last_error: str) -> str:
        rows = "\n".join(f"{c['no']} | 候选 | {c['value']} | {'有依据' if c['grounded'] else '无依据'}"
                         for c in cands) or "（没有候选）"
        return self.pb.render("expand_judge", word=node.word, key=tree.key_of(node.id) or "（根）",
                              searches=node.searches, tree=tree.summary_text(),
                              conditions="\n".join(f"- {c.id}：{c.text}" for c in tt.conditions),
                              candidates=rows,
                              last_error=f"【上一次的问题】{last_error}，请改正后重新回答。" if last_error else "")

    async def judge(self, node: ExNode, tree: ExpandTree, cands: list[dict], tt: TaskType,
                    png: bytes | None, out_dir: Path) -> dict:
        system = self.pb.render("system")
        calls, err = [], ""
        for i in range(TRIES):
            user = self._render(node, tree, tt, cands, err)
            r = await self.client.chat(system, user, [png] if png else [], JUDGE_SCHEMA)
            calls.append({"user": user, "raw": r.text, "elapsed_ms": r.elapsed_ms})
            try:
                ops = _parse(r.text)
            except (ValueError, TypeError) as e:
                err = f"回答不是合法的 JSON（{type(e).__name__}: {e}）"
                continue
            ok, bad = check_ops(ops, node, tree, cands)
            res = {"ok": ok, "rejected": bad, "tries": i + 1, "failed": False}
            _dump(out_dir / "request.json", {"system": system, "calls": calls, "candidates": cands})
            _dump(out_dir / "checked.json", res)
            return res
        res = {"ok": [], "rejected": [], "tries": TRIES, "failed": True, "why": err}
        _dump(out_dir / "request.json", {"system": system, "calls": calls, "candidates": cands})
        _dump(out_dir / "checked.json", res)
        return res
