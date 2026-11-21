"""运行历史：扫描 runs/*/report.json。"""
from __future__ import annotations

import json
from pathlib import Path


def list_runs(runs_dir, limit: int = 200) -> list[dict]:
    root = Path(runs_dir)
    if not root.exists():
        return []
    out = []
    for d in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True):
        parts = d.name.split("-", 3)
        label = parts[3] if len(parts) > 3 else ""
        when = f"{parts[0][:4]}-{parts[0][4:6]}-{parts[0][6:8]} {parts[1][:2]}:{parts[1][2:4]}:{parts[1][4:6]}" \
            if len(parts) >= 2 and len(parts[0]) == 8 and len(parts[1]) == 6 else d.name
        bj = d / "book" / "result.json"
        if bj.exists():                              # 任务书运行
            try:
                data = json.loads(bj.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            ranked = data.get("ranked") or {}
            out.append({"dir": d.name, "time": when, "label": label, "kind": "book", "trial": bool(data.get("trial")),
                        "title": data.get("title") or data.get("name"), "state": data.get("state"),
                        "leaves": len(data.get("leaves") or []), "passed": 1 if data.get("state") == "done" else 0,
                        "total": 1, "has_html": (d / "book" / "report.html").exists(), "report": "book/report.html",
                        "top": {g: [x.get("word") for x in rows] for g, rows in (ranked.get("top") or {}).items()},
                        "names": [x.get("word") for x in (data.get("leaves") or [])][:8]})
        else:
            rj = d / "report.json"
            if not rj.exists():
                continue
            try:
                data = json.loads(rj.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            results = data.get("results") or []
            out.append({"dir": d.name, "time": when, "label": label, "kind": "case",
                        "passed": sum(1 for r in results if r.get("passed")), "total": len(results),
                        "has_html": (d / "report.html").exists(), "report": "report.html",
                        "names": [r.get("name") for r in results][:5]})
        if len(out) >= limit:
            break
    return out


def safe_path(runs_dir, rel: str) -> Path | None:
    """把 /runs/<rel> 映射到 runs 目录内的文件；越界或不存在返回 None。"""
    root = Path(runs_dir).resolve()
    try:
        p = (root / rel).resolve()
    except (OSError, ValueError):
        return None
    if root not in p.parents or not p.is_file():
        return None
    return p
