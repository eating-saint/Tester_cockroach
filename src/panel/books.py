"""任务书服务（面板与平板共用）：列表、读取、校验、导入（旧格式自动转换）、保存（版本冲突检查、备份）、预览、指导提示词、试跑记录。"""
from __future__ import annotations

import json
from pathlib import Path

from hands.taskbook import book as B
from hands.taskbook import guide as G
from hands.taskbook.convert import detect, from_legacy
from hands.taskbook.validate import Problem, check_text

HIDDEN_PARAMS = ("BASE",)          # 夹具地址由面板自动填


def _problems(ps: list[Problem]) -> list[dict]:
    return [p.to_dict() for p in ps]


class BookService:
    def __init__(self, tasks_dir):
        self.dir = Path(tasks_dir)

    def _path(self, name: str) -> Path:
        name = (name or "").strip()
        if not B.NAME_RE.match(name):
            raise ValueError("任务书名称只能用字母、数字、汉字、下划线和连字符")
        return self.dir / f"{name}.yaml"

    # ---------------- 读 ----------------
    def list(self) -> list[dict]:
        out = []
        for p in sorted(self.dir.glob("*.yaml")) if self.dir.exists() else []:
            text = p.read_text(encoding="utf-8")
            kind = detect(text)
            if kind == "legacy":
                new, probs = from_legacy(text)
                b, _ = check_text(new, p.stem)
                status, probs_n = "needs_rewrite", len(probs)
            else:
                b, probs = check_text(text, p.stem)
                status, probs_n = (b.status if b else "draft"), len(probs)
            out.append({"name": p.stem, "title": (b.title if b else "") or p.stem, "status": status,
                        "rev": B.rev_of(text), "problems": probs_n, "legacy": kind == "legacy",
                        "params": [x for x in (b.params if b else []) if x.get("name") not in HIDDEN_PARAMS],
                        "budget": b.limits.get("budget") if b else None,
                        "trial_budget": b.limits.get("trial_budget") if b else None,
                        "start": b.start if b else "", "uses_4b": _uses_4b(b)})
        return out

    def get(self, name: str) -> dict:
        p = self._path(name)
        if not p.exists():
            raise FileNotFoundError(name)
        text = p.read_text(encoding="utf-8")
        return {"name": p.stem, "text": text, "rev": B.rev_of(text), **self.validate(text, p.stem)}

    def validate(self, text: str, name: str | None = None) -> dict:
        b, probs = check_text(text or "", name)
        return {"status": b.status if b else "draft", "problems": _problems(probs),
                "book": b.json() if b else None}

    # ---------------- 导入、保存 ----------------
    def import_(self, text: str) -> dict:
        """粘贴的文本或上传的文件内容：旧格式自动转换。不落盘。"""
        kind = detect(text or "")
        if kind == "legacy":
            text, _ = from_legacy(text)
        res = self.validate(text)
        return {"text": text, "legacy": kind == "legacy", **res}

    def save(self, name: str, text: str, rev: str | None, save_as: bool = False) -> dict:
        b, probs = check_text(text or "", name)
        if b is None:
            raise ValueError("任务书读不懂，不能保存：" + "；".join(str(p) for p in probs))
        self._path(name)
        new_rev = B.save(self.dir, name, text, rev, save_as)
        return {"name": name, "rev": new_rev, "status": b.status, "problems": _problems(probs)}

    # ---------------- 预览 ----------------
    def preview(self, text: str, limit: int = 200) -> dict:
        """不联网：初始树和处理顺序（每个节点都当作处理后没有改树）。"""
        from hands.expand.tree import ExpandTree
        b, probs = check_text(text or "")
        if b is None:
            raise ValueError("；".join(str(p) for p in probs))
        if not b.groups or not b.start:
            raise ValueError("要先填初始组和起始词")
        t = ExpandTree(b.groups, b.start, 10 ** 6)
        initial = t.snapshot()
        order = []
        for _ in range(limit):
            n = t.next_node()
            if n is None:
                break
            t.mark_searched(n.id, count=False)
            k = t.key_of(n.id)
            order.append({"node": n.id, "word": n.word, "key": k, "label": f"{k}/{n.word}" if k else n.word})
        return {"initial": initial, "order": order, "final": t.snapshot(), "problems": _problems(probs)}

    def guide(self) -> str:
        return G.build(self.dir if self.dir.exists() else None)

    # ---------------- 运行记录 ----------------
    @staticmethod
    def steps(run_root: Path) -> list[dict]:
        """一次运行每个节点的记录（每次消耗）：积木产出、滤掉的项、树的变化、截图网址。"""
        out = []
        d = Path(run_root) / "book" / "nodes"
        for f in sorted(d.glob("*/blocks.json")) if d.exists() else []:
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            base = f"/runs/{Path(run_root).name}/book/nodes/{f.parent.name}/"
            for blk in rec.get("blocks", []):
                blk["shot_urls"] = [base + s for s in blk.get("shots", [])]
            rec["dir"] = f.parent.name
            out.append(rec)
        return out


def _uses_4b(b) -> bool:
    if b is None:
        return False
    from hands.taskbook.runner import uses_4b
    return uses_4b(b.flow)
