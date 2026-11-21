# 使用者自制任务书 + 平板远程 Plan

日期：2026-11-15　状态：待审批（阶段二）　依据：`docs/taskbook/spec.md`（已批准）

## 架构概览

```
┌──────────── 面板服务（本机进程）────────────────────────────────────┐
│ 本机入口 127.0.0.1:8790（原令牌鉴权，电脑面板）                      │
│ 远程入口 0.0.0.0:8791（设备凭证鉴权）◄── cloudflared ◄── 公网 HTTPS │
│      │                    │                                          │
│  共用：JobManager / 任务书服务 / 实时事件中心（WS）                  │
└──────┼───────────────────────────────────────────────────────────────┘
       ▼
 hands.taskbook（新包）
   格式与积木注册表 → 校验 → 旧格式转换 → 指导提示词生成
   TaskbookRunner：挑节点（沿用 ExpandTree）→ 节点流程引擎 → 改树
        ├─ 网页积木：搜索 / 滚动看完 / 打开第 N 个 / 翻页（走 ActionExecutor：拦截、需确认、暂停照旧）
        ├─ 选编号积木：抽列表 / 读详情字段（PageDigest 标注图 + 4b 只回编号）
        ├─ 计算积木：数比例 / 取名次 / 过滤 / 比较字段（纯函数）
        └─ 改树 / 停
```

| 组件 | 职责 |
|---|---|
| `hands/taskbook/blocks.py` 积木注册表 | 12 个积木的 id、中文名、执行者、参数规格（类型 / 必填 / 范围）、是否产出清单、消耗次数。校验、搭建工具表单、指导提示词都从这一处生成，保证三者一致（AC8 自动检查）。 |
| `book.py` 任务书 | 读写 YAML（`format: taskbook/1`），开放参数替换，保存时留备份，内容版本号（内容摘要）。 |
| `validate.py` | 逐条给出"第几步 / 积木 / 参数 / 原因"，嵌套流程（打开第 N 个里面的积木）也检查。 |
| `convert.py` | 上一章旧格式转成新格式：结构字段照搬，条件写进备注，流程留空标"需要改写"。 |
| `engine.py` 节点流程引擎 | 给一个节点按顺序执行积木，维护清单表（内置清单：当前词、所在键、已知词、已知键），记录每块的输入输出，返回改树操作和消耗次数。 |
| `web.py` / `pick.py` / `calc.py` | 分别实现网页积木、4b 选编号积木（新提示词 `pick_list.md`、`pick_fields.md`）、计算积木。 |
| `runner.py` TaskbookRunner | 取代 ExpandRunner：循环挑节点 → 引擎 → 改树（来源记为 `rule`）→ 快照。支持试跑（小上限、标"试跑"）。 |
| `hands/checks.py` | 从 CaseRunner 抽出"验证码 / 登录 / 系统对话框 → 转人工"的检查，CaseRunner 和积木共用（N1 回归要保持不变）。 |
| `panel/books.py` | 任务书服务：列表、读取、保存（带版本冲突检查）、导入（文本 / 文件）、校验、预览、指导提示词。 |
| `panel/remote.py` / `tunnel.py` / `devices.py` | 远程入口（第二个 aiohttp 站点）、cloudflared 进程管理与地址解析、配对码与设备凭证（只存摘要）。 |
| 前端 | `build.*` 搭建工具（含试跑查看）、`m.*` 平板外壳（状态 / 树 / 任务书 / 记录四个标签页）。`tree.*` 改成响应式，两端共用。电脑首页加"远程访问"卡片。 |

**退役**：`expand/judge.py`、`expand/subtask.py`、`expand/run.py`、提示词 `expand_judge.md` / `expand_node.md`。`expand/tree.py`（展开规则）和 `rank.py`（价位排序）保留复用；`expand/spec.py` 保留，只供 `convert.py` 读旧格式。

### spec 需求归属

| 需求 | 归属 |
|---|---|
| F1 积木执行规则 | `engine.py`、`web.py`、`pick.py`、`calc.py`、`ExpandTree`（展开规则） |
| F2 任务书内容 | `book.py`（`TaskBook`、YAML 格式） |
| F3 校验 | `validate.py`；`/api/books/validate`、启动 / 导入 / 保存时调用 |
| F4 搭建与修改 | `build.*`、`/api/books/save`（覆盖留备份 / 另存为） |
| F5 导入与指导提示词 | `convert.py`、`guide.py`、`/api/books/import`、`/api/books/guide` |
| F6 预览与试跑 | `/api/books/preview`（ExpandTree 模拟）、`TaskbookRunner(trial=True)`、`/api/books/steps` |
| F7 正式运行与运行中改树 | `TaskbookRunner`、`JobManager` 任务种类 `book`、`/api/expand/*`、命令行 `book` |
| F8 旧样例改写 | `tasks/*.yaml` 五份改写，旧版移到测试数据 |
| F9 平板页面 | `m.*`、响应式 `tree.*` / `build.*`、远程 WS 缩略图模式、提醒 |
| F10 远程访问开关 | `remote.py`、`tunnel.py`、电脑首页"远程访问"卡片 |
| F11 配对与设备凭证 | `devices.py`、`pair.html`、远程入口中间件 |
| F12 两端同时操作 | 树 `rev`、任务书 `rev`、409 |
| F13 记录 | 运行目录 `book/`（见"模块交互 · 记录"）、`remote.jsonl`、修改来源 |

## 核心数据结构

### 1. 任务书文件（`tasks/*.yaml`，`format: taskbook/1`）

积木用英文 id，界面和指导提示词里同时显示中文名。清单名可以用中文。

```yaml
format: taskbook/1
name: transformers
title: 变形金刚角色（Google）
note: 说明文字，可选
params:                       # 开放参数：发起时可改
  - {name: lang, type: text, default: en, label: 结果语言}
groups:
  - optimus: [galvatron, megatron, starscream]
  - megatron: [starscream, shockwave, soundwave]
  - movie: [fallen, decepticon, autobot]
start: optimus
limits: {budget: 120, keep_tabs: 30, trial_budget: 5}
flow:
  - search:  {url: "https://www.google.com/search?q={q}", query: "{word} transformers", extra: {hl: "{lang}"}}
  - scroll_all: {mode: bottom_then_top}
  - pick_list: {region: 搜索结果的标题和摘要, max: 10, as: 结果}
  - pick_list: {region: 相关搜索, anchor: [相关搜索, Related searches], max: 12, as: 相关}
  - ratio:   {in: 结果, top: 10, words: 已知词, at_least: 0.8, drop_current: true, as: 高频}
  - tree:    {use: 高频, op: add_to_current}
  - filter:  {in: 相关, alias: {擎天柱: optimus, Optimus Prime: optimus}, drop_current: true, only_known: true, as: 相关已知}
  - tree:    {use: 相关已知, op: add_to_current}
output: {type: leaves}
```

点餐示例里的流程和输出部分：

```yaml
flow:
  - search: {url: "https://www.bing.com/search?q={q}", query: "{word} {city} 收藏榜"}
  - pick_list: {region: 收藏榜, max: 5, as: 榜单}
  - rank: {in: 榜单, k: 1, as: 榜首}
  - open: {from: 榜首, n: 1, back: true, flow: [
        {read_fields: {names: [店名, 价位, 评分], name_field: 店名, as: 详情}} ]}
  - compare: {in: 详情, field: 价位, op: "<=", value: "$$", as: 便宜, unknown_as: 价位未知}
  - tree: {use: 便宜, op: add_to_current, new_terminal: true}
  - tree: {use: 价位未知, op: add_to_current, new_terminal: true}
output: {type: ranked, sort: [价位:asc, 评分:desc], n: 3, group_by: key, unknown: last}
```

### 2. 12 个积木的 id 与参数（注册表）

| id | 中文 | 主要参数 | 产出 `as` | 消耗 |
|---|---|---|---|---|
| `search` | 搜索 | url（含 `{q}`）、query（可用 `{word}` `{key}` 和开放参数）、extra | — | 1 |
| `scroll_all` | 滚动看完 | mode: `bottom_then_top` / `to_text` + text | — | 0 |
| `pick_list` | 抽列表 | region、anchor（可选，只看锚点文字之后）、max、screens（最多看几屏，默认 6） | ✓ | 0 |
| `open` | 打开第 N 个 | from、n、flow（子积木串）、back | 子流程里的 `as` | 1 |
| `read_fields` | 读详情字段 | names、name_field | ✓ | 0 |
| `ratio` | 数比例 | in、top、words（清单名）、at_least、drop_current | ✓ | 0 |
| `rank` | 取名次 | in、k、top_k（二选一） | ✓ | 0 |
| `filter` | 过滤 | in、alias、stop、drop_current、drop_known、only_known、ignore_case（默认是）、contains | ✓ | 0 |
| `tree` | 改树 | use、op: `add_to_current` / `add_to_key`（+key）/ `terminal` / `delete`、new_terminal、keep_tab | — | 0 |
| `stop` | 停 | when（清单名，非空即触发）、action: `finish` / `handoff` | — | 0 |
| `page` | 翻页 | to 或 next、next_text（默认 下一页 / Next / ›） | — | 1 |
| `compare` | 比较字段 | in、field、op: `<=` `>=` `=` `contains`、value、unknown_as | ✓ | 0 |

内置清单（不用产出就能引用）：`当前词`、`所在键`、`已知词`、`已知键`。

### 3. 代码里的结构（Python）

```python
@dataclass(frozen=True)
class ParamSpec:            # 积木参数规格：校验、表单、指导提示词共用
    name: str; label: str
    type: str               # text / int / float / bool / list / map / choice / listref / words_ref / flow
    required: bool = False; default: Any = None
    min: float | None = None; max: float | None = None
    choices: tuple = (); help: str = ""

@dataclass(frozen=True)
class BlockSpec:
    id: str; label: str; actor: str          # program / 4b
    params: tuple[ParamSpec, ...]; produces: bool; cost: int; help: str

@dataclass
class Item:                 # 清单项
    text: str               # 原文（程序从元素 / 文本行逐字取出）
    element: str | None     # 依据元素 id；文本行记 "T12"
    url: str                # 来源页面
    context: str = ""       # 所在块的文字（结果摘要），供数比例使用
    fields: dict[str, Item] = field(default_factory=dict)   # read_fields 的字段值
    origin: dict = field(default_factory=dict)             # {step, block, screen}

@dataclass
class TaskBook:
    format: str; name: str; title: str; note: str
    params: list[dict]; groups: list[tuple[str, list[str]]]; start: str
    limits: dict; flow: list[dict]; output: dict
    values: dict            # 本次运行的参数值
    rev: str                # 文件内容摘要（保存冲突检查）
    status: str             # ok / draft（校验未通过）/ needs_rewrite（旧格式转换来的）

@dataclass
class Problem:              # 校验问题
    step: str               # "flow[3]" / "flow[3].open.flow[0]" / "groups[1]"
    block: str | None; param: str | None; why: str

@dataclass
class TreeOp:               # 改树积木产出，交给 ExpandTree 执行
    op: str                 # add_to_current / add_to_key / terminal / delete
    words: list[Item]; key: str | None; new_terminal: bool; keep_tab: bool
    step: str               # 来自哪一步

@dataclass
class NodeRun:              # 引擎跑完一个节点的结果（同时写进记录）
    lists: dict[str, list[Item]]; blocks: list[dict]   # 每块：参数、输入、产出、4b 选号、被拒、滤掉的项及原因
    ops: list[TreeOp]; cost: int; stop: str | None     # finish / handoff / None
    error: str | None
```

### 4. 树与版本

- `ExpandTree` 增加：
  - 来源 `rule`，表示由任务书积木改的树；
  - `new_terminal`：新节点直接定为末端叶子，不再搜索；
  - `rev`：整棵树的版本号，任何改动都 +1。快照带 `rev`，改树请求也要带 `rev`，不一致就拒绝（F12）。
- 任务书的 `rev` 是文件内容的摘要。保存请求带上打开时的 `rev`，与磁盘上的不一致就拒绝。

### 5. 设备与远程

```python
@dataclass
class Device:
    id: str; name: str; origin: str      # 绑定的访问地址（局域网 / 本次公网地址）
    kind: str                            # lan / public
    secret_hash: str                     # sha256(凭证)，明文只给平板一次
    created: float; last_seen: float

@dataclass
class PairCode:
    code: str            # secrets.token_urlsafe(32)
    origin: str; expires: float; used: bool
```

设备存放在 `<数据目录>/panel_devices.json`，加入 `.gitignore`。公网设备在隧道关闭时删除。

## 模块设计

### A. 节点流程引擎（`taskbook/engine.py`）
**职责**：给一个节点按顺序跑积木，产出 `NodeRun`。
**对外接口**：`async run_node(node, tree, book, ctx) -> NodeRun`。`ctx` 里带浏览器、ActionExecutor 工厂、PauseGate、4b 客户端、记录目录、`should_stop`、剩余次数。

执行规则：
1. 每个积木开始前都过一遍：`await gate.wait()`（人工输入或远程暂停时在这里停住）→ `should_stop()` → 剩余次数。要消耗次数的积木如果余量不足，节点中止，标"次数用满"；runner 把节点退回待查，做法和上一章一样。
2. 搜索、打开、翻页这三个积木之后都调用 `checks.check_page()`：遇到验证码、登录或系统对话框，就转人工，等人处理完再继续当前积木串。
3. 每个积木的参数、输入清单、产出、4b 选号、被拒编号、滤掉的项及原因，都追加到 `blocks` 里。出错的积木产出空清单，记录 `error`，然后继续下一块；不因为一块出错就整个节点失败。
4. 清单表起始时就有四张内置清单：`当前词`、`所在键`、`已知词`、`已知键`。`已知词` 每次都从树上实时读取。
5. `tree` 积木只登记 `TreeOp`，不当场改树。整个节点跑完后，由 runner 按顺序一次应用。这样同一批里前一条"加为值"生效后，后一条看到的状态就是对的（D2 里那种顺序问题不会再出现）。
6. `stop` 积木的清单非空时，设置 `NodeRun.stop`，后面的积木不再执行。

**依赖**：web、pick、calc、checks。

### B. 网页积木（`taskbook/web.py`）
- `search`：拼出搜索词 → 按 `{q}` 编码后填进网址，加上 `extra` 参数 → 等待按域名的最小访问间隔（从 CaseRunner 抽出来共用）→ `page.navigate` → `wait_settled`。
- `scroll_all`：
  - 两种模式都通过 ActionExecutor 的滚轮滚动，鼠标轨迹、人工暂停照旧生效。
  - `bottom_then_top`：每次向下滚一屏，直到滚动位置不再变化（最多 30 屏），再回到顶部。
  - `to_text`：在元素清单里找文字包含 `text` 的元素，滚到它进入视口；找不到就退回 `bottom_then_top`，并在记录里写明原因。
- `open`：
  - 用来源清单第 n 项的元素 id，在当前页面状态里找到元素。驱动支持标签页就 `open_tab`，否则 `click`。
  - 进入后跑子流程。`back` 为真时，关掉标签页或执行 `page.back()`。
  - 打开动作照样经过拦截、需确认、暂停的检查。
- `page`：在元素清单里找名字匹配 `next_text` 的链接（或页码 `to`）并点击，找不到就记"没有下一页"。

### C. 4b 选编号（`taskbook/pick.py`，提示词 `pick_list.md`、`pick_fields.md`）
- `pick_list`：
  1. 有锚点时先滚到锚点。
  2. 每一屏：`build_page_state` → `PageDigest.build`（标注图 + 元素清单）。
  3. 问 4b："哪些编号是「{region}」里的条目？按页面顺序只回编号；这一屏没有就回空。"返回格式是 `{"numbers": [int]}`。
  4. 程序把编号换成元素：
     - 编号不存在 → 拒绝并记录；
     - 元素在锚点之上 → 拒绝，记"在锚点之前"。
  5. 文字取元素的 `name` 或 `text`，摘要取 `context`，按规范化后的原文去重。
  6. 下滚一屏继续，直到满足以下任一条件：已取够 `max` 条；已经找到过条目、这一屏却回空（区块结束）；到底；达到 `screens` 屏数。
  7. 格式错误按现有重问机制重试 3 次，仍失败就产出空清单。
- `read_fields`：
  - 当前屏的元素编号之外，再加上**文本行编号**（`T1…Tn`）。文本行来自正文按行切分、去空、去重，最多 120 行，每行最多 80 字，用 `digest.text_lines()` 新增。
  - 问 4b："字段 X 在哪个编号？"返回格式是 `{"fields": [{"name", "no"}]}`。
  - 程序取该元素或该行的完整原文作为字段值，所以值必定在页面上逐字存在。没找到的字段记"未找到"。
  - `name_field` 的值作为这一条的 `text`。

### D. 计算积木（`taskbook/calc.py`，纯函数）
- 文字比较统一用 `normalize`（全角转半角、去空白；`ignore_case` 时转小写）。
- `ratio`：对 `words` 里每个词（`drop_current` 时去掉当前词），数 `in` 的前 `top` 条里正文或摘要包含它的条数，比例 ≥ `at_least` 的输出。每个输出项的 `origin` 里记下比例和命中了哪几条。
- `rank`：第 k 名（从 1 开始，越界产出空并记录），或前 k 个。
- `filter`：依次执行别名归一（`contains` 时包含即算）→ 停用词 → 去当前词 → 去已知词 / 只留已知词 → 去重。每个被滤掉的项都记下原因。
- `compare`：
  - 价位字段用改进后的 `parse_price`，新增"在一段文字里找出 `$`～`$$$$` 或金额"；其它字段取第一个数字；`contains` 按文字比较。
  - 比较值用同样的方式解析。识别不了的项进 `unknown_as` 清单。

### E. 运行器（`taskbook/runner.py`）
- `TaskbookRunner(browser, config, recorder, book, gate, confirm_fn, handoff_fn, client, trial=False, on_change, ...)`，大部分沿用 ExpandRunner 的骨架：主循环、等待决策、标签页保留、快照、报告页。
- 每个节点：`mark_searched` → `engine.run_node` → 节点执行期间如果被用户改过，本轮结果作废 → 应用 `TreeOp`（来源 `rule`，带依据清单和步骤）→ 没有任何生效的改动就保持现有的暂定叶子规则，所在根跑完后统一转为末端叶子。
- 次数由引擎统计，通过 `tree.consume(cost)` 记账，不再用钩子猜测。
- 试跑：上限取 `limits.trial_budget`（或请求里指定），记录目录名带 `trial`。
- 结果：
  - 末端叶子清单，每条附依据（来源步骤、清单项原文、网址）；
  - 排序输出按 `output.sort` 里的字段名排序：价位用 `parse_price`，其它取数字；`unknown: last | exclude`。
- 报告页和结果整理（原 `ExpandRunner.report_html` / `build_result`）搬到 `taskbook/report.py`，加上每次消耗的积木产出。

### F. 转人工检查（`hands/checks.py`）
- 把 CaseRunner 里"验证码 / 登录 / 系统对话框 → 转人工、已处理过的不再重复转"的逻辑原样搬成 `HandoffChecker`。CaseRunner 改为调用它，行为不变（N1）。

### G. 任务书服务（`panel/books.py`）与接口

| 接口 | 用途 |
|---|---|
| `GET /api/books` | 列表：name、title、状态（可运行 / 草稿 / 需要改写）、rev、开放参数 |
| `GET /api/books/{name}` | 文本、解析结果、校验问题、rev |
| `GET /api/books/blocks` | 积木注册表，供前端生成表单 |
| `POST /api/books/validate` | 校验一段文本 |
| `POST /api/books/import` | 粘贴文本或上传文件；旧格式自动转换；返回文本和问题，不落盘 |
| `POST /api/books/save` | 带 rev；不一致返回 409"已被另一端修改，请刷新"；覆盖前把旧版存到 `tasks/.bak/` |
| `POST /api/books/preview` | 不联网：初始树和处理顺序（用 ExpandTree 模拟，每个节点都当作搜后没有变化） |
| `POST /api/books/run` | 按名称或草稿文本、参数值、上限、是否试跑发起任务；试跑可以用未保存的草稿 |
| `GET /api/books/steps?run=` | 一次运行每次消耗的记录（截图、各积木产出、滤掉的项、树的变化） |
| `GET /api/books/guide` | 指导提示词：模板加上从注册表生成的积木说明，示例取改写后的两份夹具任务书 |
| `/api/expand/tree`、`/api/expand/edit` | 保留；快照带 `rev`，修改请求必须带 `rev` |

`JobManager` 里新的任务种类 `book` 取代 `expand`。流程里有 4b 积木时，要求本机 Ollama 可用，否则提示后拒绝启动。

### H. 远程访问（`panel/remote.py`、`tunnel.py`、`devices.py`）
- `RemoteAccess`：状态有 `off` / `lan` / `lan+public`。
  - 开启时启动第二个 aiohttp 站点，监听 `0.0.0.0:8791`（端口可配置），与本机入口共用同一个 svc。
  - 第一次监听时 Windows 防火墙会弹窗，需要在"专用网络"里允许；面板上会提示这一点。
- 远程入口的中间件：
  1. Host 必须是本机的局域网 IPv4 加端口，或本次隧道的域名，用来防 DNS 重绑定。
  2. Origin 必须等于当前访问地址（`https://<域名>` 或 `http://<内网IP>:端口`）。
  3. 除 `/pair`、`/api/pair`、`/static/*` 之外，都要求 cookie `dev = <设备id>.<凭证>`：按 id 查设备，比对 sha256 摘要，并更新最近访问时间。
  4. **只放行白名单路由**：状态、任务、暂停 / 恢复 / 确认、树、任务书、运行记录、`/ws`、`/m`、`/tree`、`/build`。其余一律返回 403"只能在电脑上操作"。
  5. CSP 里的 connect-src 按当前地址生成（`wss://<域名>`）。
- 配对流程：
  1. 电脑卡片显示二维码，内容是 `<地址>/pair#c=<配对码>`。配对码放在 `#` 后面，不进服务器日志，也不会出现在 Referer 里。配对码每 5 分钟自动换新。
  2. 平板打开后，页面脚本读出配对码，连同浏览器标识一起 `POST /api/pair`。
  3. 服务器校验：配对码存在、未用过、未过期、地址与签发时一致。通过后建设备记录，下发 cookie：`HttpOnly`、`SameSite=Strict`，https 下加 `Secure`；局域网的有效期 1 年，公网的只在本次会话内有效。然后跳到 `/m`。
- 吊销：删除设备记录，再按 `设备id → 连接集合` 立即关闭它打开的 WebSocket。
- `Tunnel`：
  - 启动方式是 `cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8791`，从输出里匹配 `https://*.trycloudflare.com`，30 秒内拿不到就判定失败。
  - 进程退出时发出 `tunnel_down` 事件，电脑面板据此提示"可一键重开"。
  - `cloudflared` 的路径取配置项或 PATH；找不到时提示"`winget install Cloudflare.cloudflared`"。
- 无人访问自动关闭：每 60 秒检查一次。公网设备中最近一次访问的时间（没有就用隧道开启时间）距今超过 `idle_close_s`（默认 7200 秒）时，关闭公网，同时删除公网设备。
- 日志：开关、配对、吊销、自动关闭都写进 `<数据目录>/logs/remote.jsonl`。

### I. 实时推送给平板
- 复用 `/ws` 事件中心。远程连接改成**缩略图模式**：不转发推流帧，最多每秒发一张缩略图（宽 640、JPEG）；收到的输入消息一律忽略（不提供远程点击）。
- 事件里已有待确认、转人工、次数用完三种情况，平板据此弹出醒目提示，同时 `navigator.vibrate`，并用 WebAudio 响一声。Safari 要求用户点过一次页面才能出声，所以第一次点击时解锁声音。
- 断线后每 2 秒重连一次，重连后重新拉取 `/api/state` 和树。

### J. 前端
- `build.html/js/css` 搭建工具：
  - 上方：名称、开放参数、初始组；中间：流程列表（增删、上下移动）；
  - 选中某个积木时，表单按 `ParamSpec` 生成；清单引用用下拉框，只列出前面已产出的清单名；
  - 底部：校验问题；按钮：校验、预览、试跑、保存、另存为、导入、指导提示词；
  - 试跑面板：每次消耗一张卡片，带截图、可展开的各积木产出和滤掉的项。
  - 宽屏左右两栏，窄屏上下排列。
- `m.html/js/css` 平板外壳：四个标签页（状态、树、任务书、记录）加顶部提示条。树和任务书两页直接嵌入响应式的 `tree` 和 `build`。
- 电脑首页加"远程访问"卡片：档位、地址、二维码（用 `segno` 在服务器端生成 SVG）、设备清单（改名、吊销）。

### K. 命令行
- `python -m hands book <任务书.yaml> [--param k=v] [--budget N] [--trial]`
- 原来的 `expand` 子命令改成同一入口的别名；拿到旧格式文件时，按转换结果提示"需要用积木改写"。

## 模块交互

### 1. 搭建、导入、保存（F3–F5）
```
build.js 打开
  → GET /api/books/blocks ─→ blocks.REGISTRY（生成表单）
  → GET /api/books/{name} ─→ book.load → validate.check → {text, book, problems, rev}
用户改表单 → 前端把表单拼回 YAML 文本
  → POST /api/books/validate ─→ book.parse → validate.check → problems（逐条显示）
  → POST /api/books/save {name, text, rev, save_as?}
        ─→ book.parse → validate.check（不通过也能存，status=draft）
        ─→ 磁盘 rev ≠ 请求 rev → 409「已被另一端修改，请刷新」
        ─→ 旧文件复制到 tasks/.bak/<name>.<时间>.yaml → 写新文件 → 新 rev
导入：POST /api/books/import {text | file}
        ─→ convert.detect（无 format 字段 = 旧格式）
             旧格式 → convert.from_legacy（经 expand/spec.py 读结构）→ status=needs_rewrite
             新格式 → book.parse
        ─→ validate.check → 返回 {text, problems}，前端进入搭建工具（不落盘）
指导提示词：GET /api/books/guide ─→ guide.build(REGISTRY, 模板, 两份夹具任务书) → 文本（复制 / 下载）
```

### 2. 预览、试跑、正式运行（F6、F7）
```
预览：POST /api/books/preview ─→ book.parse → ExpandTree(groups, start)
        → 反复 next_node / mark_searched / finish（不联网）→ {初始树, 处理顺序}

发起：面板 / 平板 POST /api/books/run {name | text, values, budget?, trial}
     或命令行 python -m hands book …
  → validate.check（有问题拒绝启动，返回问题清单）
  → 流程含 pick_list / read_fields 时检查 Ollama，不可用就拒绝
  → JobManager.start(kind="book") → 建运行目录（试跑带 -trial）
  → TaskbookRunner.run()
       循环：tree.next_node()
         → tree.mark_searched(node)
         → engine.run_node(node, tree, book, ctx)
              每块前：gate.wait → should_stop → 剩余次数
              search / open / page → web.* → ActionExecutor → page
                                   → checks.HandoffChecker.check_page（需要时 handoff_fn 等人）
              pick_list / read_fields → pick.* → PageDigest → 4b → 编号换原文
              ratio / rank / filter / compare → calc.*
              tree → 登记 TreeOp；stop → NodeRun.stop
         → tree.consume(NodeRun.cost)
         → 节点执行期间树被人改过（rev 变了且动到该节点）→ 本轮作废，节点退回待查
         → 依次 tree.apply(TreeOp…)（来源 rule）→ 都没生效 → 暂定叶子规则
         → recorder 写本节点记录 → 快照（带 rev）→ on_change
         → NodeRun.stop=finish → 结束；=handoff 或次数用完 → 等待决策（再给 N 次 / 结束并输出）
       结束：report.build_result（叶子 / 排序输出）→ report.html
  on_change → 事件中心 → /ws：电脑收完整事件和推流帧；远程连接收事件和缩略图
```

### 3. 运行中改树与两端冲突（F7、F12）
```
电脑 /tree 或平板 /m 的树页
  → POST /api/expand/edit {op, …, rev}
       ─→ JobManager.expand_edit：tree.rev ≠ 请求 rev → 409「已被另一端修改，请刷新」
       ─→ 否则执行修改，tree.rev+1，记 edits.jsonl {时间, 来源, op}
       ─→ 快照 → /ws 推给两端，两端都按新快照重画
来源：本机入口记「电脑」；远程入口由中间件把设备名放进 request，记「平板 · Safari」之类。
暂停 / 恢复 / 确认 / 取消同样记来源。
```

### 4. 远程开关与配对（F10、F11）
电脑专用的管理接口（只在本机入口，不在远程白名单里）：

| 接口 | 用途 |
|---|---|
| `GET /api/remote` | 档位、地址、配对码有效期、设备清单、隧道状态、cloudflared 是否可用 |
| `POST /api/remote/mode {mode}` | off / lan / lan+public |
| `POST /api/remote/code` | 立即换新配对码 |
| `GET /api/remote/qr.svg?kind=lan|public` | 二维码 |
| `POST /api/remote/devices/rename`、`/revoke` | 改名、吊销 |

```
电脑卡片选「仅局域网」
  → POST /api/remote/mode lan → RemoteAccess.start_lan
       → 找局域网 IPv4 → 起第二个站点 0.0.0.0:8791 → 签配对码（origin=http://IP:8791）
  → 卡片拉 /api/remote 与 qr.svg 显示
选「局域网 + 公网」
  → RemoteAccess.start_public → Tunnel.start（cloudflared 子进程，读输出拿 https 地址）
       → 拿到地址 → 允许的 Host 加上隧道域名 → 签公网配对码 → 二维码
       → 失败 / 没有 cloudflared → 返回原因，档位留在 lan
平板扫码 → GET <地址>/pair（pair.html）→ 脚本读 #c → POST /api/pair {code, ua}
  → devices.redeem(code, origin) → 新 Device（kind 由 origin 判断）→ Set-Cookie dev=…
  → 跳 /m → m.js 连 /ws、拉 /api/state 与树
之后每个请求：Host 检查 → Origin 检查 → cookie → devices.verify → 白名单 → 原接口处理
吊销：/api/remote/devices/revoke → devices.remove → 事件中心关掉该设备的 WS
自动关闭：
  面板退出 → RemoteAccess.stop（关隧道、关第二站点、删公网设备）
  空闲检查（60 秒一次）→ 超过 idle_close_s → 关公网、删公网设备 → 推 remote_changed
  隧道进程意外退出 → tunnel_down → 删公网设备 → 电脑卡片提示「可一键重开」
全部写 logs/remote.jsonl
```

### 5. 记录（F13）
每次运行目录（沿用 `runs/<时间>-<id>-...`）下新增 `book/`：

```
book/
├── taskbook.yaml          — 所用任务书的完整副本
├── params.json            — 参数值、上限、是否试跑、驱动
├── nodes/
│   └── 0003_megatron/
│       ├── blocks.json    — NodeRun.blocks：每块参数 / 输入 / 产出（原文 + 依据元素）/ 4b 选号 / 被拒 / 滤掉的项及原因 / 改树操作及依据
│       └── s01.png …      — 每屏截图（pick_list 每屏一张，网页积木后各一张）
├── snapshots/0001.json …  — 每次树变化的快照（带 rev，可按时间回看）
├── edits.jsonl            — 运行中的修改与控制操作（时间、来源、内容）
├── result.json
└── report.html
```

密码框的值沿用现有遮蔽，不写进任何文件。`/api/books/steps` 直接读这个目录。

## 文件组织

```
src/hands/
├── taskbook/                     ★ 新包
│   ├── __init__.py
│   ├── blocks.py                 — ParamSpec、BlockSpec、REGISTRY（12 个积木）、内置清单名
│   ├── book.py                   — TaskBook、parse / load / dump / save（备份、rev）、参数替换
│   ├── validate.py               — Problem、check()
│   ├── convert.py                — detect()、from_legacy()
│   ├── guide.py                  — build()：模板 + 注册表 + 示例
│   ├── engine.py                 — Item、TreeOp、NodeRun、run_node()
│   ├── web.py                    — search / scroll_all / open / page
│   ├── pick.py                   — pick_list / read_fields
│   ├── calc.py                   — ratio / rank / filter / compare
│   ├── runner.py                 — TaskbookRunner
│   └── report.py                 — build_result、report_html（由 expand/run.py 搬来）
├── checks.py                     ★ 新：HandoffChecker（从 runner.py 抽出）
├── runner.py                     — 改：CaseRunner 调 HandoffChecker；按域名访问间隔抽成可共用函数
├── expand/
│   ├── tree.py                   — 改：rule 来源、new_terminal、rev
│   ├── rank.py                   — 改：parse_price 能从长文字里找价位
│   ├── spec.py                   — 保留（只供 convert 读旧格式）
│   ├── judge.py / subtask.py / run.py   — 删
├── vlm/
│   ├── digest.py                 — 改：新增 text_lines()
│   └── prompts/
│       ├── pick_list.md          ★ 新
│       ├── pick_fields.md        ★ 新
│       ├── taskbook_guide.md     ★ 新：指导提示词模板
│       └── expand_judge.md / expand_node.md — 删
└── __main__.py                   — 改：book 子命令，expand 作别名

src/panel/
├── books.py                      ★ 新：任务书服务
├── remote.py                     ★ 新：RemoteAccess、远程中间件、白名单
├── tunnel.py                     ★ 新：Tunnel
├── devices.py                    ★ 新：Device、PairCode、DeviceStore
├── server.py                     — 改：任务书 / 远程管理 / 配对路由；/ws 缩略图模式；edit 带 rev 与来源
├── jobs.py                       — 改：kind=book 取代 expand；edits 记来源
├── preview.py                    — 改：缩略图节流给远程连接
└── static/
    ├── build.html / build.js / build.css   ★ 新
    ├── m.html / m.js / m.css               ★ 新
    ├── pair.html                           ★ 新
    ├── tree.html / tree.js / tree.css      — 改：响应式、rev
    └── index.html / app.js / style.css     — 改：远程访问卡片、任务书入口

tasks/
├── fixture_transformers.yaml、fixture_dinner.yaml        — 改写为 taskbook/1（自动验收）
├── transformers.yaml、dinner.yaml、dinner_yelp.yaml      — 改写为 taskbook/1（人工验收）
└── .bak/                                                 — 保存备份（gitignore）

tests/
├── unit/
│   ├── data/legacy/*.yaml        ★ 5 份旧格式原件（转换测试）
│   ├── test_taskbook_blocks.py   ★ 注册表、表单字段与指导提示词一致（AC8）
│   ├── test_taskbook_book.py     ★ 解析、参数替换、保存备份、rev
│   ├── test_taskbook_validate.py ★
│   ├── test_taskbook_convert.py  ★
│   ├── test_taskbook_calc.py     ★ ratio / rank / filter / compare、parse_price
│   ├── test_taskbook_pick.py     ★ 替身模型：编号换原文、拒绝、锚点、重试
│   ├── test_taskbook_engine.py   ★ 假页面：顺序、出错继续、stop、改树延后应用
│   ├── test_taskbook_runner.py   ★ 替身：夹具任务书全流程、试跑、等待决策、rev 作废
│   ├── test_checks.py            ★ HandoffChecker 与原行为一致
│   ├── test_remote_devices.py    ★ 配对码有效期 / 一次性 / origin、凭证摘要、吊销
│   ├── test_remote_access.py     ★ Host / Origin / cookie / 白名单 / 空闲关闭（假时钟）
│   ├── test_tunnel.py            ★ 假 cloudflared：地址解析、超时、退出事件
│   ├── test_expand_tree.py       — 改：rev、new_terminal、rule
│   ├── test_expand_report.py     — 改为测 taskbook/report.py
│   └── test_expand_judge.py      — 删
├── panel/
│   ├── books_check.py            ★ 面板接口：保存冲突 409、导入、预览、试跑、steps
│   ├── remote_check.py           ★ 局域网站点 + 模拟平板：配对、/m、/ws 缩略图、吊销断开、两端冲突
│   └── fake_cloudflared.py       ★ 打印假隧道地址的替身
└── acceptance/
    ├── d12_expand.py             — 改：跑 fixture_transformers 任务书（替身模型）
    └── d17_dinner.py             — 改：跑 fixture_dinner 任务书

config.yaml                       — 改：新增 remote:（port、idle_close_s、cloudflared 路径）
pyproject.toml                    — 改：panel 依赖加 segno
.gitignore                        — 改：tasks/.bak/、panel_devices.json
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 积木定义来源 | 一个注册表生成校验、表单、指导提示词 | 三处永远一致，格式变了只改一处（F5、AC8） |
| 积木 id | 英文 id，界面显示中文名 | AI 起草和手写都不易写错；清单名允许中文，读起来直观 |
| 4b 的职责 | 只回编号，文字由程序按编号逐字取 | 产出必在页面上（N3）；4b 不做语义判定，避开 D2、D3 |
| 读详情字段的编号范围 | 元素编号 + 文本行编号 | 价位、评分常是不可点的纯文字，只有元素编号会漏 |
| 改树时机 | 节点跑完后按顺序统一应用 | 同批操作看到一致状态；节点中途被人改树可整轮作废 |
| 积木出错 | 产出空清单、记录原因、继续 | 一块失败不拖垮整个节点，记录里能看到哪块错了 |
| 展开规则 | 沿用 `ExpandTree`，只加 rule / new_terminal / rev | 上一章已验证的顺序、叶子、标签页规则不重写（F1） |
| 转人工检查 | 从 CaseRunner 抽出共用 | 积木和原用例行为一致，回归不变（N1） |
| 旧格式 | 自动转换结构，条件进备注，标需要改写 | 不丢信息，又不让未改写的任务书跑起来（F5） |
| 版本冲突 | 树用整数 rev；任务书用内容摘要 | 树改动频繁，计数简单；文件可能被外部编辑，摘要能发现 |
| 远程入口 | 第二个 aiohttp 站点，与本机入口分开 | 本机入口的令牌鉴权一行不改（N1）；远程单独加 Host / Origin / 凭证 / 白名单 |
| 公网穿透 | cloudflared 临时隧道 | 不用注册账号、自带 HTTPS；关掉即地址失效 |
| 配对码位置 | 放在网址 `#` 之后 | 不进服务器日志、不随 Referer 外泄 |
| 设备凭证 | HttpOnly cookie，服务器只存 sha256 | 页面脚本读不到；WS 自动带上；凭证是 32 字节随机串，快速摘要足够 |
| 远程白名单 | 只放行列出的路由，其余 403 | 新加的接口默认不对外，电脑专用操作不会漏出去（F11） |
| 远程画面 | 每秒最多一张 640 宽缩略图，不收输入 | 隧道带宽小；spec 明确不做远程点击（F9） |
| 提醒 | vibrate + WebAudio 合成提示音 | 不需要音频文件；首次点击解锁兼容 Safari |
| 二维码 | `segno` 生成 SVG | 纯 Python、无其它依赖，SVG 在电脑卡片上清晰 |
| 前端 | 原生 JS，与现有页面一致 | 不引入构建工具；tree / build 用响应式 CSS 两端复用 |
| 命令行 | 新子命令 `book`，`expand` 作别名 | 旧命令仍可用，遇到旧格式给出明确提示 |
