"""积木注册表：12 个积木的 id、中文名、执行者、参数规格、产出、消耗。

校验（validate.py）、搭建工具表单（/api/books/blocks）、指导提示词（guide.py）都从这里生成，三者保持一致。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# 参数类型：text 文字；int / float 数字；bool 开关；list 文字列表；map 文字对照表；choice 从 choices 里选；
# listref 引用一张清单（内置清单或前面积木的产出）；out 产出清单的名字；flow 子积木串（只用于"打开第 N 个"）
PARAM_TYPES = ("text", "int", "float", "bool", "list", "map", "choice", "listref", "out", "flow")

BUILTIN_LISTS = ("当前词", "所在键", "已知词", "已知键")
TEMPLATE_VARS = ("word", "key", "q")
FORMAT = "taskbook/1"


@dataclass(frozen=True)
class ParamSpec:
    name: str
    label: str
    type: str
    required: bool = False
    default: Any = None
    min: float | None = None
    max: float | None = None
    choices: tuple = ()
    help: str = ""


@dataclass(frozen=True)
class BlockSpec:
    id: str
    label: str
    actor: str                       # program / 4b
    params: tuple[ParamSpec, ...]
    produces: bool                   # 有没有 as 产出清单
    cost: int                        # 每执行一次消耗几次
    help: str

    def param(self, name: str) -> ParamSpec | None:
        return next((p for p in self.params if p.name == name), None)


P = ParamSpec


def _as(required=True, label="产出清单名", help="这一步产出的清单叫什么，后面的积木用这个名字引用"):
    return P("as", label, "out", required, help=help)


_BLOCKS = [
    BlockSpec("search", "搜索", "program", (
        P("home", "首页网址", "text", help="填了就像人一样从首页进：打开首页，在搜索框里输入搜索词回车；"
          "已经在这个站的页面上（如上一次的结果页）就直接用页面上的搜索框。如 https://www.amazon.com/"),
        P("box", "搜索框名字", "text", help="页面上有几个搜索框时，用名字里含这段文字的那个（不分大小写），如 Search Coupons；"
          "页面还没加载出来就等几秒再找"),
        P("url", "搜索网址", "text", False, help="不填首页时必填：搜索页网址，用 {q} 表示搜索词的位置，如 https://www.google.com/search?q={q}"),
        P("query", "搜索词", "text", True, "{word}", help="可用 {word}（当前词）、{key}（所在键）和开放参数，如 {word} transformers"),
        P("extra", "附加参数", "map", help="追加到网址上的查询参数（只用于搜索网址），值也可用模板变量，如 hl: \"{lang}\""),
    ), False, 1, "从首页搜索框输入搜索词，或按搜索词模板直接打开搜索结果页。"),
    BlockSpec("scroll_all", "滚动看完", "program", (
        P("mode", "方式", "choice", False, "bottom_then_top", choices=("bottom_then_top", "to_text"),
          help="bottom_then_top：一屏一屏滚到底再回到顶部；to_text：滚到含指定文字的地方"),
        P("text", "目标文字", "text", help="mode 为 to_text 时要找的文字，如 相关搜索；找不到就改为滚到底再回顶"),
    ), False, 0, "把页面滚动看完，让视口外的内容（如底部的相关搜索）也加载出来。"),
    BlockSpec("pick_list", "抽列表", "4b", (
        P("region", "区块", "text", True, help="要抽哪个区块的条目，用页面上看得到的说法，如 搜索结果的标题和摘要、相关搜索、收藏榜"),
        P("anchor", "锚点文字", "list", help="只看这些文字之后的内容（任一命中即可），如 [相关搜索, Related searches]"),
        P("max", "最多几条", "int", False, 10, 1, 50),
        P("screens", "最多看几屏", "int", False, 6, 1, 30),
        _as(),
    ), True, 0, "4b 在标注图上选出区块里条目的编号，程序按编号逐字取文字（同一节点按原文去重）。"),
    BlockSpec("open", "打开第 N 个", "program", (
        P("from", "来源清单", "listref", True),
        P("n", "第几个", "int", False, 1, 1, 50),
        P("count", "连续几个", "int", False, 1, 1, 20,
          help="从第 N 个起依次打开几个（每个都执行子积木串并返回，子积木串的产出合并成一张清单；每打开一个消耗 1 次）"),
        P("flow", "打开后做什么", "flow", True, help="打开后在那一页执行的积木串，如 读详情字段"),
        P("back", "做完返回", "bool", False, True, help="做完后关掉标签页或后退，回到原来的页面"),
    ), False, 1, "点开来源清单第 N 条对应的元素，在打开的页面上执行子积木串；可连续打开几个。"),
    BlockSpec("read_fields", "读详情字段", "4b", (
        P("names", "字段名", "list", True, help="要读的字段，如 [店名, 价位, 评分]"),
        P("name_field", "名称字段", "text", help="哪个字段作为这一条的名字；不填用页面标题"),
        _as(),
    ), True, 0, "4b 在当前页选出每个字段所在的元素或文本行编号，程序取原文作为字段值；没找到的记“未找到”。"),
    BlockSpec("ratio", "数比例", "program", (
        P("in", "结果清单", "listref", True),
        P("top", "看前几条", "int", False, 10, 1, 100),
        P("words", "候选词清单", "listref", True, help="拿哪些词去数，常用 已知词"),
        P("at_least", "比例至少", "float", False, 0.8, 0.0, 1.0, help="0 到 1，如 0.8 表示 80%"),
        P("drop_current", "去掉当前词", "bool", False, True),
        _as(),
    ), True, 0, "对候选词清单里每个词，数结果清单前 N 条里正文或摘要提到它的比例，达到阈值的输出。"),
    BlockSpec("rank", "取名次", "program", (
        P("in", "来源清单", "listref", True),
        P("k", "第几名", "int", False, None, 1, 100, help="取第 k 名（从 1 开始）；与 前几个 二选一"),
        P("top_k", "前几个", "int", False, None, 1, 100, help="取前 k 个；与 第几名 二选一"),
        _as(),
    ), True, 0, "按清单里的先后取第 k 名或前 k 个。"),
    BlockSpec("filter", "过滤", "program", (
        P("in", "来源清单", "listref", True),
        P("alias", "别名表", "map", help="别名 → 统一写法，如 擎天柱: optimus；先归一再做其它过滤"),
        P("stop", "停用词", "list", help="条目文字里出现这些词就去掉（按包含比较）"),
        P("drop_current", "去掉当前词", "bool", False, False),
        P("drop_known", "去掉已知词", "bool", False, False),
        P("only_known", "只留已知词", "bool", False, False),
        P("ignore_case", "不分大小写", "bool", False, True),
        P("contains", "包含即算", "bool", False, False,
          help="别名、当前词、已知词按“包含”比较（如「Megatron (Transformers)」算 megatron），而不是整条相等"),
        _as(),
    ), True, 0, "按别名表、停用词、当前词、已知词过滤并去重，每个被滤掉的项都记下原因。"),
    BlockSpec("tree", "改树", "program", (
        P("use", "依据清单", "listref", True, help="加为值时是要加的词；标末端 / 删除时清单非空才执行"),
        P("op", "操作", "choice", True, choices=("add_to_current", "add_to_key", "terminal", "delete"),
          help="add_to_current 加为当前词的值（当前词成为键）；add_to_key 加到指定键下；terminal 当前节点标为末端叶子；delete 删除当前节点"),
        P("key", "指定键", "text", help="op 为 add_to_key 时填已知的键"),
        P("new_terminal", "新值直接为末端", "bool", False, False, help="新加的值不再搜索，直接作为末端叶子"),
        P("keep_tab", "保留标签页", "bool", False, False),
    ), False, 0, "条件满足时改树。整个节点跑完后按顺序统一执行。"),
    BlockSpec("stop", "停", "program", (
        P("when", "条件清单", "listref", True, help="这张清单非空就停"),
        P("action", "停下后", "choice", False, "finish", choices=("finish", "handoff"),
          help="finish 结束任务并输出；handoff 交给人，进入等待决策"),
    ), False, 0, "条件清单非空时停下，后面的积木不再执行。"),
    BlockSpec("page", "翻页", "program", (
        P("to", "翻到第几页", "int", False, None, 2, 100, help="与 再翻一页 二选一"),
        P("next", "再翻一页", "bool", False, None),
        P("next_text", "下一页的文字", "list", False, ["下一页", "Next", "›"]),
    ), False, 1, "点页码或“下一页”翻页；找不到下一页时记录原因，不消耗次数。"),
    BlockSpec("compare", "比较字段", "program", (
        P("in", "来源清单", "listref", True, help="一般是 读详情字段 的产出"),
        P("field", "字段", "text", True, help="如 价位、评分"),
        P("op", "比较方式", "choice", True, choices=("<=", ">=", "=", "contains")),
        P("value", "比较值", "text", True, help="价位可写 $$、人均 ¥80；评分写数字；contains 写文字"),
        _as(),
        P("unknown_as", "未知清单名", "out", help="字段识别不了的条目放进这张清单"),
    ), True, 0, "按字段比较：价位统一成档位比较，其它取数字；满足的输出，识别不了的进未知清单。"),
]

REGISTRY: dict[str, BlockSpec] = {b.id: b for b in _BLOCKS}


def registry_json() -> list[dict]:
    """供前端表单与指导提示词使用的纯数据。"""
    out = []
    for b in _BLOCKS:
        d = asdict(b)
        d["params"] = [{**asdict(p), "choices": list(p.choices)} for p in b.params]
        out.append(d)
    return out
