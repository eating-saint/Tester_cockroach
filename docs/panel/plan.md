# 控制面板（panel）Plan

日期：2026-11-12　状态：待用户审批（用户要求一次写完整份再检查，未逐段呈现）　依据：`docs/panel/spec.md`

## 1. 架构概览

```
┌──────────────── 面板窗口（WebView2，私密模式，只开本机面板地址）────────────────┐
│  static/index.html + app.js（纯 HTML/JS，无构建工具、无外部库）                  │
│  预览 canvas + 叠加层 canvas │ 浏览器控制 │ 设置 │ 任务树/时间线 │ 网络 │ 历史 │ 采集页 │
└───────────────▲ REST（命令） ▲ WebSocket（状态推送、画面帧、手动输入）─────────────┘
                │ 令牌 + Origin + Host 校验，只监听 127.0.0.1
┌───────────────┴──────────────── panel 包（新，Python）──────────────────────────┐
│ server.py  aiohttp 路由、鉴权、静态文件、/runs 只读、WS 集线器                     │
│ service.py PanelService：唯一持有 BrowserManager；状态汇总；崩溃看护；夹具服务       │
│ jobs.py    JobManager（同一时刻一个任务）+ Demo / Case / Target / Manual / RealSample │
│ preview.py 推流（Page.startScreencast）、跟随标签页、帧率/延迟统计                   │
│ inputs.py  手动输入 → Input.dispatch*                                            │
│ settings.py 设置表、范围校验、"下一个动作生效"、写回 config.yaml                       │
│ samples.py 轨迹样本库读写、校验、导入导出    history.py 运行历史   profile.py 用户目录 │
│ app.py     打包入口：单实例锁、pywebview 窗口、数据目录                                │
└───────────────┬──────────────────────────────────────────────────────────────────┘
                │ 只调用 hands 的公开类和新增的可选钩子（hands 不 import panel）
┌───────────────┴──────────── hands 包（ver0，少量增量修改）──────────────────────────┐
│ browser / page / monitor / dom / actions / humanize / runner / captcha / recorder │
│ 新增：status.py（进程内实时状态）、trajlib.py（真实轨迹库：挑样本、变换）              │
└───────────────┬──────────────────────────────────────────────────────────────────┘
                ▼ CDP（一条 websocket）
        自动化 Chrome（profiles/ver0，无界面 / 有界面）
```

**分层原则**
- `hands` 继续可以单独使用（命令行、`run_tests.py`），不依赖 `panel`，也不依赖 aiohttp（N3）。
- 面板需要的实时信息，靠 `hands` 新增的**可选**钩子拿到：钩子都有默认值 None，不传时行为和现在完全一样。
- 面板对浏览器的一切操作都走 `hands` 的类和 CDP，不调用 Windows 窗口接口（N13）。唯一和 Windows 绑定的是 `app.py` 里的 pywebview 窗口和打包。

## 2. 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 后台 | aiohttp（HTTP + WebSocket 同一端口），跑在 hands 的同一个 asyncio 事件循环里 | hands 全是 asyncio；一个循环里不用跨线程传 CDP 对象 |
| 前端 | 纯 HTML/JS/CSS，放在 `src/panel/static/`，不用构建工具、不加载 CDN | N11 断网可用；打包简单 |
| 命令与推送 | 命令走 REST（好测、好重放）；状态、画面帧、手动输入走一条 WebSocket | 帧和输入要低延迟；命令要有明确的成功/失败返回 |
| 鉴权（N1） | ① 只绑 127.0.0.1；② 每次启动生成随机令牌，面板页面加载时由服务端写入页面；所有 API 和 WS 都要带令牌；③ 校验 `Origin` 必须等于面板自身地址、`Host` 必须是 `127.0.0.1:端口` | 挡住其他网页的跨站请求和 DNS 重绑定 |
| 推流 | CDP `Page.startScreencast`（JPEG，质量 70，最大 1280×800），每帧 `screencastFrameAck`；服务端只保留最新一帧，客户端处理不过来就丢旧帧 | 不改视口、不产生网络请求（N4）；丢帧保证延迟不累积（N5） |
| 帧传输 | WS 二进制消息：4 字节头长度 + JSON 头（帧号、时间戳、设备尺寸、滚动偏移）+ JPEG | 不做 base64 二次编码，省 1/3 流量 |
| 坐标 | 全部用 CSS 像素。预览坐标 → CSS：除以（帧宽 / 设备宽）；叠加层反过来乘 | 与 ver0 一致（N10） |
| 有界面时 | 默认不推流；"调到前台"用 CDP `Page.bringToFront` + `Browser.setWindowBounds` | 零额外画面开销；仍走 CDP，不用 Windows 窗口接口 |
| 同一时刻一个任务（F3） | JobManager 持有一把 asyncio.Lock，后台拒绝第二个启动请求（409） | 共用一个 profile，必须互斥 |
| 实时状态 | `hands/status.py`：进程内单例 `STATUS`，hands 在等待、阶段切换、动作结束、换标签页时更新它并通知订阅者；面板订阅后每 0.5 秒（有事件时立即）推送 | 不让 hands 依赖面板；没有订阅者时开销只是几次属性赋值 |
| 设置"下一个动作生效"（F8） | ActionExecutor 新增可选钩子 `before_action`，每个动作开始时调用；面板在钩子里把待生效设置应用到 config 和 Humanizer | 动作中途不会出现新旧参数混用 |
| 面板跑用例时开着 Humanizer | 面板总是创建 Humanizer（三项关闭时发出的事件和 ver0 相同），这样运行中途打开轨迹也能生效 | ver0 里三项全关时 humanizer=None，中途无法打开 |
| 人工介入（F13） | CaseRunner 新增可选参数 `handoff_fn`；面板传入"预览手动处理"版本，命令行保持原 `captcha.handoff` | 两条路径并存，命令行行为不变 |
| 验证码不重复转人工 | runner 记住"已处理的验证码签名 =（去掉 # 的网址, 命中标记）"，同签名不再触发；两条路径都受益 | 真实验证框通过后 iframe 仍在页面上 |
| 真实轨迹库（F16） | `hands/trajlib.py` 纯函数：按方向+设备用 rng 挑样本 → 相似变换（平移、旋转、等比缩放）把样本起点、终点对到当前起点、落点 → 时间按距离比例缩放并限制在 180–1100 ms → 最后一点精确等于落点 | 可单元测试；同 rng 同结果（N6） |
| 本地采集页 | 面板窗口里的 `collect.html`，用种子生成"仿真实网站"布局；记录 `pointermove`（含 `getCoalescedEvents` 合并点）和 `performance.now()` | 我们自己的页面，不涉及注入 |
| 真实网站采集 | 有界面 Chrome；`Page.createIsolatedWorld` + `Page.addScriptToEvaluateOnNewDocument(worldName)` 注入只读监听；数据经 `Runtime.addBinding`（限定该隔离环境）回传；目标用 CDP `Overlay.highlightNode` 高亮（不改页面 DOM）；退出时移除脚本和绑定 | 只在采集期间、只读、页面脚本看不到（N4 例外条款） |
| 写回 config.yaml | 按"键路径"逐行替换值、保留注释和顺序，写前备份 `config.yaml.bak` | PyYAML 整体 dump 会丢掉注释 |
| 窗口 | pywebview（Windows 上是 WebView2），`private_mode=True` | N2；用户选的方案 C |
| 外部跳转拦截（N2） | 主：pywebview 窗口的原生 WebView2 `NavigationStarting` 事件里取消非面板地址；兜底：前端拦截链接点击 + 服务端 CSP（`default-src 'self'`） | 主方案依赖 pywebview 暴露原生控件，开发时先验证（风险 R2） |
| 打包 | PyInstaller onedir：`HandsPanel/HandsPanel.exe` + `_internal/`；数据放 `HandsPanel/data/`（profiles、runs、trajlib、config.yaml） | 升级只替换程序文件，data 保留（F17） |
| 数据目录 | `hands.config` 新增 `DATA_ROOT`：环境变量 `HANDS_DATA_DIR` 优先，否则保持 `PROJECT_ROOT`；相对路径都相对它 | 开发时行为不变；打包后指向 data/ |
| 单实例（F1） | `DATA_ROOT/panel.lock` 记录 pid、端口、令牌；第二个实例发现锁有效就调用 `/api/focus` 让原窗口到前台后退出 | 不依赖 Windows 互斥体，平板以后也能用 |
| 面板测试 | ① pytest 单元测试（纯逻辑）；② `run_panel_tests.py` 验收表：启动面板后台（不开窗口），用 REST/WS 驱动；需要看 UI 状态的项，用第二个 Chrome（独立 profile `profiles/panel_ui_test`）打开面板页面，读 DOM 判断按钮是否可用 | 和 ver0 的 run_tests 同样的表格格式 |

## 3. 核心数据结构

### 3.1 hands/status.py
```python
@dataclass
class WaitInfo:
    what: str            # "load" | "network_quiet" | "after_action" | "think" | "human"
    since: float         # time.monotonic()
    timeout_s: float | None

class LiveStatus:
    phase: str | None            # "CHECK" | "ANALYZE" | "EXECUTE" | None
    wait: WaitInfo | None
    page_epoch: int              # 每次 Page.attach / adopt_new_tab / back 换页 +1，推流据此重新挂接
    last_action: dict | None     # {"tool", "ok", "effective", "changes", "trajectory", "elapsed_ms"}
    def subscribe(self, cb) -> Callable[[], None]   # 返回取消订阅函数
    def set_wait(self, what, timeout_s=None); def clear_wait()
    def set_phase(self, phase); def page_changed(); def action_done(info)

STATUS = LiveStatus()   # 进程内单例；只有一个浏览器实例，所以单例足够
```

### 3.2 hands/trajlib.py
```python
@dataclass
class Sample:
    id: str                         # 内容哈希前 12 位（导入合并时据此去重）
    device: str                     # "mouse" | "touchpad"
    source: str                     # "local" | "real"
    domain: str | None              # 真实网站采集时的域名
    direction: int                  # 0..7，按起点→终点角度划分（与 humanize.direction_of 相同）
    start: tuple[float, float]; end: tuple[float, float]
    target: Box
    points: list[tuple[float, float, int]]   # (x, y, ms)，CSS 像素，ms 从点起点算起
    dpr: float; created: str

@dataclass
class Library:
    version: int = 1
    samples: list[Sample]
    @classmethod
    def load(cls, path) -> "Library"; def save(self, path)
    def pick(self, direction: int, device: str, rng: random.Random) -> Sample | None   # device="mixed" 时两种都算

def transform(sample: Sample, start, landing, rng, min_ms=180, max_ms=1100) -> list[tuple[float, float, int]]
```

### 3.3 humanize.py 增量
- `MouseConfig` 新增：`source: str = "template"`（或 `"library"`）、`device: str = "mouse"`、`library_path: str = "trajlib/samples.json"`。
- `Move` 新增：`source: str = "template"`、`sample_id: str | None = None`、`fallback: bool = False`；`to_dict()` 输出这三项。
- `Humanizer.__init__(cfg, seed, library: Library | None = None)`；`move_to` 在 `source == "library"` 时调用 `plan_library_move()`，库里没样本则调 `plan_move` 并置 `fallback=True`。
- `Humanizer.think()` 停顿期间 `STATUS.set_wait("think")`。

### 3.4 panel 侧
```python
@dataclass
class JobInfo:
    id: str; kind: str            # "demo" | "case" | "target" | "manual" | "sample_real"
    title: str; state: str        # "running" | "need_human" | "stopping" | "done" | "failed" | "stopped"
    started: float; progress: dict   # 例如 {"step": 3, "max_steps": 20}
    result: dict | None              # 结束时：passed、reason、report 路径等

@dataclass
class PanelState:                  # GET /api/state 和 WS "state" 消息
    browser: dict                  # {"status": "idle|running|crash_restarting|need_human|stopped", "headless", "crashes", "pid"}
    page: dict                     # {"url", "title"}
    job: JobInfo | None
    wait: dict | None              # {"what", "elapsed_ms", "timeout_ms"}
    phase: str | None
    monitor: dict                  # MonitorState.to_dict()：pending、failed、console_errors、lifecycle
    tree: dict | None              # TaskTree 节点列表 + active_id
    steps: list[dict]              # 时间线：index、tool、ok、effective、changes、durations
    preview: dict                  # {"on", "fps", "latency_ms_p50", "overlays": {...}}
    settings_pending: bool         # 有已修改但尚未生效的设置
    seed: int | None

@dataclass
class SettingSpec:                 # settings.py 里一张表定义全部可调项
    path: str                      # "humanize.mouse.overshoot_prob"
    kind: str                      # "bool" | "int" | "float" | "enum"
    min: float | None; max: float | None; choices: list | None
    label: str                     # 中文标签
```

**WS 消息**
- 服务端 → 客户端：`{"t":"state", ...PanelState}`、`{"t":"event","name":"action_done"|"need_human"|"job_end"|..., ...}`、二进制帧。
- 客户端 → 服务端：`{"t":"overlay", ...}`、手动输入 `{"t":"mouse","kind":"move|down|up|wheel","x","y","button","dy"}`、`{"t":"key","kind":"down|up","key","code","mods"}`、`{"t":"text","text":"中文"}`。

## 4. 模块设计

### hands（增量修改，全部向后兼容）
| 文件 | 改动 | 对应 |
|---|---|---|
| status.py（新） | LiveStatus 单例 | F6、F7、F4 |
| trajlib.py（新） | 样本结构、库读写、pick、transform | F16 |
| page.py | `wait_settled` 进入/退出时设置/清除 `STATUS.wait`（load / network_quiet）；`attach` 结束时 `STATUS.page_changed()` | F6、F4 |
| actions.py | `ActionExecutor(..., before_action=None)`；`_run` 开头调 `before_action()`，结束时 `STATUS.action_done(...)` | F8、F4 |
| humanize.py | 见 3.3 | F16、F6 |
| runner.py | `CaseRunner(..., should_stop=None, before_action=None, handoff_fn=captcha.handoff, humanizer_factory=None)`；循环开头检查 `should_stop()` → 根任务失败、原因 `user_stopped`；CHECK/ANALYZE/EXECUTE 切换时 `STATUS.set_phase`；已处理验证码签名集合；人工介入起止时间写进 CaseResult（`human_handoffs: [{start, end, hit}]`） | F10、F13、N8 |
| captcha.py | 默认标记补 `challenges.cloudflare.com`；新增 `signature(state, hit)` | F13 |
| config.py | `DATA_ROOT`；`DEFAULT_CAPTCHA_MARKERS` 同步补充 | F17 |
| recorder.py | report.json 写入 `human_handoffs`；报告 HTML 显示人工介入时间 | N8 |

### panel/server.py
- **职责**：HTTP/WS 入口；鉴权中间件；静态文件；`/runs/<目录>/...` 只读映射；把请求转给 PanelService。
- **路由**：
  - `GET /` → index.html（注入令牌）；`GET /collect` → 采集页
  - `GET /api/state`；`POST /api/focus`
  - 浏览器：`POST /api/browser/start|stop|restart`、`POST /api/browser/mode {headless}`、`POST /api/browser/front`
  - 导航：`POST /api/nav {url}`、`/api/nav/back`、`/api/nav/reload`
  - 设置：`GET/PUT /api/settings`、`POST /api/settings/save_default`、`POST /api/settings/restore`
  - 任务：`POST /api/jobs {kind, params}`、`POST /api/jobs/stop`、`POST /api/jobs/continue`
  - 预览：`POST /api/preview {on, overlays}`、`GET /api/elements`
  - 列表：`GET /api/cases`、`GET /api/runs`
  - 用户目录：`GET /api/profile`、`POST /api/profile/clear {confirm: "CLEAR"}`
  - 样本：`GET /api/samples`、`POST /api/samples`、`DELETE /api/samples/{id}`、`GET /api/samples/export`、`POST /api/samples/import?mode=merge|replace`
  - `GET /ws?token=`
- **依赖**：aiohttp、service。

### panel/service.py — PanelService
- **职责**：持有唯一的 BrowserManager、Config、LiveSettings、Screencaster、JobManager、夹具服务；汇总 PanelState；订阅 STATUS；崩溃看护。
- **接口**：`start()`、`shutdown()`（停任务 → 停推流 → 关浏览器 → 停夹具服务）、`state() -> PanelState`、`browser_start/stop/restart/set_mode/front()`、`navigate/back/reload()`。
- **崩溃看护**：空闲时（没有任务）检测到 `browser.crashed` → 状态灯"崩溃重启中" → `restart()` → 计数 +1。有用例任务时交给 CaseRunner 自己的崩溃重跑，面板只显示状态。
- **夹具服务**：首次需要时启动（`${BASE}` 用例、演示页、Cookie 夹具），面板关闭时停止。

### panel/jobs.py — JobManager 与任务
- `JobManager.start(kind, params) -> JobInfo`：拿不到锁返回 409"正在运行：xxx"；`stop()`；`continue_human()`。
- 所有任务共用：开始前 `settings.apply_now()`；创建 Humanizer 时带 `library`；RunRecorder 标签以 `panel-` 开头（N8）。
- **DemoJob**（F9）：打开 F12 夹具 → 依次点击 中心、八个方向、小按钮 → 每次读页面记录的点击坐标判断命中 → 推送 `action_done`（含轨迹、计划/实际耗时）。参数 `seed`（可重放）。前端保留上一轮轨迹换色显示。
- **CaseJob**（F10）：`load_case` → `PanelCaseRunner(CaseRunner)`：传 `should_stop`、`before_action`、`handoff_fn=self.panel_handoff`、`on_step`（推送任务树和时间线）。停止 = 置标志，当前动作结束后生效。
- **panel_handoff**（F13）：截图 → 状态"需要人工" → 打开推流和手动模式 → `await continue_event` → 返回当前 page（不重启浏览器）；记录起止时间。
- **TargetJob**（F11）：参数 `{url?, x, y}`：需要时先导航 → DomReader 读元素 → 取包含该点、可见、面积最小的元素 → `executor.click(el)` → 推送结果。
- **ManualJob**（F12）：占住任务锁；WS 的手动输入只在 ManualJob 或"需要人工"状态下被接受，其余时间丢弃。
- **RealSampleJob**（F15 真实网站）：要求有界面；注入监听（见 samples.py）；循环：挑目标 → `Overlay.highlightNode` → 等这一条的点击数据 → 校验 → 存库 → 下一条；用户点"结束"或停止时撤除注入。

### panel/preview.py — Screencaster
- `start(page)` / `stop()`；订阅 `Page.screencastFrame`，回 ack，保存最新帧 + 元数据，通知 WS 集线器。
- 订阅 `STATUS.page_epoch` 变化：在新 page 的 session 上重新 `startScreencast`。
- 统计：最近 2 秒帧数 → fps；客户端回报的"显示时刻 − 帧时间戳" → 延迟中位数（AC5）。
- 有界面时默认 `stop()`；用户手动打开才推。

### panel/inputs.py
- 把 WS 手动输入转换成 `Input.dispatchMouseEvent` / `dispatchKeyEvent` / `insertText`。中文由前端隐藏输入框的 `compositionend` 拿到整段文本后走 `insertText`。按键映射表复用 `actions.KEYS` 并补足常用键。

### panel/settings.py
- `SPECS: list[SettingSpec]`（三项干扰的全部参数、seed、轨迹来源与设备、quiet_ms、nav_timeout_s；决策方式只有 "rule"）。
- `LiveSettings`：`current`、`pending`；`update(patch) -> errors`（逐项范围校验，有错整体拒绝）；`apply_now(config, humanizer)`（`before_action` 调用它）。
- `save_default(path)`：按键路径逐行改 config.yaml（保留注释），先写 `.bak`；`restore()`：回到代码默认值。

### panel/samples.py — SampleStore
- 文件：`DATA_ROOT/trajlib/samples.json`（`{"version":1,"samples":[...]}`，UTF-8，缩进 1，人可读）。
- `add(raw) -> Sample | Reject`：服务端复核作废规则（没点中、离开区域、>5 秒、点数 < 8）；`delete(id)`；`grid() -> {device: {direction: {"local": n, "real": n}}}`；`export() -> bytes`；`import_(data, mode)`（merge 按 id 去重 / replace）。
- **注入脚本**（真实网站）：常量 `LISTENER_JS`：`capture` 阶段被动监听 `pointermove/pointerdown/pointerup/click`，不调用 `preventDefault`，按 50 ms 批量通过绑定函数回传；只在"当前题目"开启后记录。

### panel/history.py、panel/profile.py
- history：扫描 `runs/*/report.json`，返回时间、标签、通过数/总数；报告通过 `/runs/...` 打开。
- profile：`info()` → 路径、占用字节；`clear(confirm)`：浏览器未停止时拒绝；`shutil.rmtree` 后重建空目录。

### panel/app.py（Windows 入口）与 panel/__main__.py（开发入口）
- `__main__`：`python -m panel [--port 0] [--no-window]`，打印 `http://127.0.0.1:端口/?token=...`，可用普通 Chrome 打开（AC28）。
- `app.py`：设置 `HANDS_DATA_DIR` → 单实例检查 → 后台线程启动事件循环和服务 → 主线程 `webview.create_window(...)`、`webview.start(private_mode=True)` → 窗口关闭时 `service.shutdown()`。
- Chrome 找不到：`find_chrome` 抛错 → 面板显示"设置 Chrome 路径"表单 → 写入 config.yaml 的 `chrome_path`。

### 前端（src/panel/static/）
| 文件 | 内容 |
|---|---|
| index.html / style.css | 布局：左侧预览（canvas 帧 + canvas 叠加）、顶部地址栏和浏览器控制、右侧标签页（设置 / 任务 / 网络 / 历史 / 用户目录 / 采集） |
| app.js | API 封装（自动带令牌）、WS 连接与重连（重连后先拉 `/api/state` 恢复界面，N7）、帧绘制（`createImageBitmap`）、叠加层、悬停高亮、手动输入转发、按钮可用状态 |
| collect.html / collect.js | 本地采集页：设备选择、按种子生成仿真实网站布局、出题、记录、前端预校验、进度格、回看（画图 + 动画回放）、删除、补采、导入导出 |
| about.txt 文案 | 设置页用途说明（N12） |

## 5. 模块交互

**用例运行（F10、F7、F13）**
```
前端 POST /api/jobs{kind:case} → JobManager 加锁 → CaseJob
  → PanelCaseRunner.run_case
      每步：STATUS.set_phase(CHECK) → build_page_state → captcha.detect
            ├ 命中且未处理过 → panel_handoff：状态 need_human，推流+手动模式，await 继续 → 记签名，回到 CHECK
            └ 否 → ANALYZE → EXECUTE（ActionExecutor._run → before_action → 动作 → STATUS.action_done）
      on_step → 推送任务树、时间线；should_stop → 用户停止
  → 结束：JobInfo.result（通过/失败、原因、报告路径）→ 释放锁
STATUS 订阅者（PanelService）→ 每 0.5 秒或有事件 → WS "state"
Screencaster → 帧 → WS 二进制
```

**设置修改（F8）**：`PUT /api/settings` → 校验 → 存入 `pending` → 下一个动作开始时 `before_action` → `apply_now` → 新参数写进 Humanizer.cfg 和 Config → 该动作的 action.json 记录新参数。

**真实轨迹库回放（F16）**：Humanizer.move_to → `cfg.mouse.source == "library"` → `Library.pick(direction, device, rng)` → 有：`transform` → Move(source=library, sample_id) ；无：`plan_move` → Move(fallback=True)。

**本地采集（F15）**：collect.js 出题 → 用户点起点、移动、点目标 → 前端打包 points → `POST /api/samples` → 服务端复核 → 写库 → 返回新的进度格。

**真实网站采集（F15）**：RealSampleJob → 注入隔离环境监听 + 绑定 → 挑目标（DomReader 可点元素，优先缺口方向）→ Overlay 高亮 → 绑定回传点 → 校验存库 → 下一题；结束 → 移除脚本、绑定、高亮。

**依赖方向（无环）**
```
panel.app → panel.server → panel.service → panel.{jobs, preview, inputs, settings, samples, history, profile}
panel.* → hands.*            hands.* → hands.status / hands.trajlib（不反向依赖 panel）
```

## 6. spec 覆盖

| 需求 | 落点 |
|---|---|
| F1 | app.py（单实例、窗口）、server.py（只本机）、__main__.py（开发地址） |
| F2 | service.py（启停、模式、崩溃看护、调到前台） |
| F3 | jobs.JobManager 锁 + 409 + 前端按钮状态 |
| F4 | preview.py、app.js 叠加层、STATUS.page_epoch |
| F5 | service.navigate/back/reload + 前端地址栏 |
| F6 | STATUS.wait + Monitor.snapshot |
| F7 | CaseJob on_step + STATUS.phase |
| F8 | settings.py + ActionExecutor.before_action |
| F9 | DemoJob（F12 夹具） |
| F10 | CaseJob + CaseRunner.should_stop |
| F11 | TargetJob + /api/elements |
| F12 | ManualJob + inputs.py |
| F13 | panel_handoff + runner 已处理签名 + captcha 标记 |
| F14 | history.py + /runs 静态映射 |
| F15 | collect.html/js、samples.py、RealSampleJob |
| F16 | trajlib.py + humanize 增量 |
| F17 | app.py、DATA_ROOT、PyInstaller spec |
| F18 | profile.py（profiles/ver0 本来就跨运行保留） |
| N1 | 鉴权中间件 | N2 | private_mode、导航拦截、独立 profile |
| N3 | 钩子默认 None、hands 不依赖 panel、每阶段跑回归 | N4 | 推流不改视口、注入仅限采集 |
| N5 | 最新帧策略、统计 | N6 | trajlib 用 rng、Move 记录 sample_id |
| N7 | 服务端是状态唯一来源，前端重连即恢复 | N8 | 标签 panel-*、human_handoffs |
| N9 | samples.json 格式 | N10 | 统一 CSS 像素、记录 dpr |
| N11 | 依赖清单 | N12 | 文案 + 不自动处理验证码 |
| N13 | 只经 hands/CDP；平台相关只在 app.py |

## 7. 文件组织

```
D:\work\Bing_browse\
├── pyproject.toml                    ★ 改：optional-dependencies 加 panel=[aiohttp, pywebview]、build=[pyinstaller]；packages 含 panel
├── config.yaml                       ★ 改：humanize.mouse 加 source/device/library_path（默认 template）
├── run_panel_tests.py                ☆ 新：面板验收表（P 系列）
├── packaging/panel.spec              ☆ 新：PyInstaller 配置
├── src/hands/
│   ├── status.py  trajlib.py         ☆ 新
│   ├── page.py actions.py humanize.py runner.py captcha.py config.py recorder.py   ★ 改（增量、可选参数）
├── src/panel/                        ☆ 新包
│   ├── __init__.py __main__.py app.py server.py service.py jobs.py preview.py
│   ├── inputs.py settings.py samples.py history.py profile.py
│   └── static/ index.html style.css app.js collect.html collect.js
├── fixtures/
│   ├── f13_turnstile/ pass.html interactive.html   ☆ 新：Cloudflare 官方测试密钥（需联网）
│   └── f14_cookie/ index.html                      ☆ 新：写入/读取 Cookie
├── cases/
│   ├── extra_turnstile.yaml          ☆ 新：真实验证框（总是通过）
│   └── panel_stop.yaml               ☆ 新：步数多、便于中途停止
├── tests/unit/
│   ├── test_trajlib.py test_settings.py test_samples.py test_status.py test_auth.py   ☆ 新
│   └── test_captcha.py               ★ 改：补真实验证框标记、签名
└── tests/panel/
    ├── common.py                     ☆ 新：起后台、REST/WS 客户端、UI 用第二个 Chrome
    └── p01_… pNN_…                   ☆ 新：对应 AC1–AC28 中的自动项
```

## 8. 风险与开发顺序

| 编号 | 风险 | 应对 |
|---|---|---|
| R1 | 轨迹模块（11-12）还没在 Windows 回归、没提交，面板改动会和它混在同一批未提交文件里 | AC0 先过；面板写回时附改动文件清单，建议先提交轨迹那批 |
| R2 | pywebview 是否能拿到 WebView2 的 `NavigationStarting` | 开发 F17 时先做 10 分钟验证；拿不到则用 CSP + 前端拦截兜底，并在验收时说明 |
| R3 | 无显卡机器推流达不到 10 帧 | 降质量/分辨率参数可调；达不到记入调试 |
| R4 | 云端开发环境是 Linux + Chrome 153，Windows 特有项（窗口、打包、缩放、PowerShell 进程查找）只能在用户电脑上验 | task.md 把这些单独列为"本机验收"任务 |
| R5 | 真实网站采集时点中链接会跳转，隔离环境随之销毁 | 用 `addScriptToEvaluateOnNewDocument` 自动在新页面重建；样本在点击时已回传 |
| R6 | reCAPTCHA / Turnstile 需要联网，结果受对方服务影响 | 只放在 AC15②、AC29，假验证码页保证离线回归 |

**开发顺序（task.md 将按此分阶段，每阶段末跑 ver0 回归）**
1. hands 增量（status、钩子、trajlib、captcha 签名）+ 单元测试
2. 面板后台骨架：鉴权、状态、浏览器控制、导航、预览推流
3. 前端主界面：预览、叠加、控制、网络、任务树
4. 四种使用方式：演示、用例、指定目标、手动 + 人工介入
5. 设置、历史、用户目录
6. 采集（本地页、真实网站）+ 真实轨迹库回放
7. Windows 窗口与打包
8. 全量验收
