"""桌面驱动与任务书验收公共设施：夹具服务、按驱动建浏览器、替身模型（任务书替身 4b 只回编号）。"""
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


# ---------------- 任务书替身 4b：只回编号（与真实 4b 的输入输出一样） ----------------
_LINE = re.compile(r"^\s*(\d+) \| [^|]+ \| ([^|]+?)(?: \| (.*))?$")


def element_lines(user: str) -> list[tuple[int, str, str]]:
    out = []
    for line in user.splitlines():
        m = _LINE.match(line)
        if m:
            out.append((int(m.group(1)), m.group(2).strip(), m.group(3) or ""))
    return out


def book_pick(region: str, lines) -> list[int]:
    """按区块选编号：看元素清单里的链接地址（夹具页面的结构是固定的）。"""
    if "相关" in region:
        return [no for no, _, rest in lines if "search.html?q=" in rest]
    if "餐厅" in region:
        return [no for no, _, rest in lines if "biz.html" in rest]
    if "下一页" in region:
        return []
    return [no for no, _, rest in lines if "result.html" in rest]


def book_fields(names: list[str], lines: list[str]) -> dict:
    """读详情字段：店名取第一行，其它字段取以「字段：」开头的文本行。"""
    out = {}
    for n in names:
        if n in ("店名", "名称", "标题"):
            out[n] = lines[0].split(" ")[0] if lines else None
        else:
            out[n] = next((l.split(" ")[0] for l in lines if re.match(rf"^T\d+ {re.escape(n)}[:：]", l)), None)
    return out


class BookStub:
    """任务书替身 4b。bad_no：每次抽列表时额外给一个不存在的编号（验收"被拒并记录"）。"""

    def __init__(self, pick=book_pick, fields=book_fields, bad_no: int | None = None):
        self.pick, self.fields, self.bad_no = pick, fields, bad_no
        self.calls: list[str] = []

    def __call__(self, system, user, images, schema):
        if "numbers" in json.dumps(schema or {}):
            self.calls.append("pick_list")
            region = re.search(r"哪些编号是「(.+?)」里的条目", user).group(1)
            nos = list(self.pick(region, element_lines(user)))
            if self.bad_no is not None:
                nos.append(self.bad_no)
            return json.dumps({"numbers": nos})
        if "fields" in json.dumps(schema or {}):
            self.calls.append("pick_fields")
            names = re.search(r"字段：(.+)", user).group(1).strip().split("、")
            lines = [l for l in user.splitlines() if re.match(r"^T\d+ ", l)]
            got = self.fields(names, lines)
            return json.dumps({"fields": [{"name": n, "no": got.get(n)} for n in names]}, ensure_ascii=False)
        self.calls.append("other")
        return DESC_OK


def book_client(stub: BookStub | None = None) -> FuncClient:
    return FuncClient(stub or BookStub())


def search_order(tree) -> list[str]:
    out = []
    for e in tree.events:
        if e.op == "search":
            k = tree.key_of(e.node)
            out.append(f"{k}/{tree.nodes[e.node].word}" if k else tree.nodes[e.node].word)
    return out
