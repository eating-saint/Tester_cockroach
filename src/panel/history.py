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
        rj = d / "report.json"
        if not rj.exists():
            continue
        try:
            data = json.loads(rj.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        results = data.get("results") or []
        parts = d.name.split("-", 3)
        label = parts[3] if len(parts) > 3 else ""
        when = f"{parts[0][:4]}-{parts[0][4:6]}-{parts[0][6:8]} {parts[1][:2]}:{parts[1][2:4]}:{parts[1][4:6]}" \
            if len(parts) >= 2 and len(parts[0]) == 8 and len(parts[1]) == 6 else d.name
        out.append({"dir": d.name, "time": when, "label": label,
                    "passed": sum(1 for r in results if r.get("passed")), "total": len(results),
                    "has_html": (d / "report.html").exists(),
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
