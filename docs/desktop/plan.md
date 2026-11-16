# 桌面驱动与关联展开任务 Plan

日期：2026-11-14　状态：待审批（阶段二）　依据：`docs/desktop/spec.md`（已批准）
语言与环境：Python 3.12，Windows 11，Chrome 156，沿用 asyncio + aiohttp 面板。

## 架构概览

本章在现有分层上加三块，原有模块只做"接线"级改动：

```
                 ┌──────────── 面板（panel）────────────┐
                 │ 设置 / 状态 / 预览 / 确认 / 任务树页   │
                 └───────┬───────────────▲──────────────┘
                         │ HTTP + WS     │ 状态推送
┌────────────────────────▼───────────────┴──────────────────────────┐
│ 控制层                                                            │
│  ExpandRunner（新，关联展开外层）── 每个节点 → CaseRunner（现有） │
│  PauseGate（新，暂停/恢复）  ConfirmPolicy（新，需确认判断）      │
├───────────────────────────────────────────────────────────────────┤
│ 决策：RuleDecider / VlmDecider（现有）  ExpandJudge（新，4b 判定） │
├───────────────────────────────────────────────────────────────────┤
│ 动作：ActionExecutor（现有 + 右键、网格、密码、确认、打断）        │
│ 兜底：Guard（现有，不改）                                         │
├───────────────────────────────────────────────────────────────────┤
│ 驱动接口 BrowserLike / PageLike（现有，加 3 个可选方法 + 能力声明）│
│   CDP（现有）   Playwright（现有）   Desktop（新）                 │
│                                       ├ win32：窗口、SendInput、对话框 │
│                                       ├ inputwatch：低级钩子      │
│                                       ├ uia：无障碍树             │
│                                       └ screen：截图、稳定判断    │
└───────────────────────────────────────────────────────────────────┘
```

- **桌面驱动（`hands/desktop/`）**：实现现有的 `BrowserLike` / `PageLike`。看页面用"窗口截图 + UIA 无障碍树"，动作用 `SendInput`，坐标在驱动内部从屏幕物理像素换算成视口 CSS 像素。上层（runner、actions、vlm、guard、captcha、面板）不判断驱动名，只看驱动声明的 `capabilities`。
- **人在环中（`hands/pause.py`、`hands/confirm.py`）**：`PauseGate` 统一管理暂停与恢复（人工输入、面板按钮、命令行、改树都走它），与驱动无关；`ConfirmPolicy` 判定需确认动作与登录类转人工，桌面驱动默认开启，CDP / Playwright 默认关闭。
- **关联展开（`hands/expand/`）**：`ExpandTree` 是纯逻辑的键值树，`ExpandRunner` 按展开规则挑节点，每个节点生成一个子用例交给现有 `CaseRunner` 执行（搜索、必要时打开阅读、用 `extract` 列出候选），再由 `ExpandJudge` 调 4b 判定增减节点。现有 `TaskTree`（母子任务）不改，作为每个节点子用例内部的任务树。

## 核心数据结构

### 驱动能力声明（`hands/driver.py`）
```python
@dataclass(frozen=True)
class Capabilities:
    network_log: bool = True      # 有网络请求列表（等待超时能给出未完成请求）
    screencast: bool = True       # 有 CDP 会话可推流、可在预览上手动输入
    js: bool = True               # 能在页面里执行脚本（evaluate）
    os_input: bool = False        # 动的是系统真实鼠标键盘（需要人工输入暂停）
    grid: bool = False            # 支持网格兜底
    right_click: bool = False     # 支持右键菜单
    confirm_default: bool = False # 需确认判断默认开启
```
CDP、Playwright 驱动用默认值（与现在行为一致）；桌面驱动为 `Capabilities(False, False, False, True, True, True, True)`。驱动对象上加 `capabilities` 属性，上层用 `getattr(browser, "capabilities", Capabilities())` 读取。

### Element 增加字段（`hands/dom.py`，带默认值，向后兼容）
```python
source: str = "dom"            # dom / uia / grid
is_password: bool = False      # 密码框（值恒为 None）
```

### 桌面元素引用（`hands/desktop/uia.py`）
```python
@dataclass
class UiaRef:
    runtime_id: tuple[int, ...]    # UIA RuntimeId，用于重新定位与"是不是同一个元素"
    element: Any                   # IUIAutomationElement（COM 指针，仅本步有效）
    epoch: int                     # 读取时的页面纪元；纪元变了再用即判失效
```
`Element._ref` 在桌面驱动下存 `UiaRef`；网格元素的 `_ref` 为 `GridRef(cell_no, box)`。

### 坐标换算（`hands/desktop/coords.py`，纯函数）
```python
@dataclass(frozen=True)
class Frame:
    content_left: int; content_top: int       # 网页内容区左上角，屏幕物理像素
    content_w: int; content_h: int            # 内容区宽高，物理像素
    scale: float                              # 物理像素 / CSS 像素 = 显示缩放 × 浏览器缩放（要求浏览器缩放 100%）
    def to_css(self, sx, sy) -> tuple[float, float]: ...
    def to_screen(self, cx, cy) -> tuple[int, int]: ...
    def box_to_css(self, rect) -> Box: ...
    def contains_screen(self, sx, sy) -> bool: ...
```

### 暂停门（`hands/pause.py`）
```python
@dataclass
class PauseInfo:
    reason: str            # human_input / user / budget / edit / confirm
    since: float
    detail: dict

class PauseGate:
    paused: PauseInfo | None
    def pause(self, reason: str, detail: dict | None = None) -> None
    def resume(self, how: str) -> None                 # how: button / cli / idle
    def check(self) -> None                            # 已暂停则抛 Interrupted（动作中途调用）
    async def wait(self) -> float                      # 未暂停立即返回 0；否则等到恢复，返回暂停秒数
    def on_human_input(self, t: float) -> None         # 输入监听回调
    def arm(self, after_s: float) -> None              # 恢复后宽限期，期内人工输入不触发暂停
    log: list[dict]                                    # {start, end, reason, how}
class Interrupted(Exception): ...
```
自动恢复：`idle_resume_s > 0` 且暂停原因是 `human_input` 时，后台任务在"最后一次人工输入"之后满 N 秒调用 `resume("idle")`。

### 需确认策略（`hands/confirm.py`）
```python
@dataclass
class ConfirmRequest:
    kind: str            # account_change / form_submit / unverifiable_point
    tool: str
    target: dict         # 角色、名称、文字、落点
    hit: str             # 命中的词或规则
@dataclass
class ConfirmPolicy:
    enabled: bool
    account_words: list[str]       # 加入购物车、收藏、关注、写评价、发消息、预订……（中英，可增删）
    submit_words: list[str]        # 提交、保存、继续、Submit、Save、Continue……
    login_words: list[str]         # 登录、Sign in、验证码、短信……（网址、标题、正文命中）
    @classmethod
    def from_config(cls, cfg: dict | None, default_on: bool) -> "ConfirmPolicy"
    def check_click(self, el: Element) -> ConfirmRequest | None
    def check_enter(self, focused: Element | None) -> ConfirmRequest | None   # 非搜索框里按回车
    def login_hit(self, state: PageState) -> str | None                       # 有密码框 / 登录词 → 转人工
ConfirmFn = Callable[[ConfirmRequest], Awaitable[bool]]   # True = 允许一次
```

### 关联展开：任务类型定义（`hands/expand/spec.py`）
```python
@dataclass
class Condition:
    id: str
    text: str                 # 自然语言，给 4b 看
    params: dict              # 如 {"ratio": 0.8}，渲染进 text 的 {ratio}
@dataclass
class TaskType:
    name: str
    title: str
    search_url: str           # 如 "https://www.google.com/search?q={q}"
    query: str                # 如 "{value}" 或 "{key} {value}" 或 "便宜 {value} ${location}"
    groups: list[tuple[str, list[str]]]   # [(键, [值…])]，有序
    start: str
    sources: list[str]        # 候选词来源描述（右侧相关搜索、结果标题、排行榜……）
    conditions: list[Condition]
    budget: int = 120
    keep_tabs: int = 30
    read_per_node: int = 2    # 每个节点最多打开阅读几个页面
    node_steps: int = 12      # 每个节点子用例的步数上限
    output: dict              # {"type": "leaves"} 或 {"type": "ranked", "fields": [...], "sort": [...], "n": 5}
    params: list[dict]        # 开放给用户填的参数 {name, label, default}
def load_task_type(path, overrides: dict | None = None) -> TaskType   # 不合法抛 TaskTypeInvalid（写明哪一项）
```

### 关联展开：树（`hands/expand/tree.py`，纯逻辑）
```python
NodeState = Literal["pending", "key", "tentative", "terminal", "deleted"]

@dataclass
class ExNode:
    id: str
    word: str
    parent: str | None        # 所在键节点 id（根为 None）
    state: NodeState
    origin: str               # initial / model / user
    searches: int = 0
    reasons: list[dict]       # [{t, actor, op, reason, evidence}]
    fields: dict              # 点餐类抽取到的字段（店名、价位、评分、链接）及是否有依据
    tab: str | None = None    # 保留的标签页句柄（驱动给的 id）
    url: str | None = None
    children: list[str]

@dataclass
class ExEvent:
    t: float; actor: str      # model / user / system
    op: str                   # create / search / key / tentative / terminal / delete / add_value / new_key /
                              # set_pending / add_group / grant / pause / resume / finish
    node: str | None; detail: dict

class ExpandTree:
    def __init__(self, tt: TaskType)
    mapping: dict[str, list[str]]       # 键 → 值（初始组 + 运行中新增）
    roots: list[str]                    # 根节点队列（起始词 + 各组键，按顺序；新键追加到当前根之后）
    used: int; budget: int
    def next_node(self) -> ExNode | None          # 按展开规则挑下一个要搜的节点
    def mark_searched(self, nid: str) -> None      # used += 1, searches += 1
    def consume(self, n: int = 1) -> None          # 阅读消耗
    def apply(self, nid: str, ops: list[dict], actor: str) -> list[dict]   # 返回被拒的项
    # 用户操作（面板 / 下一章手机共用）
    def delete(self, nid, actor="user"); def set_terminal(self, nid, actor="user")
    def set_pending(self, nid, actor="user"); def add_group(self, key, values, actor="user")
    def grant(self, n: int, actor="user")
    @property
    def finished(self) -> bool; @property
    def exhausted(self) -> bool
    def snapshot(self) -> dict          # 节点、状态、计数、曲线点（每次变化后的叶子数/节点数/已用次数）
    events: list[ExEvent]
```
展开规则落在 `next_node`：
1. 当前根的子树里按"深度优先、组内按值的先后"找第一个 `pending` 节点；值的词本身是键（在 `mapping` 里）且不是自己祖先上的键 → 搜完后转 `key` 并按 `mapping` 生成子节点；是祖先上的键（环）→ 搜完直接 `terminal`，理由"循环"。
2. 当前根子树没有 `pending` 了，换到 `roots` 里下一个**尚未作为根或分叉展开过**的键（例：megatron 已在 optimus 下分叉过，不再单独作为根；movie 没展开过，作为下一个根）。
3. 运行中 `new_key` 产生的键追加到"当前根之后"的位置（例：movie 组里建出的 decepticon 排在 movie 之后）。
4. 同一个词在不同键下是不同节点，各自搜索、各自判定。
5. `finished`：没有 `pending`，且所有未删除的值节点都是 `terminal`。

### 关联展开：判定（`hands/expand/judge.py`）
4b 回答格式（JSON）：
```json
{"ops": [
  {"op": "add_value", "key": "megatron", "word": "shockwave", "from": 3, "condition": "c1", "reason": "..."},
  {"op": "new_key", "word": "starscream", "from": 5, "condition": "c2", "reason": "..."},
  {"op": "terminal", "reason": "..."},
  {"op": "delete", "reason": "..."}
]}
```
`from` 是本节点子用例里 `extract` 结果的序号（候选清单编号），只有**有依据**的候选才能被引用；`add_value` 的 `key` 必须是已知键或当前词。程序检查：操作在允许集合内、`from` 指向有依据的候选、`word` 与候选值一致；不满足的项拒绝并记"无依据 / 不允许的操作"。

## 模块设计

### hands/desktop/win32.py（ctypes，无新依赖）
**职责：** 进程 DPI 感知（Per-Monitor V2）；找默认浏览器（注册表 `UrlAssociations\https\UserChoice` → ProgId → `shell\open\command`）；枚举 Chrome 顶层窗口（类名 `Chrome_WidgetWin_1`）；前台、还原、移动窗口、`IsWindow`、`IsHungAppWindow`；`SendInput`（鼠标绝对坐标移动、左/右/中键、双击、滚轮、虚拟键、`KEYEVENTF_UNICODE` 文本），每个注入事件的 `dwExtraInfo` 填固定标记；查找属于 Chrome 进程的系统对话框（类名 `#32770` 等）。
**对外接口：** `set_dpi_aware()`, `default_browser() -> (progid, exe)`, `chrome_windows() -> set[int]`, `launch_new_window(exe, url) -> None`, `focus(hwnd)`, `place(hwnd, rect)`, `alive(hwnd)`, `hung(hwnd)`, `move_abs(x, y)`, `button(kind, down)`, `wheel(delta)`, `key(vk, down)`, `text(s)`, `owned_dialogs(pid) -> list[int]`, `INJECT_MARK`。
**依赖：** 无。

### hands/desktop/inputwatch.py
**职责：** 独立线程安装 `WH_MOUSE_LL` / `WH_KEYBOARD_LL` 钩子并跑消息循环；`dwExtraInfo == INJECT_MARK` 的事件判为本程序注入，其余一律判为人工输入；人工输入时回调 `PauseGate.on_human_input`（跨线程用 `loop.call_soon_threadsafe`）；记录最后一次人工输入时间。
**对外接口：** `InputWatch(loop, on_human).start() / stop()`, `last_human: float`, `classify(flags, extra) -> "human" | "self" | "other"`（纯函数，可单测）。
**依赖：** win32。

### hands/desktop/uia.py（可选依赖 comtypes）
**职责：** 通过 `IUIAutomation` 读代理窗口的无障碍树：
- 定位当前标签页的 `Document` 控件（它的位置框就是网页内容区）；一次 `CacheRequest` + `FindAll(Subtree)` 取 ControlType、Name、AriaRole、BoundingRectangle、IsEnabled、IsOffscreen、IsPassword、ValuePattern.Value、ClassName。
- 角色映射：ControlType + AriaRole → 现有角色名（link、button、textbox、searchbox、checkbox、combobox、menuitem、tab……）；链接的 ValuePattern 值即链接地址。
- 浏览器自身界面（Document 之外）不进元素清单，只读：地址栏（ClassName `OmniboxViewViews`）的值作网址、重新加载按钮（ClassName `ReloadButton`）的名称判断是否在加载、标签条（`Tab`）判断标签页数量与当前页、右键菜单项（`MenuItem`，出现时并入元素清单）。
- 正文：Document 子树内 Text 控件名称按文档顺序拼接；`context` 取最近的 listitem / group 祖先的文字（截断）。
- 点查询：`ElementFromPoint` → 用于遮挡判断和网格落点核对。
- 第一次读取时 Chrome 才开启无障碍支持：读取带超时与一次重试。
**对外接口：** `UiaReader(hwnd).read(frame, epoch) -> (elements, body, browser_ui)`, `.element_at(sx, sy) -> UiaHit | None`, `.document_rect() -> rect`, `.refresh_box(ref) -> rect | None`, `.value(ref) -> str | None`, `.scroll_percent() -> float | None`。
**依赖：** comtypes、coords。

### hands/desktop/screen.py
**职责：** `PrintWindow(hwnd, PW_RENDERFULLCONTENT)` 取整窗图像，按 `Frame` 裁出内容区 → PNG（被别的窗口挡住也只拿到 Chrome 自己的内容，满足 N5）；失败时先 `focus` 再按内容区矩形截屏。稳定判断：缩成小灰度图，相邻两帧平均差 < 阈值并持续 `stable_ms`。
**对外接口：** `capture(hwnd, frame) -> bytes`, `async wait_stable(grab, loading, stable_ms, timeout_s) -> WaitResult`, `diff(a, b) -> float`。
**依赖：** Pillow、win32。

### hands/desktop/grid.py（纯逻辑）
**职责：** 生成网格元素：内容区按配置切成 `cols × rows` 格；**整页可用元素少于阈值**时全部格子入清单；模型调用 `show_grid` 工具后，下一步也加入网格（只加不和已有元素重叠的格子）。网格元素 `role="grid_cell"`、`source="grid"`、名字"格 N"。
**对外接口：** `make_grid(vw, vh, cols, rows, occupied: list[Box]) -> list[Element]`。

### hands/desktop/page.py — DesktopPage（实现 PageLike）
| 方法 | 桌面实现 |
|---|---|
| `navigate(url)` | 确保前台 → Ctrl+L → `text(url)` → Enter → `wait_settled(True)` |
| `reload` / `back` | F5 / Alt+Left，然后 `wait_settled` |
| `wait_settled` | 截图稳定 且 重新加载按钮不在"停止"状态；总超时返回 `timeout`，`pending_urls` 为空并在原因里写"桌面驱动无网络列表" |
| `read_dom` | `UiaReader.read` + 必要时 `make_grid`；页面纪元 +1 |
| `layout` | `vw/vh` = 内容区 / scale；`scroll_y` 由 `scroll_percent` 换算，取不到时用锚点元素位移累计估计；`content_h` 同理估计 |
| `locate_box(el)` | UIA 重新取位置框；元素不在视口内时用滚轮朝它滚（最多 N 次）再取；纪元过期或取不到 → `LookupError` |
| `node_value` | ValuePattern；密码框返回 None |
| `form_action` | 返回 None（拿不到，靠名称判断） |
| `occluded(els)` | 中心点 `element_at`，RuntimeId 不是本元素或其子孙 → 被遮挡 |
| `element_at(x, y)` | 新增可选方法：网格落点核对 |
| `mouse_move` / `mouse_click(x, y, ctrl=False, button="left", count=1)` / `mouse_wheel` | 先 `gate.check()`；换算到屏幕坐标；落点不在内容区 → `OutsideWindow`；注入 |
| `key_press` / `select_all` / `insert_text` | 虚拟键 / Ctrl+A / `KEYEVENTF_UNICODE` 逐字；每个字前 `gate.check()` |
| `screenshot` | `screen.capture` |
| `device_pixel_ratio` | 新增可选方法，返回 `frame.scale` |
| `take_new_tab` / `adopt_new_tab` / `clear_spawned_tabs` / `take_spawned_tab` | 标签条数量增加即"新标签页"；跟随用 Ctrl+PageDown 直到选中的是新标签页 |
| `new_tab(url)` / `close_tab(tab)` / `select_tab(tab)` | 新增可选方法（关联展开保留标签页用）：Ctrl+T 开 / 选中后 Ctrl+W 关 / Ctrl+数字或 Ctrl+PageDown 切 |
| `evaluate` / `evaluate_in_frame` | 抛 `Unsupported`（继承 `CDPError`，现有 `except CDPError` 分支照常兜住） |
| `frame_info` / `refresh_offsets` / `panel_session` | `((0,0), False)` / 无操作 / None |
| `bring_to_front` | `win32.focus` |

**依赖：** win32、uia、screen、grid、coords、pause。

### hands/desktop/browser.py — DesktopBrowser（实现 BrowserLike）
**职责：** `start`：检查平台、comtypes、默认浏览器为 ChromeHTML，否则抛 `DriverUnavailable`（中文提示）；`headless=True` 时打印提示并按有界面；设 DPI 感知；记下现有 Chrome 窗口集合 → `launch_new_window(exe, "about:blank")`（不加其他参数）→ 轮询出新增的那个窗口 → `place` 到配置位置 → 浏览器缩放不是 100% 时 Ctrl+0；启动 `InputWatch`；建 `DesktopPage`。看门狗：窗口不存在 → `AgentWindowClosed`（不重跑）；窗口无响应超过阈值 → 置 `crashed`（按现有崩溃重跑：重开一个新窗口）。`stop`：停钩子，**不关窗口**。`relaunch(headless)`：忽略参数。`capabilities` 见上。
**依赖：** page、inputwatch、win32。

### hands/driver.py（改）
`create_browser` 增加 `desktop` 分支；`DriverUnavailable` 文案三种（缺依赖：`pip install -e .[desktop]`；非 Windows；默认浏览器不是 Chrome）；新增 `Capabilities`。`PageLike` 协议补注释说明新增的可选方法（`element_at`、`device_pixel_ratio`、`new_tab`、`close_tab`、`select_tab`）。

### hands/config.py（改）
`DRIVERS` 加 `desktop`；新配置段：
```yaml
desktop:
  window: [100, 60, 1280, 860]   # 代理窗口屏幕位置与大小（物理像素）
  stable_ms: 600                 # 截图稳定时长
  settle_timeout_s: 20
  uia_timeout_s: 5
  grid: {cols: 12, rows: 8, min_elements: 3}
  wheel_px_per_notch: 100
  idle_resume_s: 0               # 人停手 N 秒自动恢复；0 = 只能手动恢复
  resume_grace_s: 2              # 恢复后宽限期
confirm:                          # 需确认；不写 enabled 时：桌面驱动开、其他驱动关
  enabled: null
  add_words: [] / remove_words: []（account / submit / login 三组）
```

### hands/screenshot.py、hands/pagestate.py（改）
`device_pixel_ratio(page)`：页面有同名方法时直接用，否则照旧执行脚本。`build_page_state` 不改逻辑（`DomReader.read` 已按驱动分派到 `page.read_dom()`）。

### hands/actions.py — ActionExecutor（改）
- 构造参数新增可选 `confirm: ConfirmPolicy | None`、`confirm_fn: ConfirmFn | None`、`gate: PauseGate | None`、`caps: Capabilities`。都不传时行为与现在一致。
- `click` / `open_tab`：兜底拦截（不变）之后，若是网格元素 → `page.element_at` 核对：有元素就用它做 Guard 与 ConfirmPolicy 判断，没有 → `unverifiable_point` 需确认；然后 `confirm.check_click` → `confirm_fn`；拒绝返回 `ActionResult(False, False, error="用户拒绝：…")`。
- `type`：`el.is_password` 且策略开启 → 直接失败"不往密码框输入，已转人工"并置 `data.handoff="login"`。
- `press("Enter")`：现有表单网址检查之后，加 `confirm.check_enter(当前焦点元素)`。
- 新增 `right_click(el)`：同 click 的拦截链，`mouse_click(button="right")`，等待菜单出现。
- `_run_inner`：捕获 `pause.Interrupted` → 返回 `ActionResult(False, False, error="interrupted")` 并带 `data.interrupted=True`；捕获 `OutsideWindow` → 失败并记录。
- `_fingerprint`：页面有 `visual_hash()`（桌面）时把截图哈希并入指纹，差异记作 `visual`。

### hands/decision.py（改）
`TOOL_REGISTRY` 加 `right_click`（action，element）与 `show_grid`（read，无参）；`ELEMENT_ARGS` 加 `right_click`。新增 `tools_for(caps) -> dict`：驱动不支持的工具不出现在可用工具里、出现在决策里判不合法。VlmDecider 的工具说明从 `tools_for` 生成附加段（`prompts/tools_desktop.md`），CDP / Playwright 下提示词与现在逐字相同。

### hands/runner.py — CaseRunner（改）
- 构造参数新增可选 `gate`、`confirm_fn`、`budget_hook`（关联展开用：每步后回调消耗计数，返回 False 则停）。
- 每步开头：`paused_s = await gate.wait()`，`deadline += paused_s`；若上一步被打断，本步从 CHECK 重新观察（index 不回退，记录里标"恢复后重新观察"）。
- CHECK：`captcha.detect` 之后，策略开启时 `confirm.login_hit(state)` 命中 → 走现有 handoff（hit 前缀 `login:`）；`page.dialogs()`（桌面可选方法）有系统对话框 → handoff（hit 前缀 `dialog:`）。
- `_execute` 加 `right_click`、`show_grid`（置位让下一步加网格）。
- 捕获 `AgentWindowClosed` → `tree.fail("代理窗口已关闭")`，不重跑。
- `CaseResult` 增加 `pauses: list[dict]`、`confirms: list[dict]`（默认空，旧报告不受影响）。

### hands/pause.py、hands/confirm.py（新）
见数据结构。命令行的 `confirm_fn`：打印目标与命中词，`input("允许一次？[y/N]")`；命令行的恢复：暂停时打印"已暂停（原因）：回车恢复 / q 取消"，回车后 `gate.arm(resume_grace_s)` 再继续。

### hands/expand/（新）
- `spec.py`：读 YAML（`kind: expand`），`${param}` 替换，校验（起始词在某组里、阈值 0–1、budget ≥ 1、query 含 `{value}`、output 类型合法），错误抛 `TaskTypeInvalid("groups[2].values 为空")` 一类具体信息。
- `tree.py`：见数据结构。
- `subtask.py`：节点 → `CaseSpec`：`start_url = search_url.format(q=query)`，`root_goal` 由模板 `prompts/expand_node.md` 渲染（当前词、所在键、候选来源、可阅读次数、要抽取的字段），`brief.extract_fields` 设为 `["候选"]`（点餐类再加店名、价位、评分、链接），`limits.max_steps = node_steps`。
- `judge.py`：`ExpandJudge(client, prompts).judge(node, tree_summary, candidates, page_state, tt) -> (ops, raw)`，提示词 `prompts/expand_judge.md`；带标注图（复用 `PageDigest` 与 `annotate_numbered`）；格式错误按 3 次重问。替身模型通过现有 `scripted()` 机制注入。
- `rank.py`：价位解析（`$`～`$$$$` → 1–4；"人均 ¥45" / "$12–18" → 金额中值，换成同一档位尺度比较）、排序（排序键列表，未知价位置后）、取前 n。
- `run.py`：`ExpandRunner(browser, config, recorder, tt, gate, confirm_fn, on_change, log)`：
  ```
  loop:
    await gate.wait()
    if tree.finished: break
    if tree.exhausted: gate.pause("budget"); await gate.wait(); continue   # 等用户：结束 / 再给 N 次 / 改树
    node = tree.next_node()
    tab = 保留上限内 ? page.new_tab() : 复用临时标签页
    case = subtask.build(node, tt)
    res = await CaseRunner(..., budget_hook=按 url 变化计阅读消耗).run_case(case)
    tree.mark_searched(node.id)
    if 节点在运行期间被用户删除/改动: 丢弃本次结果, continue
    ops = await judge.judge(...)   →  rejected = tree.apply(node.id, ops, "model")
    节点变 key/deleted → close_tab(tab)；变 terminal → 记录 tab 与 url
    on_change(tree.snapshot())
  ```
  用户结束 → `finish`；输出 `result.json`（最终树、末端叶子清单与依据、保留标签页、修改记录；点餐类加 `rank` 结果）。
- 消耗口径：每个节点的起始搜索计 1；子用例里 `click` / `open_tab` 后网址变化计 1（`back` 不计）；达到上限时 `budget_hook` 让子用例以 `budget` 结束，节点回到 `pending`。

### hands/__main__.py（改）
- `run` 子命令 `--driver desktop` 可选（沿用现有优先级）。
- 新子命令 `expand <任务类型.yaml> [--param k=v ...] [--budget N]`：只能大模型决策（`decision` 不是 vlm 时提示并退出）；运行中命令行提供暂停/恢复/确认；等待决策时提示菜单：`f` 结束并输出 / `+N` 再给 N 次 / 回车查看树。
- 退出码沿用现有约定（依赖缺失 2）。

### panel（改）
- `settings.py`：驱动选项加"桌面"。`service.py`：`_new_browser` 走 `create_browser`；`state()` 加 `capabilities`、`pause`、`confirm_pending`；桌面驱动下预览改为 `ThumbLoop`（每秒 `page.screenshot()` 缩放成 JPEG，走现有帧推送通道，`app.js` 不改帧处理）；预览上的手动输入按 `capabilities.screencast` 隐藏。
- `jobs.py`：`JobManager` 持有 `PauseGate`；新状态 `paused`、`need_confirm`；`_confirm_fn` 把请求放进 `job.confirm_pending` 并等待；新任务种类 `expand`（`_expand` 调 `ExpandRunner`）。演示、真实网站采集在桌面驱动下拒绝（提示用 CDP）。
- `server.py` 新接口：`POST /api/jobs/pause`、`/api/jobs/resume`、`/api/jobs/confirm {allow}`；`GET /api/expand/types`、`POST /api/expand/start {type, params}`、`GET /api/expand/tree`、`POST /api/expand/edit {op, node?, key?, values?, n?}`（op：delete / set_terminal / set_pending / add_group / grant / finish）。编辑接口只在暂停或等待决策时接受；所有接口沿用现有令牌与 Host 校验。
- `static/tree.html|js|css`（新页面，顶部加链接，不改原有界面）：树（缩进列表 + 状态色块 + 来源标记），点节点看理由、依据、截图链接；顶部显示已用 / 上限次数、暂停原因；右侧曲线（叶子数、节点数、已用次数，用内联 SVG 折线）；暂停 / 恢复 / 结束 / 再给 N 次 / 删除 / 改成叶子 / 改回待查 / 加一组 按钮。`index.html` 状态区加"待确认"卡片（目标、命中词、允许一次 / 拒绝）与暂停原因。

### hands/recorder.py（改）
- 每步目录新增 `desktop.json`：元素来源计数、`Frame`、注入事件列表（类型、屏幕坐标、时间；输入文本记录长度与内容，密码框不会出现）、稳定等待结果；截图照旧存 `screenshot.png`。
- 用例级新增 `pauses.json`、`confirms.json`；报告每步显示"元素来源 / 换算比例 / 等待"，用例头部列出暂停、确认、转人工。
- 关联展开：运行目录下 `expand/` 存 `type.yaml`（含参数）、`events.json`、`snapshots/0001.json…`（每次变化一份）、`judge/<节点>/request.json|response.json`、`result.json`；报告加"展开过程"页（演变时间线 + 最终树 + 结果）。

## 模块交互

**桌面驱动下一步的调用链：**
```
CaseRunner 每步
 ├ gate.wait()                                   ← 暂停时阻塞
 ├ build_page_state(page)
 │   ├ DomReader.read → DesktopPage.read_dom → UiaReader.read (+ grid)
 │   ├ page.url（地址栏） page.title  page.device_pixel_ratio  page.layout
 │   └ screen.capture → screenshot.png
 ├ captcha.detect / confirm.login_hit / page.dialogs → handoff（面板 need_human 或命令行）
 ├ page.occluded（vlm 时）
 ├ decide_with_retry(VlmDecider) ── 工具集 = tools_for(capabilities)
 ├ ActionExecutor.click/…
 │   ├ Guard.check（不变）
 │   ├ [grid] page.element_at → Guard / ConfirmPolicy
 │   ├ ConfirmPolicy.check_click → confirm_fn（面板 need_confirm / 命令行 y/N）
 │   ├ Humanizer.move_to(page.mouse_move …)      ← 每个轨迹点 gate.check()，人一碰抛 Interrupted
 │   ├ page.mouse_click → win32.SendInput(带标记)
 │   └ page.wait_settled → screen.wait_stable + 重新加载按钮状态
 └ recorder.save_step（含 desktop.json）

InputWatch 线程 ── 无标记的输入 → loop.call_soon_threadsafe(gate.on_human_input)
gate.on_human_input ── 运行中且过了宽限期 → gate.pause("human_input") → STATUS 推送 → 面板显示
```

**关联展开：**
```
面板 /api/expand/start 或 CLI expand
 → load_task_type → ExpandTree
 → ExpandRunner 循环：
     next_node → subtask.build → CaseRunner.run_case（上面那条链；budget_hook 计消耗）
     → 子用例 extract 出的有依据候选 → ExpandJudge（4b）→ tree.apply → 标签页保留/关闭
     → on_change → JobManager 存快照 → 面板 tree.html 刷新
 → 用户在面板暂停/编辑 → /api/expand/edit → tree.delete/... (只在暂停时) → 恢复
 → finished 或 用户结束 → result.json（+ rank）
```

依赖方向：`expand → runner → actions → driver 接口`；`desktop → driver 接口`；`pause`、`confirm` 不依赖任何驱动；面板只依赖 `hands` 的公开对象。无环。

## 文件组织
```
src/hands/
├── driver.py            改：desktop 分支、Capabilities、可选方法说明
├── config.py            改：DRIVERS、desktop / confirm 配置段
├── dom.py               改：Element 加 source、is_password
├── screenshot.py        改：device_pixel_ratio 优先用页面方法
├── actions.py           改：确认、网格核对、密码、右键、打断
├── decision.py          改：right_click、show_grid、tools_for
├── runner.py            改：gate、login/dialog 转人工、窗口关闭、新工具、pauses/confirms
├── recorder.py          改：desktop.json、pauses/confirms、expand 目录与报告
├── __main__.py          改：--driver desktop、expand 子命令
├── pause.py             新：PauseGate、Interrupted
├── confirm.py           新：ConfirmPolicy、ConfirmRequest、命令行 confirm_fn
├── desktop/
│   ├── __init__.py
│   ├── win32.py         新：窗口、SendInput、对话框、DPI、默认浏览器
│   ├── inputwatch.py    新：低级钩子与输入来源区分
│   ├── uia.py           新：无障碍树读取、角色映射、点查询
│   ├── coords.py        新：Frame 坐标换算
│   ├── screen.py        新：窗口截图、稳定判断
│   ├── grid.py          新：网格元素
│   ├── page.py          新：DesktopPage
│   └── browser.py       新：DesktopBrowser
├── expand/
│   ├── __init__.py
│   ├── spec.py          新：任务类型定义读取与校验
│   ├── tree.py          新：ExpandTree
│   ├── subtask.py       新：节点 → 子用例
│   ├── judge.py         新：4b 判定
│   ├── rank.py          新：价位解析与排序
│   └── run.py           新：ExpandRunner
└── vlm/
    ├── decider.py       改：工具说明按 tools_for 追加
    └── prompts/
        ├── tools_desktop.md   新：right_click、show_grid、网格说明
        ├── expand_node.md     新：节点子用例目标模板
        └── expand_judge.md    新：判定提示词
src/panel/
├── service.py           改：桌面驱动、ThumbLoop、状态字段
├── jobs.py              改：PauseGate、need_confirm、expand 任务
├── server.py            改：pause/resume/confirm、expand 接口
├── settings.py          改：驱动选项
├── preview.py           改：ThumbLoop（桌面驱动的定时截图预览）
└── static/
    ├── index.html, app.js   改：待确认卡片、暂停原因、任务树链接、桌面下隐藏预览手动输入
    └── tree.html, tree.js, tree.css   新：任务树页
tasks/
├── dinner.yaml          新：点餐（与用户共同编写）
└── transformers.yaml    新：变形金刚角色（与用户共同编写）
fixtures/
├── f20_desktop/         新：已知位置元素、密码框、右键菜单、双击、滚动长页、canvas 按钮、慢加载 / 永不稳定、打印 / 下载触发
├── f21_confirm/         新：登录页、加入购物车 / 收藏、非搜索表单、提交订单
├── f22_search/          新：仿搜索引擎（?q= 按脚本返回结果列表与右侧相关词，可设"第一页 80% 提到某词"）
└── f23_dinner/          新：仿餐厅列表（三个品类、价位多种写法、评分、收藏榜 / 消费榜）
tests/unit/              新：test_coords、test_uia_map、test_inputwatch_classify、test_pause_gate、
                              test_confirm_policy、test_grid、test_expand_spec、test_expand_tree、
                              test_expand_judge、test_rank、test_tools_for
tests/acceptance/d*.py   新：桌面与关联展开验收脚本（编号对应 spec AC）
run_desktop_tests.py     新：一键验收（--driver 指定 cdp / desktop；自动项）
pyproject.toml           改：可选依赖 desktop = ["comtypes"]
config.yaml              改：desktop / confirm 段（注释示例）
README.md                改：桌面驱动与关联展开用法
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 读页面 | Windows UIA（comtypes 直调 + CacheRequest 批量取属性） | 默认 Profile 读不到 DOM；Chrome 原生向 UIA 提供无障碍树，含角色、名称、位置框、链接、输入值；comtypes 是唯一新增依赖，比 pywinauto 轻、比逐属性调用快 |
| 输入 | ctypes `SendInput`，`dwExtraInfo` 打固定标记 | 不新增依赖；标记让低级钩子能区分"本程序注入"与"人工输入"（包括其他软件的注入也按人工处理，宁可多停） |
| 文本输入 | `KEYEVENTF_UNICODE` 逐字 | 不依赖当前输入法，中英文结果一致 |
| 人工输入检测 | `WH_MOUSE_LL` / `WH_KEYBOARD_LL`，独立线程消息循环 | 系统级、可读注入标记；钩子回调只做时间戳和投递，不阻塞系统输入 |
| 截图 | `PrintWindow(PW_RENDERFULLCONTENT)` 裁内容区；失败时前台后区域截屏 | 只拿 Chrome 窗口自己的像素，被遮挡也不会截到别的窗口（N5） |
| 内容区与坐标 | Document 控件的位置框 = 内容区；进程 Per-Monitor-V2 DPI 感知；scale = 显示缩放（启动时把浏览器缩放复位到 100%） | UIA 与 SendInput 都用物理像素，统一在 `Frame` 里换算成 CSS 像素（N3） |
| 等待稳定 | 截图稳定 + 重新加载按钮状态 | 没有网络列表；两者结合避免"白屏不变就当加载完"或"动画一直变" |
| 网格兜底触发 | 可用元素少于阈值自动加；或模型调 `show_grid` 后下一步加 | 不在每页都塞网格编号，免得 4b 提示词变乱；canvas 页自动触发 |
| 新工具暴露 | `tools_for(capabilities)` 只在桌面驱动下加 `right_click`、`show_grid` | CDP / Playwright 下提示词与工具表逐字不变，ver1 验收不受影响（N1） |
| 需确认默认值 | 桌面驱动默认开，其他驱动默认关，可配置 | spec F9；现有验收不变 |
| 暂停实现 | 统一 `PauseGate`；动作中途在每个轨迹点 / 每个字前检查 | 人一碰就能在一个轨迹点间隔（16 ms）内停下，满足"0.5 秒内" |
| 恢复后误触发 | 宽限期 `resume_grace_s`（默认 2 秒） | 用户在面板点完"恢复"后把手移开，不会立刻又被判为人工输入 |
| 关联展开与现有任务树 | 外层 `ExpandTree` + 每个节点一个子用例走 `CaseRunner` | 复用搜索、阅读、抽取回查、验证码、兜底、确认、暂停、记录；现有 `TaskTree` 不改 |
| 4b 加词的依据 | 只能引用本节点子用例 `extract` 出的有依据候选 | 直接复用 ver1 的抽取回查；判定仍以 4b 为准，程序只拦"凭空编词"和不允许的操作 |
| 环的处理 | 祖先链上已有的键再次出现 → 搜完直接末端叶子（理由"循环"） | 防止 `decepticon <megatron>`、`megatron <… decepticon>` 无限展开；预算之外的第二道保险 |
| 标签页保留 | 驱动可选方法 `new_tab` / `close_tab` / `select_tab`；CDP、Playwright 也实现 | 关联展开驱动无关（N9）；超过上限用一个临时标签页轮换 |
| 树的修改时机 | 只在暂停或等待决策时接受编辑 | 避免和正在执行的节点竞争；被改动的节点本轮结果丢弃 |
| 任务类型存放 | 新目录 `tasks/`，`kind: expand` | 与 `cases/` 普通用例分开；以后新类型往里加 |
| 面板任务树页 | 独立 `tree.html`，内联 SVG 折线 | 不改原有界面（与 ver1 人眼比对页同做法）；不引入前端库 |
| 预览 | 桌面驱动下每秒一帧窗口截图，复用现有帧通道 | 没有 CDP 推流；缩略预览够用，前端帧处理不改 |

## 先行验证（开发第一组任务，结论写进 `docs/desktop/probe.md`）
以下几点依赖 Chrome 156 在本机的实际表现，先写小脚本实测，结果不符时按备选做：
1. Chrome 在 UIA 请求下自动开启无障碍树、读一页的耗时 — 备选：读不到时提示用户在 `chrome://accessibility` 打开"原生无障碍 API"。
2. Document 的 `ScrollPattern.VerticalScrollPercent` 是否可用 — 备选：锚点位移估计滚动位置。
3. 链接的 ValuePattern 是否给出完整地址 — 备选：`LegacyIAccessible.Value`。
4. `PrintWindow(PW_RENDERFULLCONTENT)` 对 GPU 合成的 Chrome 是否得到非黑图 — 备选：前台后区域截屏（截前确认没有其他窗口压在内容区上）。
5. 低级钩子的 `dwExtraInfo` 能否读回 `SendInput` 填的标记 — 备选：注入前后记录时间窗 + `LLMHF_INJECTED` 组合判断。
6. 地址栏、重新加载按钮、标签的 ClassName 在中文界面下是否稳定 — 备选：按控件类型 + 位置关系查找。

## spec 覆盖

| spec | 落在 |
|---|---|
| F1 驱动选择 | driver.py、config.py、DesktopBrowser.start、panel settings |
| F2 代理窗口 | win32、DesktopBrowser（新窗口、定位、看门狗、窗口关闭） |
| F3 截图 | screen、coords |
| F4 无障碍树读元素 | uia、DesktopPage.read_dom / url / occluded |
| F5 网格兜底 | grid、show_grid、ActionExecutor 网格核对 |
| F6 动作 | DesktopPage 输入方法、ActionExecutor.right_click、decision.tools_for |
| F7 观察与验证 | DesktopPage.wait_settled、screen.wait_stable、`_fingerprint` 视觉哈希 |
| F8 人工输入即暂停 | inputwatch、PauseGate、runner、CLI / 面板 pause/resume |
| F9 需确认与转人工 | ConfirmPolicy、ActionExecutor、runner login_hit、面板 need_confirm |
| F10 窗口外与对话框 | DesktopPage 落点检查（OutsideWindow）、win32.owned_dialogs、runner dialog 转人工 |
| F11 任务类型定义 | expand/spec.py、tasks/*.yaml |
| F12 展开规则 | expand/tree.py、run.py、标签页可选方法 |
| F13 4b 判定 | expand/judge.py、prompts/expand_judge.md |
| F14 次数上限 | ExpandTree.used/budget、budget_hook、等待决策 |
| F15 人工改树 | ExpandTree 用户操作、/api/expand/*、tree.html |
| F16 发起与结果 | CLI expand、/api/expand/start、rank.py、result.json |
| F17 记录 | recorder（desktop.json、pauses、confirms、expand 目录） |
| F18 面板 | service、preview ThumbLoop、jobs、index.html 待确认卡片 |
| N1 向后兼容 | Capabilities 默认值、tools_for、confirm 默认关、可选依赖 |
| N2 复用 | 驱动接口、CaseRunner 嵌套、vlm 原样 |
| N3 坐标统一 | coords.Frame |
| N4 只在本机 | 无新外部连接 |
| N5 隐私 | PrintWindow、密码不读不存 |
| N6 边界 | Guard 不变、min_interval_s、budget、不改指纹 |
| N7 可测 | 纯逻辑模块 + 替身模型 + 仿搜索 / 仿餐厅夹具 |
| N8 速度 | desktop.json 记录读取耗时，报告汇总中位数 |
| N9 驱动无关 | expand 只用驱动接口；CDP / Playwright 实现标签页可选方法 |
