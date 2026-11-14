# Playwright 驱动方案 Plan

对应 spec：`docs/playwright/spec.md`（已批准，F1–F10、N1–N4、AC1–AC7）。

## 架构概览

```
            runner / captcha / rule_decider / recorder / panel(jobs, service)
                               │  只用"浏览器接口"和"页面接口"
                 ┌─────────────┴──────────────┐
          actions.py（动作执行，驱动无关）   pagestate.py / screenshot.py
                 │  调页面接口的底层原语（鼠标、键盘、定位、视口……）
     ┌───────────┴────────────┐
 CDP 驱动（现有）              Playwright 驱动（新增）
 browser.py  BrowserManager    pw/browser.py  PwBrowserManager
 page.py     Page              pw/page.py     PwPage
 dom.py      DomReader         pw/dom.py      PwDomReader
 monitor.py  Monitor ◄─────── 共用"请求账本"，各自把事件喂进来
```

- **驱动工厂**（`hands/driver.py`）：按 `config.driver` 返回 `BrowserManager` 或 `PwBrowserManager`；选 Playwright 而未安装时抛出带中文提示的异常。
- **两套驱动实现同一组公开方法**（鸭子类型，`driver.py` 里用 `Protocol` 写成文档并供类型检查），上层不判断驱动类型。
- **现有 CDP 代码留在原位**，不搬目录（减少改动、保住 N1）；只把 `actions.py`、`pagestate.py`、`screenshot.py`、`humanize.py` 里直接发 CDP 命令的几行，挪进 `Page` 的新方法里。
- **共用的逻辑不复制**：等待"网络安静"的算法抽成一个函数，两个页面类都调它；请求记账（`Monitor`）拆出与事件格式无关的记账方法，CDP 和 Playwright 各写一层薄的事件转换。

## 核心数据结构

### Config（修改）
新增字段 `driver: str = "cdp"`（取值 `cdp` / `playwright`），来自 `config.yaml` 的 `driver`，命令行 `--driver` 覆盖。

### Element（不变）
`_ref` 改为"不透明引用"：CDP 驱动仍是 `NodeRef(session_id, backend_node_id)`；Playwright 驱动是 `PwRef(frame_key: str, aria_ref: str)`。上层只做相等比较，不读内部字段。

### WaitResult、MonitorState、PageState（不变）

### 浏览器接口 BrowserLike（两套都实现）
| 成员 | 说明 |
|---|---|
| `config`、`headless`、`page` | 同现有 |
| `crashed: asyncio.Event`、`crash_history`、`crash_reason`、`crash_detected_at` | 同现有 |
| `pid -> int | None` | Chrome 主进程号（Playwright 版从用户目录的进程列表里取不带 `--type=` 的那个） |
| `driver -> str` | `"cdp"` / `"playwright"` |
| `start()`、`stop()`、`restart()`、`relaunch(headless)`、`new_page()`、`is_alive()` | 同现有 |

### 页面接口 PageLike（两套都实现）
现有方法（签名不变）：`navigate`、`reload`、`back`、`wait_settled`、`take_new_tab`、`adopt_new_tab`、`wait_for`、`evaluate(expr)`、`url`、`title`、`monitor`、`refresh_offsets`（Playwright 版为空操作）。

新增底层原语（CDP 版把 actions 等处现有代码原样搬进来）：
| 方法 | 用途 |
|---|---|
| `read_dom() -> (list[Element], str)` | 元素列表 + 正文；`DomReader().read(page)` 对 Playwright 页面转调它，测试和面板代码不用改 |
| `layout() -> dict` | `{vw, vh, scroll_x, scroll_y, content_w, content_h}`，CSS 像素 |
| `locate_box(el) -> Box` | 滚到可见后取元素在主页面视口中的框 |
| `node_value(el) -> str | None` | 读输入框当前值 |
| `mouse_move(x, y)`、`mouse_click(x, y)`、`mouse_wheel(x, y, dy)` | 鼠标 |
| `key_press(name)`、`select_all()`、`insert_text(text)` | 键盘（`name` 用现有 `KEYS` 表里的名字） |
| `screenshot(full_page=False) -> bytes` | PNG |
| `bring_to_front()` | 调到前台 |
| `frame_info(el) -> (offset, cross_process: bool)`、`evaluate_in_frame(el, expr)` | 给 T08 这类验收脚本用，替代直接访问 `sessions()` / `frame_of()` |
| `panel_session()` | **仅面板用**：返回一个带 `send / on / off / session_id` 的 CDP 会话（CDP 版就是 `self.session`；Playwright 版是 `context.new_cdp_session(page)` 的包装），供推流、预览手动输入、布局度量使用 |

## 模块设计

### driver.py（新建）
**职责：** `create_browser(config)` 工厂；`DriverUnavailable` 异常（消息含 `pip install -e .[pw]` 和 `playwright` 无需另装浏览器的说明）；`BrowserLike` / `PageLike` 协议。
**依赖：** browser.py；按需延迟导入 `pw`（未装 Playwright 时导入 hands 不报错，N1）。

### monitor.py（修改，行为不变）
把现有 `_on_request / _on_response / _on_finished / _on_failed / _on_lifecycle` 拆成两层：
- 记账层（驱动无关）：`req_started(key, url, rtype, frame_id, owner)`、`req_response(key, status, url, frame_id)`、`req_finished(key)`、`req_failed(key, error_text)`、`console_error(entry)`、`set_lifecycle(name)`。长连接 3 秒规则、`blob:`/`data:` 忽略、redirect、`IGNORED_TYPES` 全在这一层。
- CDP 事件层：现有 `attach_session` 订阅不变，回调解析参数后调记账层。

### page.py（修改）
- 把 `_wait_settled` 的算法抽成模块级 `wait_quiet(monitor, loaded: asyncio.Event, config, timeout, setup_tasks=())`；`Page.wait_settled` 调它。
- 新增上表的底层原语，内容从 actions.py / pagestate.py / screenshot.py / humanize.py 搬来，CDP 命令不变。

### actions.py / pagestate.py / screenshot.py / humanize.py（修改，行为不变）
- actions 里所有 `self.page.session.send(...)` 改为调页面原语；`_locate_box`、`_node_value`、`_viewport`、`_scroll_y` 变成一行转调。
- `Humanizer.move_to(session, ...)` 改为 `move_to(mouse_move, ...)`，`mouse_move` 是 `async (x, y)`；轨迹节奏代码不动。
- `build_page_state` 用 `page.layout()`；`screenshot.capture(page)` 转调 `page.screenshot()`。

### pw/browser.py（新建）PwBrowserManager
- `start()`：杀掉占用用户目录的残留 Chrome（复用 `profile_chrome_pids` / `kill_tree`）→ `async_playwright().start()` → `chromium.launch_persistent_context(profile_dir, executable_path=config.chrome_path, headless=…, no_viewport=True, args=[窗口尺寸, 与 CDP 版相同的 --disable-* 参数])`。不改 Playwright 默认参数（保留自动化标记，N4）。
- 崩溃：`context.on("close")`（非主动停止时）和 `page.on("crash")` → `_mark_crashed`，并让进行中的调用抛 `BrowserCrashed`；看门狗每 0.5 秒检查 `pid` 是否存活（T02 要求 5 秒内检测到）。
- `stop()`：`context.close()` → `playwright.stop()` → 清理残留进程。
- `relaunch(headless)`：stop + 改 headless + start（验证码转人工复用 captcha.py 现有流程）。
- `new_page()`：取 `context.pages[0]` 或新开，包成 `PwPage`。

### pw/page.py（新建）PwPage
- 事件接线：`request / response / requestfinished / requestfailed` → Monitor 记账层（key 用 Request 对象本身；redirect 时 `redirected_from` 的旧 key 结束；资源类型按 CDP 名映射：`document→Document`、`eventsource→EventSource`、`websocket→WebSocket`、`ping→Ping`、`other→Other`）；`console`（error/assert）、`pageerror` → console 记录；`domcontentloaded`、`load` → lifecycle 与 `_loaded`；主 frame 的导航请求开始时清 `_loaded`（等价于 CDP 的 `frameStartedLoading`）。
- `navigate`：`monitor.reset_for_navigation()`、清 `_loaded`，`page.goto(url, wait_until="commit", timeout=剩余时间)`，再走共用 `wait_quiet`；goto 自身超时则返回 `WaitResult("timeout", pending_urls)`（T07）。`reload`、`back`（`go_back(wait_until="commit")`，无历史时回到打开者标签页，逻辑同 CDP 版）同理。
- 新标签页：`context.on("page")` 里 `await p.opener()` 是当前页就入队；`adopt_new_tab` 换绑事件到新页。
- 原语：`mouse_*` → `page.mouse.move/down/up/wheel`；`key_press` → `page.keyboard.press`；`select_all` → `keyboard.press("Control+A")`；`insert_text` → `keyboard.insert_text`；`screenshot` → `page.screenshot`；`layout` → 一次 `evaluate`（`documentElement.clientWidth/Height`、`scrollX/Y`、`scrollWidth/Height`）；`locate_box` → `locator("aria-ref=…").scroll_into_view_if_needed()` 后 `bounding_box()`（Playwright 给的就是主页面坐标，含跨源 iframe）；`node_value` → `locator.evaluate`。
- 引用失效：`locate_box` 找不到 ref 时重读一次元素列表，按（角色、名字、frame）匹配同一元素，仍找不到抛 `LookupError`（动作返回失败，与 CDP 版一致）。
- 所有 Playwright 异常在此转换：浏览器已崩溃 → `BrowserCrashed`；其他 → `CDPError`（沿用现有异常类型，actions 的捕获逻辑不改）。
- `panel_session()`：懒创建 `context.new_cdp_session(page)`，换页时作废重建。

### pw/dom.py（新建）PwDomReader
1. 封闭 Shadow DOM：context 级 `add_init_script`，把 `attachShadow` 的 `mode` 强制为 `open`（已在临时用户目录实测：封闭影子按钮出现在快照中）。
2. `page.aria_snapshot(mode="ai", boxes=True)` 得到整页（含同源、跨源 iframe）的无障碍树；解析每行的角色、名字、`[ref=…]`、`[disabled]`。
3. 取角色属于 `INTERACTIVE_ROLES` 或 `iframe` 的条目作为元素。
4. 对这些 ref 并发执行 `locator("aria-ref=…").evaluate(EXTRA_JS)`，一次拿到：标签名、绝对 href、value、disabled、`getBoundingClientRect`（frame 内坐标）、是否在 Shadow DOM 中、text（前 300 字）、context（把 `_Doc.context_of` 的同一算法移植成 JS）。实测 400 个 ref 约 0.3 秒。
5. 坐标换算：每个 frame 只算一次偏移 = `frame_element().bounding_box()` + 边框与内边距（实测快照里 iframe 内的坐标是相对 iframe 的）；多层 iframe 逐层累加。`visible` 按 CDP 版同一规则（宽高 > 0 且不在页面左上方之外）；快照不含的隐藏元素不出现在列表中（T04 允许）。
6. 正文：各 frame `document.body.innerText` 依 frame 顺序拼接。
7. `id` 依序编号 `e0, e1, …`，与 CDP 版一致。

### dom.py（修改）
`DomReader.read(page)` 开头：若 `page.driver != "cdp"`，`return await page.read_dom()`。

### __main__.py、config.py（修改）
`--driver {cdp,playwright}`；`load_config(driver=...)`；`config.yaml` 加注释说明的 `driver: cdp`。

### panel（修改）
- `service.py`：三处 `BrowserManager(self.config)` → `create_browser(self.config)`；新增 `set_driver(name)`：有任务时拒绝（沿用 `guarded` 的 409），否则停浏览器、改 `config.driver`、启动新浏览器；Playwright 版跳过窗口摆放（`Browser.setWindowBounds`），调到前台用 `page.bring_to_front()`；`browser_state()` 增加 `driver` 字段。
- `preview.py`：`b.conn.on(..., sid)` 改为 `sess.on / sess.off`，会话来自 `page.panel_session()`。
- `server.py`：手动输入转发用 `page.panel_session()`；新增 `POST /api/browser/driver`。
- `jobs.py`：布局度量改用 `page.layout()`；真实网站采集在 Playwright 驱动下直接返回"此驱动不支持真实网站采集"。
- `settings.py`：`driver` 作为可保存为默认值的设置项（写回 config.yaml）。
- 前端：浏览器区域加"驱动"下拉（CDP / Playwright），任务运行时禁用；状态栏显示当前驱动。

### 验收脚本（修改）
- `run_tests.py`、`run_panel_tests.py`：加 `--driver`；`tests/acceptance/common.py` 用 `create_browser`。
- T01：Playwright 驱动下不检查 `/json/version`（没有调试端口），改为检查页面可用（`evaluate("1+1")`）且关闭后无残留进程；CDP 驱动下逻辑不变。
- T08：改用 `frame_info` / `evaluate_in_frame`，两种驱动共用。
- 新增夹具 F15（封闭 Shadow DOM 按钮）和验收 T22（读到并点中，`in_shadow` 为真），两种驱动都跑。
- `tools/bench_drivers.py`：性能对比表（F10），结果写 `docs/playwright/perf.md`。

## 模块交互

```
python -m hands run case.yaml --driver playwright
  → load_config(driver) → create_browser(config) → PwBrowserManager.start()
  → CaseRunner(browser) → browser.new_page() → PwPage
  → 每步：build_page_state(page) → DomReader().read(page) → PwPage.read_dom() → PwDomReader
         → 决策器 → ActionExecutor.click(el)
              → page.locate_box(el) → humanizer.move_to(page.mouse_move) → page.mouse_click
              → page.wait_settled() → wait_quiet(monitor, …)   ← Playwright 网络事件喂给 Monitor
  → 崩溃：context close / 看门狗 → BrowserCrashed → runner 重启浏览器、用例从头重跑（现有逻辑）
  → 验证码：captcha.handoff(browser) → browser.relaunch(headless=False) … （现有逻辑）

面板：service.set_driver → stop → create_browser → start；preview / inputs → page.panel_session()
```

## 文件组织

```
src/hands/
├── driver.py          新建：create_browser、DriverUnavailable、BrowserLike/PageLike 协议
├── pw/
│   ├── __init__.py    新建
│   ├── browser.py     新建：PwBrowserManager
│   ├── page.py        新建：PwPage、PwRef、Playwright 事件 → Monitor
│   ├── dom.py         新建：PwDomReader（aria 快照 + 补充字段 JS + 封闭 Shadow 初始化脚本）
│   └── session.py     新建：Playwright CDPSession 包装（面板专用）
├── monitor.py         改：拆记账层 / CDP 事件层
├── page.py            改：wait_quiet 抽出；新增底层原语
├── actions.py         改：改调页面原语
├── pagestate.py       改：page.layout()
├── screenshot.py      改：page.screenshot()
├── humanize.py        改：move_to 接 mouse_move
├── dom.py             改：Playwright 页面转调 read_dom
├── config.py          改：driver 字段
└── __main__.py        改：--driver
src/panel/  service.py、preview.py、server.py、jobs.py、settings.py、static/(index.html, app.js)  改
fixtures/f15_closed_shadow/  新建：index.html、expected.json
tests/acceptance/  common.py、t01_launch.py、t08_frames.py 改；t22_closed_shadow.py 新建
tests/unit/  test_monitor_ledger.py、test_pw_dom_parse.py、test_driver_factory.py 新建
run_tests.py、run_panel_tests.py  改：--driver
tools/bench_drivers.py  新建
config.yaml、pyproject.toml（optional: pw = ["playwright>=1.63"]）  改
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 抽象方式 | 两套类实现同一组方法（鸭子类型 + Protocol 文档），CDP 代码留原位 | 上层零判断；不搬文件，CDP 行为与现在逐字节一致（N1） |
| 元素角色 / 名字 | Playwright `aria_snapshot(mode="ai", boxes=True)` | 原生接口直接给角色、名字、ref；不必自写可访问名字计算（已实测） |
| 补充字段与精确坐标 | 每个 ref 并发 `locator.evaluate` 一次 | 快照坐标是取整的、且 iframe 内为相对坐标；实测 400 个 ref 约 0.3 秒 |
| iframe 坐标 | frame 元素框 + 边框内边距，逐层累加；点击定位直接用 `locator.bounding_box()` | 实测 bounding_box 已是主页面坐标，跨源 iframe 也正确 |
| 封闭 Shadow DOM | `add_init_script` 强制 `attachShadow` 为 open | 用户要求必须看到；已实测有效；用户接受注入 |
| 网络等待 | 共用 `wait_quiet` 算法 + 共用 Monitor 记账层，只重写事件转换 | 规则一处维护，T06/T07 行为天然一致；不用 Playwright 的 `networkidle`（不给未完成请求列表、规则不同） |
| 导航 | `goto/go_back/reload(wait_until="commit")` 后走自有等待 | 让超时判定与未完成请求列表由自有逻辑给出 |
| 预览、手动输入 | 面板经 `context.new_cdp_session(page)` 复用现有推流代码 | 用户选最轻量；以后整体换远程推流 |
| 窗口摆放 | Playwright 驱动下跳过 | spec"不做的事" |
| 焦点模拟 | 不另加 | Playwright 内部对 Chromium 页面已开启（以 T10/T21 滚动结果验证）；不生效则按 spec 记为已知差异 |
| 异常类型 | Playwright 异常转为现有 `CDPError` / `BrowserCrashed` | runner、actions 的错误处理不改 |
| 依赖 | `pw = ["playwright>=1.63"]` 可选依赖；用本机 Chrome，不执行 `playwright install` | N1；对比公平 |
| 浏览器启动参数 | 沿用 CDP 版的 `--disable-features=BackForwardCache,…` 等，追加在 Playwright 默认参数之后 | 后退必触发 load 等现有等待前提；若与 Playwright 自带的 `--disable-features` 冲突（后者覆盖前者），合并成一条（以 T12 后退验证） |
