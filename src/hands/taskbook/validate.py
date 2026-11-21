"""任务书校验：逐条给出"第几步 / 哪个积木 / 哪个参数 / 为什么"。启动、导入、保存时都调用。"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .blocks import BUILTIN_LISTS, REGISTRY, TEMPLATE_VARS
from .book import PARAM_TYPES, REWRITE_MARK, BookError, TaskBook, parse, template_vars


@dataclass
class Problem:
    step: str                 # "flow[3]" / "flow[3].open.flow[0]" / "groups" / "params[1]" / "文件"
    block: str | None
    param: str | None
    why: str

    def to_dict(self) -> dict:
        return asdict(self)

    def __str__(self) -> str:
        where = " / ".join(x for x in (self.step, self.block, self.param) if x)
        return f"{where}：{self.why}"


def _empty(v) -> bool:
    return v is None or v == "" or v == [] or v == {}


def _type_problem(spec, v) -> str | None:
    t = spec.type
    if t in ("text", "listref", "out"):
        if isinstance(v, dict):
            return "值看起来是没加引号的 {…}，请给这个值加上引号"
        if not isinstance(v, (str, int, float)) or isinstance(v, bool):
            return "要填一段文字"
    elif t == "int":
        if isinstance(v, bool) or not isinstance(v, int):
            return "要填整数"
    elif t == "float":
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return "要填数字"
    elif t == "bool":
        if not isinstance(v, bool):
            return "要填 true 或 false"
    elif t == "list":
        if not isinstance(v, list) or any(isinstance(x, (dict, list)) for x in v):
            return "要写成列表 [文字, 文字]"
    elif t == "map":
        if not isinstance(v, dict):
            return "要写成对照表 {甲: 乙}"
    elif t == "choice":
        if v not in spec.choices:
            return f"只能是 {' / '.join(map(str, spec.choices))}"
    elif t == "flow":
        if not isinstance(v, list):
            return "要写成积木列表"
    if t in ("int", "float"):
        if spec.min is not None and v < spec.min:
            return f"不能小于 {spec.min:g}"
        if spec.max is not None and v > spec.max:
            return f"不能大于 {spec.max:g}"
    return None


class _Checker:
    def __init__(self, book: TaskBook):
        self.book = book
        self.out: list[Problem] = []
        self.param_names = {str(p.get("name")) for p in book.params if p.get("name")}

    def add(self, step, block, param, why):
        self.out.append(Problem(step, block, param, why))

    # ---------------- 流程 ----------------
    def flow(self, items, prefix: str, defined: set[str]):
        if not isinstance(items, list):
            self.add(prefix, None, None, "要写成积木列表")
            return
        for i, item in enumerate(items):
            step = f"{prefix}[{i}]"
            if not isinstance(item, dict) or len(item) != 1:
                self.add(step, None, None, "每一步要写成「积木: {参数}」")
                continue
            bid, params = next(iter(item.items()))
            spec = REGISTRY.get(bid)
            if spec is None:
                self.add(step, str(bid), None, f"没有这个积木，可用：{', '.join(REGISTRY)}")
                continue
            if params is None:
                params = {}
            if not isinstance(params, dict):
                self.add(step, bid, None, "参数要写成 {参数: 值}")
                continue
            self.block(step, spec, params, defined)

    def block(self, step, spec, params: dict, defined: set[str]):
        bid = spec.id
        known = {p.name for p in spec.params}
        for k in params:
            if k not in known:
                self.add(step, bid, str(k), f"这个积木没有参数 {k}，可用：{', '.join(sorted(known))}")
        for p in spec.params:
            v = params.get(p.name)
            if _empty(v):
                if p.required:
                    self.add(step, bid, p.name, f"必填参数 {p.name}（{p.label}）没填")
                continue
            why = _type_problem(p, v)
            if why:
                self.add(step, bid, p.name, why)
        # 积木自己的组合规则
        if bid == "rank" and (params.get("k") is None) == (params.get("top_k") is None):
            self.add(step, bid, "k", "第几名（k）与前几个（top_k）要填且只填一个")
        if bid == "page" and (params.get("to") is None) == (not params.get("next")):
            self.add(step, bid, "to", "翻到第几页（to）与再翻一页（next: true）要填且只填一个")
        if bid == "scroll_all" and params.get("mode") == "to_text" and _empty(params.get("text")):
            self.add(step, bid, "text", "mode 为 to_text 时要填目标文字")
        if bid == "tree" and params.get("op") == "add_to_key" and _empty(params.get("key")):
            self.add(step, bid, "key", "op 为 add_to_key 时要填指定键")
        if bid == "search":
            self.templates(step, bid, params)
        # 清单引用：只能引用内置清单或前面已产出的
        for p in spec.params:
            v = params.get(p.name)
            if p.type == "listref" and isinstance(v, str) and v and v not in defined:
                self.add(step, bid, p.name, f"清单「{v}」在这一步之前还没有产出（内置清单：{'、'.join(BUILTIN_LISTS)}）")
        if bid == "open" and isinstance(params.get("flow"), list):
            self.flow(params["flow"], f"{step}.open.flow", defined)      # 子流程产出外层也能用
        for p in spec.params:
            v = params.get(p.name)
            if p.type == "out" and isinstance(v, str) and v:
                if v in BUILTIN_LISTS:
                    self.add(step, bid, p.name, f"「{v}」是内置清单名，换一个名字")
                elif v in defined:
                    self.add(step, bid, p.name, f"清单名「{v}」前面已经用过，换一个名字")
                else:
                    defined.add(v)

    def templates(self, step, bid, params):
        url = params.get("url")
        if bid == "search" and _empty(url) and _empty(params.get("home")):
            self.add(step, bid, "url", "首页网址和搜索网址至少填一个")
        if isinstance(url, str) and url and "{q}" not in url:
            self.add(step, bid, "url", "搜索网址里要有 {q}，表示搜索词放在哪里")
        allowed = set(TEMPLATE_VARS) | self.param_names
        fields = [("url", url), ("home", params.get("home")), ("query", params.get("query"))]
        extra = params.get("extra")
        if isinstance(extra, dict):
            fields += [(f"extra.{k}", v) for k, v in extra.items()]
        for name, v in fields:
            if not isinstance(v, str):
                continue
            for var in template_vars(v):
                if var not in allowed:
                    self.add(step, bid, name, f"模板变量 {{{var}}} 没有来源（可用 word、key、q 和开放参数）")
                elif var == "q" and name != "url":
                    self.add(step, bid, name, "{q} 只能用在搜索网址里")

    # ---------------- 全局 ----------------
    def globals(self):
        b = self.book
        seen = set()
        for i, p in enumerate(b.params):
            step = f"params[{i}]"
            n = p.get("name")
            if not n or not str(n).isidentifier():
                self.add(step, None, "name", "参数名要用字母、数字、下划线（不能以数字开头）")
            elif n in TEMPLATE_VARS:
                self.add(step, None, "name", f"参数名不能用 {n}（保留给模板变量）")
            elif n in seen:
                self.add(step, None, "name", f"参数名 {n} 重复")
            seen.add(n)
            if (p.get("type") or "text") not in PARAM_TYPES:
                self.add(step, None, "type", f"类型只能是 {' / '.join(PARAM_TYPES)}")
        if not b.groups:
            self.add("groups", None, None, "至少要有一个初始组")
        words = {k for k, _ in b.groups} | {v for _, vs in b.groups for v in vs}
        if not b.start:
            self.add("start", None, None, "要写起始词")
        elif b.groups and b.start not in words:
            self.add("start", None, None, f"起始词「{b.start}」不在任何组里")
        for k in ("budget", "keep_tabs", "trial_budget"):
            v = b.limits.get(k)
            lo = 0 if k == "keep_tabs" else 1
            if isinstance(v, bool) or not isinstance(v, int) or v < lo:
                self.add("limits", None, k, f"{k} 要填不小于 {lo} 的整数")
        o = b.output
        t = o.get("type")
        if t not in ("leaves", "ranked"):
            self.add("output", None, "type", "输出方式只能是 leaves（末端叶子清单）或 ranked（排序取前 n 个）")
        elif t == "ranked":
            sort = o.get("sort")
            if not isinstance(sort, list) or not sort:
                self.add("output", None, "sort", "ranked 要写排序字段，如 [价位:asc, 评分:desc]")
            else:
                for s in sort:
                    f, _, order = str(s).partition(":")
                    if not f or order not in ("asc", "desc"):
                        self.add("output", None, "sort", f"「{s}」要写成 字段:asc 或 字段:desc")
            n = o.get("n", 5)
            if isinstance(n, bool) or not isinstance(n, int) or n < 1:
                self.add("output", None, "n", "n 要填正整数")
            if o.get("unknown", "last") not in ("last", "exclude"):
                self.add("output", None, "unknown", "unknown 只能是 last（排在后面）或 exclude（不参与）")
            if o.get("group_by") not in (None, "key"):
                self.add("output", None, "group_by", "group_by 只能是 key 或不写")
        if not b.flow:
            self.add("flow", None, None, "节点流程为空：至少要有一个积木")


def needs_rewrite(book: TaskBook) -> bool:
    """旧格式转换来的、节点流程还没补全（备注带改写标记，流程里只有搜索）。"""
    return book.note.startswith(REWRITE_MARK) and all(
        isinstance(s, dict) and list(s) == ["search"] for s in book.flow)


def check(book: TaskBook) -> list[Problem]:
    c = _Checker(book)
    c.globals()
    c.flow(book.flow, "flow", set(BUILTIN_LISTS))
    if needs_rewrite(book):
        c.add("flow", None, None, "由旧格式转换，需要用积木补全节点流程（候选来源与条件见备注）")
    return c.out


def status_of(book: TaskBook, problems: list[Problem]) -> str:
    if needs_rewrite(book):
        return "needs_rewrite"
    return "ok" if not problems else "draft"


def check_text(text: str, name: str | None = None) -> tuple[TaskBook | None, list[Problem]]:
    """解析 + 校验；读不懂时返回 (None, [问题])。"""
    try:
        b = parse(text, name)
    except BookError as e:
        return None, [Problem("文件", None, None, str(e))]
    probs = check(b)
    b.status = status_of(b, probs)
    return b, probs
