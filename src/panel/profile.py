"""自动化 Chrome 专用用户目录：查看位置与占用、清空（浏览器停止时才允许）。"""
from __future__ import annotations

import os
import shutil
from pathlib import Path


def dir_size(path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def info(profile_dir) -> dict:
    p = Path(profile_dir)
    return {"path": str(p), "exists": p.exists(), "bytes": dir_size(p) if p.exists() else 0}


def clear(profile_dir, browser_running: bool, confirm: str) -> tuple[bool, str]:
    if browser_running:
        return False, "浏览器运行中，先停止浏览器再清空"
    if confirm != "CLEAR":
        return False, "需要二次确认"
    p = Path(profile_dir)
    if p.exists():
        shutil.rmtree(p, ignore_errors=False)
    p.mkdir(parents=True, exist_ok=True)
    return True, "已清空"
