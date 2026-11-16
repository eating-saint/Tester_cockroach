"""桌面驱动与关联展开验收公共设施：夹具服务、按驱动建浏览器、替身模型路由。"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import sys
import time
from pathlib import Path

from hands.config import PROJECT_ROOT, load_config
from hands.driver import create_browser
from hands.vlm.client import FuncClient

sys.path.insert(0, str(PROJECT_ROOT / "fixtures"))

PORT = 8771
BASE = f"http://127.0.0.1:{PORT}"
DESC_OK = json.dumps({"regions": [{"position": "中部", "kind": "其他", "note": "替身描述"}], "overlay": False})


def start_fixtures():
    import server
    return server.start(PORT)


def config(driver: str = "cdp", **kw):
    c = load_config(driver=driver, decision="vlm")
    c.headless = True if driver != "desktop" else False
    c.profile_dir = PROJECT_ROOT / "profiles" / "desktop_acceptance"   # 不和其他验收抢同一个用户目录
    c.min_interval_s = {}
    for k, v in kw.items():
        setattr(c, k, v)
    return c


async def browser_for(driver: str, cfg=None):
    cfg = cfg or config(driver)
    b = create_browser(cfg)
    await b.start()
    if b.page is None:
        await b.new_page()
    return b, cfg


async def close_agent_window(b):
    """验收专用：跑完关掉本次的代理窗口（正常使用时窗口保留给用户看）。"""
    hwnd = getattr(b, "hwnd", None)
    await b.stop()
    if hwnd:
        from hands.desktop import win32
        win32.close_window(hwnd)


def no_touch(seconds: int = 5):
    print(f"[验收] 接下来会真实移动鼠标、操作代理窗口：请勿触碰鼠标键盘（{seconds} 秒后开始）", flush=True)
    time.sleep(seconds)


def say(tool, reason="替身", **args):
    return json.dumps({"reason": reason, "tool": tool, "arguments": args}, ensure_ascii=False)


def element_no(user: str, pattern: str) -> int | None:
    """从决策提示词的元素清单里按名称找编号（行格式：编号 | 类型 | 名称 | …）。"""
    for line in user.splitlines():
        m = re.match(r"\s*(\d+)\s*\|\s*[^|]+\|\s*([^|]*)", line)
        if m and re.search(pattern, m.group(2)):
            return int(m.group(1))
    return None


class ExpandScript:
    """关联展开替身模型：子用例里先 extract 候选、再 complete；判定按 judge[词] 给出（默认 terminal）。"""

    def __init__(self, candidates: dict[str, list[str]], judge: dict[str, list[dict]], fields=None):
        self.candidates = candidates
        self.judge = judge
        self.fields = fields or {}               # 词 → [(字段, 值)]
        self.extracted: set[str] = set()
        self.judged: list[str] = []

    @staticmethod
    def _word(user: str) -> str | None:
        m = re.search(r"当前节点：「([^」]+)」", user)
        return m.group(1) if m else None

    def __call__(self, system, user, images, schema):
        if "【关联展开判定】" in user:
            w = self._word(user)
            self.judged.append(w)
            ops = self.judge.get(w, [{"op": "terminal", "reason": "替身：无新连接"}])
            return json.dumps({"ops": ops}, ensure_ascii=False)
        if "regions" in json.dumps(schema or {}, ensure_ascii=False) and "【任务】" not in user:
            return DESC_OK
        w = self._word(user)
        if w in self.extracted:
            return say("complete", summary=f"{w} 已收集候选")
        self.extracted.add(w)
        items = [{"field": "候选", "value": v} for v in self.candidates.get(w, [w])]
        items += [{"field": f, "value": v} for f, v in self.fields.get(w, [])]
        return say("extract", items=items)


def expand_client(script: ExpandScript) -> FuncClient:
    return FuncClient(script)
