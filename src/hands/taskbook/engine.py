"""节点流程引擎：给一个节点按顺序执行积木，产出 NodeRun（清单、每块记录、改树操作、消耗）。

执行规则（plan 模块 A）：
1. 每块开始前：等暂停门 → 检查是否要求停止 → 要消耗次数的积木余量不足则节点中止（stop=budget）。
2. 搜索、打开、翻页之后截图并做转人工检查（web.after_nav）。
3. 每块的参数、输入、产出、4b 选号、被拒、滤掉的项都记进 NodeRun.blocks；出错的积木产出空清单、记录原因，继续下一块。
4. 内置清单：当前词、所在键、已知词、已知键（已知词 / 已知键每次从树上现读）。
5. tree 积木只登记 TreeOp，节点跑完后由运行器按顺序应用。
6. stop 积木的清单非空：设置 NodeRun.stop，后面的积木不再执行。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class Item:
    """清单项：文字都是程序从页面元素 / 文本行逐字取出的原文。"""
    text: str
    element: str | None = None       # 依据元素 id；文本行记 "T12"
    url: str = ""                    # 来源页面
    context: str = ""                # 所在块的文字（结果摘要），供数比例使用
    fields: dict = field(default_factory=dict)      # 字段名 → Item（读详情字段）
    origin: dict = field(default_factory=dict)      # {step, block, screen, ...}

    def to_dict(self) -> dict:
        return {"text": self.text, "element": self.element, "url": self.url, "context": self.context,
                "fields": {k: v.to_dict() for k, v in self.fields.items()}, "origin": dict(self.origin)}


@dataclass
class TreeOp:
    op: str                          # add_to_current / add_to_key / terminal / delete
    words: list                      # list[Item]
    key: str | None = None
    new_terminal: bool = False
    keep_tab: bool = False
    step: str = ""

    def to_dict(self) -> dict:
        return {"op": self.op, "words": [w.text for w in self.words], "key": self.key,
                "new_terminal": self.new_terminal, "keep_tab": self.keep_tab, "step": self.step}


@dataclass
class NodeRun:
    lists: dict = field(default_factory=dict)       # 清单名 → list[Item]（不含内置清单）
    blocks: list = field(default_factory=list)      # 每块的记录
    ops: list = field(default_factory=list)         # list[TreeOp]
    cost: int = 0
    stop: str | None = None                         # finish / handoff / cancel / budget / None
    error: str | None = None

    def to_dict(self) -> dict:
        return {"lists": {k: [i.to_dict() for i in v] for k, v in self.lists.items()}, "blocks": self.blocks,
                "ops": [o.to_dict() for o in self.ops], "cost": self.cost, "stop": self.stop, "error": self.error}


@dataclass
class NodeCtx:
    """引擎执行一个节点所需的一切。page 是当前页面（转人工后可能换成新对象）。"""
    browser: Any
    page: Any
    config: Any
    book: Any                                       # TaskBook（已 bind 参数）
    tree: Any                                       # ExpandTree
    node: Any                                       # ExNode
    node_dir: Path
    gate: Any
    checker: Any = None                             # HandoffChecker
    client: Any = None                              # 4b 客户端
    prompts: Any = None                             # PromptBook
    vlm: dict = field(default_factory=dict)         # vlm_settings
    make_executor: Callable | None = None           # page -> ActionExecutor
    should_stop: Callable = lambda: False
    consume: Callable = lambda k: None              # 记一次消耗（运行器转给 tree.consume）
    remaining: Callable = lambda: 10 ** 9
    last_visit: dict = field(default_factory=dict)  # 按域名访问间隔
    can_tab: bool = False                           # 驱动支持标签页（打开第 N 个用新标签页）
    log: Callable = print
    shots: int = 0                                  # 本节点已截图数

    def executor(self):
        return self.make_executor(self.page)

    def shot_name(self) -> str:
        self.shots += 1
        return f"s{self.shots:02d}.png"


class ListTable(dict):
    """清单表：自己产出的清单 + 四张内置清单（已知词 / 已知键现读树）。"""

    def __init__(self, ctx: NodeCtx):
        super().__init__()
        self.ctx = ctx

    def get_list(self, name: str) -> list[Item]:
        t, n = self.ctx.tree, self.ctx.node
        if name == "当前词":
            return [Item(n.word)]
        if name == "所在键":
            k = t.key_of(n.id)
            return [Item(k)] if k else []
        if name == "已知键":
            return [Item(k) for k in t.mapping]
        if name == "已知词":
            seen, out = set(), []
            for k, vs in t.mapping.items():
                for w in [k, *vs]:
                    if w not in seen:
                        seen.add(w)
                        out.append(Item(w))
            return out
        return list(self.get(name) or [])


class EngineAbort(Exception):
    """要整个任务停下的错误（模型不可用、浏览器崩溃等）由运行器处理，引擎不吞。"""


def _jsonable(v):
    try:
        json.dumps(v, ensure_ascii=False)
        return v
    except (TypeError, ValueError):
        return str(v)


def _handlers() -> dict:
    from . import pick, web
    return {"search": web.search, "scroll_all": web.scroll_all, "open": web.open_nth, "page": web.page,
            "pick_list": pick.pick_list, "read_fields": pick.read_fields,
            "ratio": _ratio, "rank": _rank, "filter": _filter, "compare": _compare, "tree": _tree, "stop": _stop}


async def run_node(ctx: NodeCtx) -> NodeRun:
    run = NodeRun()
    lists = ListTable(ctx)
    await run_flow(ctx, ctx.book.flow, "flow", lists, run)
    run.lists = dict(lists)
    return run


async def run_flow(ctx: NodeCtx, steps: list, prefix: str, lists: ListTable, run: NodeRun):
    from ..checks import HandoffFailed
    from ..cdp import BrowserCrashed
    from ..vlm.client import ModelUnavailable
    from .blocks import REGISTRY
    handlers = _handlers()
    for i, item in enumerate(steps or []):
        if run.stop:
            return
        step = f"{prefix}[{i}]"
        bid, params = next(iter(item.items()))
        params = params or {}
        spec = REGISTRY[bid]
        await ctx.gate.wait()
        if ctx.should_stop():
            run.stop = "cancel"
            return
        if spec.cost and ctx.remaining() < spec.cost:
            run.stop = "budget"
            run.blocks.append({"step": step, "block": bid, "skipped": "次数用满"})
            return
        rec: dict = {"step": step, "block": bid, "label": spec.label,
                     "params": {k: _jsonable(v) for k, v in params.items() if k != "flow"},
                     "inputs": {p.name: len(lists.get_list(params[p.name])) for p in spec.params
                                if p.type == "listref" and isinstance(params.get(p.name), str)}}
        t0 = time.monotonic()
        out: list[Item] = []
        try:
            got = await handlers[bid](ctx, params, lists, rec, run, step)
            out = list(got or [])
        except (ModelUnavailable, BrowserCrashed):
            rec["error"] = "任务中止"
            run.blocks.append(rec)
            raise
        except HandoffFailed as e:
            rec["error"] = str(e)
            run.stop = "handoff"
        except Exception as e:                     # 一块出错不拖垮整个节点：产出空清单，继续
            rec["error"] = f"{type(e).__name__}: {e}"
            import traceback
            rec["trace"] = traceback.format_exc(limit=-6).splitlines()[-14:]
            ctx.log(f"[taskbook] {ctx.node.word} {step} {bid} 出错：{rec['error']}")
        cost = rec.get("cost", spec.cost)
        rec["cost"] = cost
        if cost:
            run.cost += cost
            if not rec.pop("_consumed", False):       # open 每打开一个已经当场记过账
                ctx.consume(cost)
        rec.pop("_consumed", None)
        if spec.produces and params.get("as"):
            lists[params["as"]] = out
            rec["out"] = [x.to_dict() for x in out]
        rec["ms"] = int((time.monotonic() - t0) * 1000)
        # 子流程的记录先于本块追加（open 在内部递归）：本块记录插到它的子记录之前
        idx = rec.pop("_index", None)
        if idx is None:
            run.blocks.append(rec)
        else:
            run.blocks.insert(idx, rec)


# ---------------- 计算、改树、停（直接在引擎里接线） ----------------
def _current(ctx) -> str:
    return ctx.node.word


async def _ratio(ctx, p, lists, rec, run, step):
    from .calc import ratio
    out, log = ratio(lists.get_list(p["in"]), int(p.get("top", 10)), lists.get_list(p["words"]),
                     float(p.get("at_least", 0.8)), _current(ctx), bool(p.get("drop_current", True)))
    rec["ratios"] = log
    return out


async def _rank(ctx, p, lists, rec, run, step):
    from .calc import rank
    out, log = rank(lists.get_list(p["in"]), p.get("k"), p.get("top_k"))
    if log:
        rec["note"] = log[0]["why"]
    return out


async def _filter(ctx, p, lists, rec, run, step):
    from .calc import filter_items
    out, dropped = filter_items(
        lists.get_list(p["in"]), alias=p.get("alias"), stop=p.get("stop"), current=_current(ctx),
        known=lists.get_list("已知词"), drop_current=bool(p.get("drop_current", False)),
        drop_known=bool(p.get("drop_known", False)), only_known=bool(p.get("only_known", False)),
        ignore_case=bool(p.get("ignore_case", True)), contains=bool(p.get("contains", False)))
    rec["dropped"] = dropped
    return out


async def _compare(ctx, p, lists, rec, run, step):
    from .calc import compare
    ok, unknown, dropped = compare(lists.get_list(p["in"]), str(p["field"]), str(p["op"]), p["value"])
    rec["dropped"] = dropped
    if p.get("unknown_as"):
        lists[p["unknown_as"]] = unknown
        rec["unknown"] = [x.to_dict() for x in unknown]
    return ok


async def _tree(ctx, p, lists, rec, run, step):
    words = lists.get_list(p["use"])
    op = p["op"]
    if not words:
        rec["note"] = f"清单「{p['use']}」为空，不改树"
        return None
    # 加为值时 words 就是要加的词；标末端 / 删除时 words 只是依据（清单非空才执行）
    tops = TreeOp(op, words, p.get("key"), bool(p.get("new_terminal", False)), bool(p.get("keep_tab", False)), step)
    run.ops.append(tops)
    rec["op"] = tops.to_dict()
    return None


async def _stop(ctx, p, lists, rec, run, step):
    when = lists.get_list(p["when"])
    if when:
        run.stop = p.get("action", "finish")
        rec["stop"] = run.stop
        rec["evidence"] = [w.text for w in when]
    else:
        rec["note"] = f"清单「{p['when']}」为空，不停"
    return None
