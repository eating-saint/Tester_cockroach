# ver0 无头版"手脚" Plan

> 语言：Python 3.12，asyncio。依赖：websockets、httpx、pillow、pyyaml、pytest。

## 架构概览

```
run_tests / cli              一键验收入口、单用例入口
CaseRunner（控制层）          用例循环 CHECK→ANALYZE→EXECUTE→VERIFY→NEXT/FINISH；崩溃重跑、人工介入
  ├─ Decider（决策层）        RuleDecider；ver1 换 LLMDecider（同一接口）
  ├─ TaskTree                 母/子任务、深度和步数上限、上下文清理
  └─ RunRecorder              运行目录、每步记录、报告
PageState 构建（感知层）       DomReader + Monitor + Screenshot → PageState
Page / ActionExecutor（执行层） 导航、等待、点击/输入/按键/滚动/后退、标签页跟随、跨源 iframe 会话树
BrowserManager + CDP 客户端    启动/连接/关闭/看门狗；基于 websockets 的异步 CDP 客户端
Chrome 156（无头 / 有界面）
```

- **CDP 客户端**：一条浏览器级 websocket，后台读任务分发命令响应与事件；flat session 模式管理所有标签页和 iframe 会话。
- **BrowserManager**：Chrome 进程生命周期与看门狗，崩溃时让所有挂起操作以 BrowserCrashed 结束。
- **Page**：当前标签页会话 + 跨源 iframe 子会话树；导航与统一等待；新标签页跟随。
- **Monitor**：汇总所有会话的网络、console、生命周期事件。
- **DomReader**：逐 frame 的 DOMSnapshot + AX 树，合并成主页面坐标下的元素列表和正文。
- **ActionExecutor**：执行动作并以前后指纹判断是否生效。
- **Decider / RuleDecider**：PageState 进、决策 JSON 出。
- **TaskTree**：纯逻辑任务树，程序侧硬约束。
- **CaseRunner**：用例循环、崩溃重跑、验证码人工介入、结果判定。
- **RunRecorder**：运行目录与报告。

## 核心数据结构

### Config（config.yaml，缺项用默认值）
```
chrome_path: str                      # 默认自动查找 Program Files 下 chrome.exe
profile_dir: Path = profiles/ver0
headless: bool = True
window: (int,int) = (1280, 800)
quiet_ms: int = 500                   # 网络安静保持时长
nav_timeout_s: float = 30
cmd_timeout_s: float = 30             # 单条 CDP 命令超时
watchdog_interval_s: float = 0.5
max_crash_reruns: int = 2
min_interval_s: dict = {"bing.com": 20}   # 按域名的最小访问间隔
captcha_markers: list[str]            # 网址/文本/iframe 网址中出现即判为验证码
runs_dir: Path = runs/
```

### Box
`x, y, width, height`（CSS 像素，相对主页面视口）；`center()`、`contains(x, y)`。

### Element
```
id: str               # "e0","e1"…，PageState 内唯一，决策引用它
role: str             # AX 角色；AX 失败时由标签推断
name: str             # 可访问名称
text: str             # 元素内可见文本
tag: str
href: str | None
value: str | None     # 输入框当前值
box: Box
visible: bool         # 有布局 且 visibility≠hidden 且 宽高>0 且 不在页面左/上边界之外
in_viewport: bool
enabled: bool
frame_path: list[str] # 主页面到所在 frame 的 frameId 链；主页面为 []
in_shadow: bool
_ref: NodeRef         # 内部，不序列化：(session_id, backend_node_id)
```

### MonitorState
```
lifecycle: str                        # init / DOMContentLoaded / load / networkIdle
pending: list[RequestInfo]            # url, started_at, frame_id
failed: list[RequestInfo]             # url, status 或 error_text
console_errors: list[ConsoleEntry]    # source, level, text, url
```

### PageState
```
url, title: str
viewport: Box; device_pixel_ratio: float
elements: list[Element]
body_text: str
monitor: MonitorState
screenshot: str | None                # 截图相对路径
captured_at: float
```

### Decision
```json
{"decision": "call_tool", "reason": "...", "tool": "click", "arguments": {"element": "e12"}}
```
`validate_decision(raw: str) -> Decision`：校验 JSON 合法、`decision == "call_tool"`、`tool` 已注册、参数符合工具定义；否则抛 `DecisionInvalid`。

### TOOL_REGISTRY

| 类别 | 工具 | 参数 |
|---|---|---|
| 任务树 | create_child | goal, done_when |
| | complete | summary |
| | fail | reason |
| 动作 | click | element |
| | type | element, text |
| | press | key |
| | scroll | direction(up/down)+pixels 或 to_bottom: true |
| | back | — |
| 读取 | read_text | element?（缺省读正文）, contains?（按关键词筛段落） |
| | read_links | href_contains?, href_excludes?（逗号分隔多个）, title_min_len?（开发中新增：过滤导航短链接；同地址去重保留最长标题） |

### DecisionInput
```
page: PageState
task: TaskView     # goal, done_when, depth, steps_used, max_steps, child_summaries,
                   # recent_steps（本节点最近几步：工具、参数、结果，不含 PageState）
inputs: dict       # 用例预置输入（如搜索词）
```

### Decider（Protocol）
```
async def decide(self, inp: DecisionInput) -> str   # 返回原始 JSON 文本，由调用方校验
```

### ActionResult
```
ok: bool; effective: bool
changes: list[str]        # "url" / "dom" / "focus" / "new_tab"
wait: WaitResult          # status(ok/timeout), elapsed_ms, pending_urls
data: Any                 # read_text / read_links 的结果
error: str | None
```

### TaskNode / TaskTree / TreeEvent
```
TaskNode: id, parent_id, goal, done_when, status(running/done/failed), depth,
          steps_used, summary, child_summaries: list[{goal,status,summary}],
          context: list[StepRecord] | None     # 结束后置 None
TaskTree: max_depth, max_steps, nodes, active_id, events: list[TreeEvent]
          create_child() / complete() / fail() / tick_step() / view() -> TaskView / finished
TreeEvent: t, kind(create/complete/fail/force_fail), node_id, payload
```

### StepRecord
```
index, node_id, page_state_file, screenshot_file, decision_raw, decision,
retries, action_result, durations_ms: {check, decide, execute, total}
```

### CaseSpec / Rule / Expect / CaseResult
```
CaseSpec: name, start_url, inputs, limits{max_depth,max_steps}, root_goal, root_done_when,
          rules: list[Rule], default: Rule | None, expect: Expect, timeout_s
Rule:     when: dict（全部满足才命中）, do: {tool, arguments}
Expect:   root_status(done/failed), summary_contains: list[str], final_url_contains,
          tree_shape: list[str] | None, no_clicks: bool
CaseResult: name, passed, reason, crashes, steps, tree_events_file, duration_ms
```
规则条件：node_depth、goal_is、url_contains、url_not_contains、text_contains、element{role,name_contains,text_contains,href_contains,enabled}、no_element、has_child_summary、no_child_with_goal、last_tool、last_effective、read_count、steps_used（url_not_contains / steps_used / enabled 为开发中新增）。
参数模板：`{{inputs.xxx}}`、`{{match}}`（when.element 命中元素 id）、`{{last_read}}`。

## 模块设计

### cdp
**职责：** websocket 连接、命令/响应匹配、事件分发、会话。
**对外接口：** `CDPConnection.connect(ws_url)` / `close()`；`send(method, params, session_id=None, timeout) -> dict`（`CDPError` / `BrowserCrashed`）；`on(method, cb, session_id=None)` / `off()`；`wait_event(method, predicate, timeout)`；`fail_all(exc)`；`CDPSession(conn, session_id)` 自动携带 session_id。
**依赖：** websockets。

### browser（M1）
**职责：** 启动、连接、关闭、看门狗、重启。
**对外接口：** `BrowserManager(config)`：`start()`（拼参数 → 启进程 → 读 `DevToolsActivePort` → GET `/json/version` → 连接 → 启看门狗）；`stop()`（`Browser.close`，超时 `taskkill /T /F`）；`restart()`；`relaunch(headless)`；`is_alive()`；`crashed: asyncio.Event`；`new_page() -> Page`；`pid`。
看门狗每 `watchdog_interval_s` 检查 `proc.poll()` 与 websocket 状态，并监听 `Inspector.targetCrashed`；异常时置位 `crashed` 并 `conn.fail_all(BrowserCrashed)`。
**依赖：** cdp、httpx、subprocess。

### page（M2 + 标签页/frame 管理）
**职责：** 当前标签页会话与跨源 iframe 子会话树；导航与等待；新标签页跟随。
**对外接口：** `attach(target_id)`（启用 Page/Network/Runtime/Log/DOM、`setLifecycleEventsEnabled`、`Target.setAutoAttach{autoAttach, waitForDebuggerOnStart, flatten, filter: iframe}`）；`navigate(url)` / `back()` / `reload()` → WaitResult；`wait_settled(after_navigation, timeout)`；`sessions() -> list[FrameSession]`（session、frame_id、parent、owner_offset）；`adopt_new_tab(target_id)`。
跨源 iframe：收到 `Target.attachedToTarget` → 子会话启用 Network/Runtime/Log/DOM/DOMSnapshot → `Monitor.attach_session` → `Runtime.runIfWaitingForDebugger`；子会话内同样开启 setAutoAttach（递归）。偏移：父会话 `DOM.getFrameOwner` → `DOM.getBoxModel` 内容框左上角，逐层累加。
等待：导航 = load 事件 → 网络安静（pending 为 0 持续 quiet_ms）；动作后 = 仅网络安静；超时返回 timeout 与未完成请求地址。
back：有历史走 `Page.navigateToHistoryEntry`；无历史且有 opener 则关闭当前标签页并切回 opener。
新标签页：`Target.setDiscoverTargets`，监听 `Target.targetCreated` 中 `openerId == 当前标签页`。
**依赖：** cdp、monitor、geometry。

### monitor（M4）
**职责：** 订阅所有会话的事件，维护 MonitorState。
**对外接口：** `attach_session(session)`；`snapshot() -> MonitorState`；`pending_count()`；`quiet_since()`；`reset_for_navigation()`。
事件：`Network.requestWillBeSent / responseReceived(status≥400 记失败) / loadingFinished / loadingFailed`；`Runtime.consoleAPICalled(error) / exceptionThrown`；`Log.entryAdded(error)`；`Page.lifecycleEvent`（主 frame）。WebSocket、EventSource 不计入 pending。
**依赖：** cdp。

### geometry
**职责：** 纯函数：Box 运算、frame 偏移累加、`css_to_image(box, dpr)`、相交判断。
**依赖：** 无。

### dom（M3）
**职责：** 从所有 frame 提取元素与正文。
**对外接口：** `DomReader.read(page) -> (list[Element], body_text)`。
算法：① 每个 FrameSession 调 `DOMSnapshot.captureSnapshot`（computedStyles: display/visibility/opacity，含布局框），同进程 iframe 与 Shadow DOM 已含其中；② 每个 frame 调 `Accessibility.getFullAXTree`，按 backendNodeId 关联角色/名称/disabled，失败则退回标签推断；③ 交互判定：AX 交互角色，或 `a[href] / button / input(非 hidden) / select / textarea`；④ 位置框 = 快照布局 − 滚动偏移 + frame 累计偏移；⑤ 正文按文档顺序拼接布局文本节点。
**依赖：** page、geometry。

### screenshot（M6）
**对外接口：** `capture(page, full_page=False) -> bytes`（`Page.captureScreenshot`；整页用 captureBeyondViewport + `Page.getLayoutMetrics.cssContentSize` 作为 clip）；`annotate(png, elements, dpr) -> bytes`（PIL 画框和编号）。
**依赖：** page、geometry、pillow。

### pagestate（M5）
**对外接口：** `build_page_state(page, save_dir=None) -> PageState`；`PageState.to_json()`。
**依赖：** dom、monitor、screenshot。

### actions（M7）
**职责：** 执行动作并判断是否生效。
**对外接口：** `ActionExecutor(page)`：`click(el)`、`type(el, text)`、`press(key)`、`scroll(...)`、`back()`、`read_text(...)`、`read_links(...)` → ActionResult。
流程：取前指纹（url、元素+正文哈希、焦点节点）→ 执行 → `wait_settled(after_navigation=url 变化)` → 取后指纹 → `effective`、`changes`。
点击/输入定位：按 `_ref` 找会话 → 逐层 `DOM.scrollIntoViewIfNeeded`（先 iframe 元素再目标）→ `DOM.getContentQuads` + 偏移 → 主页面会话发 `Input.dispatchMouseEvent`（mouseMoved/Pressed/Released，中心点）。输入：点击聚焦后 `Input.insertText`；按键：`Input.dispatchKeyEvent`。
**依赖：** page、dom、pagestate、geometry。

### decision
**职责：** TOOL_REGISTRY、Decision、`validate_decision`、Decider 协议、DecisionInput。
**依赖：** 无业务依赖。

### rule_decider（M8）
**职责：** RuleDecider(Decider)：按序匹配 `case.rules`，首条命中者渲染 `do` 模板输出 JSON；无命中用 `default`；都没有输出 `fail("no rule matched")`。
**依赖：** decision、pagestate（数据结构）。

### tasktree（M9）
**职责：** 纯逻辑任务树。只有活动节点接收步骤；`create_child` 设新活动节点，超深度返回错误不建节点；`complete/fail` 结束活动节点、context 置 None、摘要追加到父节点、父节点重新活动；`tick_step` 达上限触发 `force_fail`；根节点结束则 `finished`；每次变化追加 TreeEvent。
**依赖：** 无。

### captcha（F12）
**对外接口：** `detect(state, markers) -> str | None`（检查网址、正文、iframe 元素网址/名称）；`handoff(browser, page, url, input_fn=input)`：保存截图 → `relaunch(headless=False)` 打开同一网址 → 命令行提示并等回车 → `relaunch(headless=True)` 打开同一网址 → 返回继续当前步。
**依赖：** browser、page、screenshot。

### recorder（M10）
**对外接口：** `RunRecorder(runs_dir)` 创建 `runs/<时间戳>-<随机后缀>/`；`case_dir(name)`；`save_step(StepRecord, page_state, png)`；`save_tree(events)`；`save_case_result()`；`write_report(results)` 输出 report.json + 简单 report.html（汇总表，每用例每步决策/结果/截图链接/耗时）。
**依赖：** pagestate、tasktree（数据结构）。

### runner（F11 + 控制循环）
**对外接口：** `load_case(path) -> CaseSpec`；`CaseRunner(browser, decider_factory, recorder, config)`：`run_case(case) -> CaseResult`、`run_cases(paths)`；测试钩子 `on_step(i)`。按域名执行 `min_interval_s`（距上次访问同域名不足则等待到间隔满足）。
**依赖：** browser、page、pagestate、actions、decision、rule_decider、tasktree、captcha、recorder。

## 模块交互

```
run_case:
  crashes = 0
  loop:                                        # 崩溃从头重跑
    try:
      page.navigate(start_url); tree = TaskTree(root_goal, limits)
      while not tree.finished and 未超时:
        CHECK   state = build_page_state(page)               (dom + monitor + screenshot)
                if captcha.detect(state): captcha.handoff(...); continue
        ANALYZE raw = decider.decide(DecisionInput(state, tree.view(), inputs))
                decision = validate_decision(raw)            不合格重问，最多 3 次；仍不合格 → fail
        EXECUTE 任务树工具 → tree.create_child/complete/fail
                动作/读取工具 → ActionExecutor.xxx(...)      (page → cdp)
        VERIFY  ActionResult.effective 写入 StepRecord；tree.tick_step()
                recorder.save_step(...)
      NEXT/FINISH: 按 Expect 判定，保存结果、任务树事件
      return CaseResult
    except BrowserCrashed:
      crashes += 1; 超过 max_crash_reruns → 判失败；browser.restart(); page = browser.new_page()
```

依赖方向（无环）：
```
runner → {rule_decider, tasktree, recorder, captcha, actions, pagestate, browser}
captcha → {browser, page, screenshot}
actions, pagestate → {dom, monitor, screenshot, page}
dom, page, monitor → cdp ；dom, page, screenshot → geometry
rule_decider → decision
```

spec 覆盖：F1→browser；F2→page；F3→dom、geometry、page（跨源会话）；F4→monitor；F5→pagestate；F6→screenshot；F7→actions、page；F8→decision、rule_decider；F9→tasktree；F10→recorder；F11→runner、browser；F12→captcha、browser.relaunch；F13→fixtures、run_tests。

## 文件组织

```
D:\work\Bing_browse\
├── pyproject.toml               最小打包配置，pip install -e . 安装 src/hands
├── config.yaml                  全局配置
├── run_tests.py                 一键验收：python run_tests.py [--only T03,T05] [--rounds 50] [--headed]
├── docs/ver0/                   spec / plan / task / checklist
├── src/hands/
│   ├── __init__.py
│   ├── __main__.py              python -m hands run cases/xxx.yaml [--headed]
│   ├── config.py                Config、load_config
│   ├── cdp.py                   CDPConnection、CDPSession、CDPError、BrowserCrashed
│   ├── browser.py               BrowserManager
│   ├── page.py                  Page、FrameSession、WaitResult
│   ├── monitor.py               Monitor、MonitorState、RequestInfo、ConsoleEntry
│   ├── geometry.py              Box、坐标换算
│   ├── dom.py                   DomReader、Element、NodeRef
│   ├── screenshot.py            capture、annotate
│   ├── pagestate.py             PageState、build_page_state
│   ├── actions.py               ActionExecutor、ActionResult
│   ├── decision.py              TOOL_REGISTRY、Decision、validate_decision、Decider、DecisionInput、DecisionInvalid
│   ├── rule_decider.py          RuleDecider、Rule、条件匹配、模板渲染
│   ├── tasktree.py              TaskTree、TaskNode、TaskView、TreeEvent
│   ├── captcha.py               detect、handoff
│   ├── recorder.py              RunRecorder、StepRecord、报告
│   └── runner.py                CaseSpec、Expect、CaseResult、load_case、CaseRunner
├── fixtures/
│   ├── server.py                同时监听 127.0.0.1 与 ::1（localhost 制造跨源）；/api/delay?ms=&data=、/missing → 404
│   ├── f01_static/  index.html expected.json        5 按钮 / 3 输入框 / 8 链接
│   ├── f02_hidden/                                  display:none、visibility:hidden、disabled、移出屏幕
│   ├── f03_dynamic/                                 打开即请求 /api/delay?ms=2000，返回后渲染列表
│   ├── f04_slow/                                    /api/delay?ms=8000
│   ├── f05_iframe/  index.html same.html cross.html  同源 iframe + 跨源 iframe（localhost）
│   ├── f06_shadow/                                  Web Component 内按钮
│   ├── f07_form/                                    提交后显示"你输入了：xxx"
│   ├── f08_lazy/                                    滚到底请求第二页
│   ├── f09_fault/                                   404 资源 + console.error("FIXTURE_ERROR_42")
│   ├── f10_search/  with_reddit.html no_reddit.html reddit.html  reddit 页 5 条评论预览
│   ├── f11_nav/                                     同标签页跳转 + target=_blank
│   └── extra/gate.html                           假验证码页，仅验证人工介入流程
├── cases/
│   ├── t02_crash.yaml  t13_reddit_yes.yaml  t13_reddit_no.yaml
│   ├── t14_unreachable.yaml  t16_report.yaml  t20_bing.yaml
├── tests/
│   ├── unit/        test_geometry.py test_decision.py test_rule_decider.py test_tasktree.py test_captcha.py
│   └── acceptance/  common.py  t01_launch.py … t17_stability.py（每个 async run(ctx) -> (passed, reason)）
├── profiles/ver0/               专用用户目录（自动创建）
└── runs/                        每次运行一个子目录
```

## 开发中的设计补充

- `Page.release()`：切换标签页前真正断开旧 CDP 会话（`Target.detachFromTarget`）。旧会话若仍挂着，浏览器会把后续 iframe 自动接入交给旧会话。
- `Page.wait_for(getter, predicate, timeout)`：无事件可订阅时的条件等待（目前只用于等待滚动位置变化）。
- Monitor 只按 requestId 记账（跨源 iframe 文档请求在父会话发出、在子会话结束）；主页面导航前清空等待中请求；子 frame 接入时结束其在父会话的文档请求。
- 启动参数加 `--disable-features=BackForwardCache`，保证后退总是触发 load。
- 验证码人工介入的 `input_fn` 可为协程（测试中模拟人工操作）；假验证码页为 `fixtures/extra/gate.html`（网址不能含 "captcha"，否则解决后仍被网址规则命中）。
- 验证码正文检测只匹配短语类标记（含空格或非 ASCII），避免 "challenge" 等普通单词误报。
- （有界面 Bing 演示后补充）"网络安静"的计时至少从本次等待开始算：按回车/点击触发的跳转要稍后才发出请求，原先会 0ms 就判定稳定、在结果出来前读页面。
- Monitor 忽略 `Ping` 类型请求（sendBeacon、`<a ping>`），它们常收不到结束事件。
- load 之后才发出、挂满 `LONG_POLL_S`=3 秒仍未结束的非文档请求视为后台长连接（Bing 的 rewards XHR 等），不再阻塞"网络安静"；load 之前发出的请求仍必须结束（F4 慢请求照常超时）。
- 有界面启动加 `--disable-backgrounding-occluded-windows` 等参数，窗口被挡住时照常绘制。
- 用例判定新增可选 `summary_count_min`；T20 要求至少 5 条 `/ck/a` 结果链接，防止只读到页脚链接也判通过。

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 驱动方式 | 自写原生 CDP（websockets） | 用户已定；确定性、无隐式等待，可复用到 Edge / WebView |
| 并发模型 | 单个 asyncio 事件循环，后台读任务分发 | CDP 天然异步，事件与命令共用连接 |
| 连接 | 浏览器级 websocket + `Target.attachToTarget(flatten)` | 一条连接管所有标签页和 iframe，崩溃只监控一个点 |
| 调试端口 | `--remote-debugging-port=0` + 读 `DevToolsActivePort` | 避免端口冲突，T01 连续启动不撞残留端口 |
| 无头 | `--headless`、`--window-size=1280,800`；有界面去掉 `--headless` | 两种模式视口一致 |
| 元素提取 | 每个 frame 会话 DOMSnapshot + 每个 frame 一次 AX，按 backendNodeId 关联 | snapshot 稳定、一次调用；AX 是实验接口，只补角色名称，失败可退回 |
| 跨源 iframe | `Target.setAutoAttach(waitForDebuggerOnStart, flatten, filter iframe)` 递归拿子会话；`getFrameOwner`+`getBoxModel` 累加偏移；Monitor 汇总所有会话 | 用户要求必须实现；等待调试器保证不漏早期请求和报错 |
| 跨源夹具 | 127.0.0.1 主页面嵌 localhost iframe；夹具服务同时监听 IPv4/IPv6 | 站点以域名划分、不看端口；localhost 在 Windows 可能先解析 ::1 |
| 坐标 | 全系统 CSS 像素、相对主页面视口；截图像素 = CSS × DPR | 符合 N4；T05/T08 以 getBoundingClientRect 验证 |
| 点击 | `scrollIntoViewIfNeeded` → `getContentQuads` 取最新位置 → 主页面会话发鼠标事件 | 不用可能过期的 PageState 位置框 |
| 输入 | 文本 `Input.insertText`；按键 `Input.dispatchKeyEvent` | 简单确定，支持中文 |
| 等待 | 导航：load → 网络安静(quiet_ms) → 总超时；动作后：网络安静 | 符合 F2，无固定 sleep |
| F3 动态加载 | 页面打开即请求，服务器延迟 2 秒返回 | 用户已定；setTimeout 后再请求会让"网络安静"提前成立 |
| 动作生效 | 前后指纹（url、元素+正文哈希、焦点）对比 | 符合 F7 |
| 崩溃检测 | 看门狗 0.5 秒轮询进程与连接 + `Inspector.targetCrashed`；`fail_all(BrowserCrashed)` | 5 秒内检测；挂起操作不卡死 |
| 崩溃后 | 当前用例从头重跑，上限 `max_crash_reruns` | 用户已定 |
| 关闭 | `Browser.close` → 超时 `taskkill /T /F` | 保证无残留进程 |
| 人工介入 | 同一 profile 关闭无头 → 有界面打开同网址 → 回车 → 回到无头继续 | 用户已定；同一 profile 不能被两个实例同时使用 |
| 夹具服务 | 标准库 `ThreadingHTTPServer` 自写 | `http.server` 不支持延迟与 404 路由 |
| 测试分层 | pytest 单元测试纯逻辑；`run_tests.py` 跑 T01–T17 输出表 | 验收表格式可控；逻辑错误在单元层定位 |
| 进程/内存测量 | ctypes `GetProcessMemoryInfo`；PowerShell `Get-CimInstance` 按 profile 路径筛 Chrome 进程 | 不新增依赖，符合 N9 |
| 决策重问 | 校验失败最多重问 3 次 | spec F8；规则决策器虽确定，循环为 ver1 预留并做单元测试 |
| T20 | 不进自动表，`python -m hands run cases/t20_bing.yaml` + 人工核对 | 外网结果不稳定，spec 定为人工验收 |
